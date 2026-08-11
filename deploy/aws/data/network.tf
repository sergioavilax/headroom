# The VPC the whole stack sits in — a data-layer resource because RDS lives in it and
# RDS outlives compute.
#
# ── There is no NAT gateway, and that is a decision ────────────────────────────
# A NAT gateway is $0.045/hr — $1.08 a day, forty percent of this stack's entire daily
# cost — to give private subnets a route to the internet. Two things remove the need:
#
#   * The Fargate tasks run in the **public** subnets with a public IP and no inbound
#     rule except from the ALB's security group. A public IP with a security group that
#     admits one source is not an exposed host; it is a host with an egress path. That is
#     how they reach Anthropic, ECR, and CloudWatch Logs for nothing.
#   * DynamoDB is reached through a **gateway endpoint**, which is free, so the token
#     buckets and the budget items never leave the VPC at all.
#
# RDS and the rollup Lambda stay in the private subnets, which have no default route.
# The Lambda's one AWS call — reading the database URL out of Secrets Manager — goes
# through an interface endpoint that `compute` creates and destroys with itself, so the
# ~$0.48/day it costs is not charged while only the data layer is standing.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  # Two AZs, because a DB subnet group requires two and an ALB requires two. Not three:
  # the third buys availability this stack is not claiming and costs a subnet nobody
  # reads.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.project }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = var.project }
}

# --- subnets ---------------------------------------------------------------------

resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.project}-public-${local.azs[count.index]}" }
}

resource "aws_subnet" "private" {
  count = length(local.azs)

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 8)
  availability_zone = local.azs[count.index]

  tags = { Name = "${var.project}-private-${local.azs[count.index]}" }
}

# --- routing ---------------------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.project}-public" }
}

# No default route, on purpose. Anything in here can reach the VPC and the gateway
# endpoints below, and nothing else — which is the whole of the argument for RDS and the
# Lambda living here.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${var.project}-private" }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# A gateway endpoint is a route-table entry, not an ENI: no hourly charge, no data
# processing charge, no availability zone to choose. It is on both route tables so the
# answer to "where does a conditional write go" is the same from a task and from
# anything a later phase puts in a private subnet.
resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id, aws_route_table.private.id]

  tags = { Name = "${var.project}-dynamodb" }
}

# --- security groups -------------------------------------------------------------
#
# Two groups, both owned here, and the reason is the destroy story. `sg_workload` is a
# *membership* — "this thing may reach the database" — that compute's tasks and Lambda
# join. `sg_db` admits it. Neither group names a compute resource, so destroying compute
# removes members from a group rather than rules from one, and this root's plan stays
# empty. The alternative (an ingress rule here naming compute's task group) is the shape
# that makes a targeted destroy fail with `DependencyViolation` at the worst moment.

# `computes tasks` is the charset strip's missing apostrophe (H-082) and it stays: a
# security group's description is immutable in AWS, so re-wording it is a *replacement* of
# a group that RDS's own group references and that this phase's `No changes` plan depends
# on. A typo is cheaper than replacing a standing group to fix one. It goes when the data
# layer does, at the end of Phase 10.
resource "aws_security_group" "workload" {
  name        = "${var.project}-workload"
  description = "Anything in this VPC that may talk to Postgres. Joined by computes tasks and Lambda."
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All outbound: providers, ECR, CloudWatch, Secrets Manager."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-workload" }
}

resource "aws_security_group" "db" {
  name        = "${var.project}-db"
  description = "RDS. One ingress rule, and its source is a security group rather than a CIDR."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Postgres from anything wearing the workload group"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.workload.id]
  }

  tags = { Name = "${var.project}-db" }
}
