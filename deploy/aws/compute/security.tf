# Three security groups, all owned by this root, all destroyed with it.
#
# The data layer owns two more — `workload` (may reach Postgres) and `db` (is Postgres) —
# and the tasks below *join* `workload` rather than adding a rule to it. That is the whole
# of the "destroy compute, keep data" mechanism: membership is a property of the member,
# so removing the member is enough, and no ingress rule anywhere names a resource whose
# lifetime is shorter than the group it sits on.

# --- the load balancer: one source address ------------------------------------------
#
# §P9's words are "a two-listener ALB locked to the operator's home /32". This group is
# that lock, and it is the only place in either root where an address from outside the
# VPC appears. `var.home_cidr` has no default and refuses `0.0.0.0/0` (variables.tf).

resource "aws_security_group" "alb" {
  name        = "${var.project}-alb"
  description = "The two public listeners. One source CIDR, supplied at apply time."
  vpc_id      = local.data.vpc_id

  ingress {
    description = "Gateway listener, from the home CIDR only"
    from_port   = var.gateway_port
    to_port     = var.gateway_port
    protocol    = "tcp"
    cidr_blocks = [var.home_cidr]
  }

  ingress {
    description = "Console listener, from the home CIDR only"
    from_port   = var.ui_port
    to_port     = var.ui_port
    protocol    = "tcp"
    cidr_blocks = [var.home_cidr]
  }

  egress {
    description = "To the tasks. The ALB talks to nothing else."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-alb" }
}

# --- the tasks ------------------------------------------------------------------------
#
# The tasks run in **public** subnets with a public IP, which is what gives them a route
# to Anthropic, ECR, and CloudWatch without a NAT gateway (deploy/aws/data/network.tf).
# A public IP with no inbound rule except from the load balancer is a host with an egress
# path, not an exposed host — and this group is where that claim is made true.

# **This group has no inline rules at all, and that is deliberate.** One of its ingress
# rules is self-referencing (the console reaching the gateway), which an inline block
# cannot express without `self = true` — and the AWS provider is explicit that a security
# group carrying inline blocks must not also be the target of standalone rule resources:
# the inline set is authoritative, so it revokes anything it does not know about on the
# next apply. Mixing the two styles on one group is a rule that disappears silently on the
# second `terraform apply`, which is the worst time to find out. Every other group in these
# two roots is pure-inline; this one is pure-standalone.
resource "aws_security_group" "service" {
  name        = "${var.project}-service"
  description = "Fargate tasks: reachable from the ALB, and from each other on the gateway port."
  vpc_id      = local.data.vpc_id

  tags = { Name = "${var.project}-service" }
}

resource "aws_vpc_security_group_egress_rule" "service_all" {
  security_group_id = aws_security_group.service.id
  description       = "Providers, ECR, CloudWatch Logs, Secrets Manager, and Postgres."
  # With `-1` the port range must be omitted entirely rather than set to 0–0.
  ip_protocol = "-1"
  cidr_ipv4   = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "service_from_alb_gateway" {
  security_group_id            = aws_security_group.service.id
  description                  = "The gateway container port, from the load balancer"
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_ingress_rule" "service_from_alb_ui" {
  security_group_id            = aws_security_group.service.id
  description                  = "The console container port, from the load balancer"
  from_port                    = 3000
  to_port                      = 3000
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.alb.id
}

# The console → gateway hop, and the reason the ALB group can stay at one source address.
# Self-referencing rather than a fourth group: both services wear this group, and "a task
# in this stack may call the gateway" is exactly the permission being granted.
resource "aws_vpc_security_group_ingress_rule" "service_from_service" {
  security_group_id            = aws_security_group.service.id
  description                  = "The console reaching the gateway by its service-discovery name"
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.service.id
}

# --- the interface endpoint -----------------------------------------------------------
#
# The rollup Lambda runs in the private subnets, which have no default route, and its one
# AWS call is `GetSecretValue`. This group fronts the endpoint that makes that call
# possible. It lives in *compute* because the Lambda does: the endpoint's ~$0.48/day is
# not charged while only the data layer is standing, which matters through Phase 10's
# three-day window.

resource "aws_security_group" "endpoints" {
  name        = "${var.project}-endpoints"
  description = "The Secrets Manager interface endpoint. HTTPS, from the workload group."
  vpc_id      = local.data.vpc_id

  ingress {
    description     = "HTTPS from anything wearing the workload group: in practice, the Lambda"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [local.data.workload_security_group_id]
  }

  tags = { Name = "${var.project}-endpoints" }
}

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = local.data.vpc_id
  service_name        = "com.amazonaws.${var.region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.data.private_subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "${var.project}-secretsmanager" }
}
