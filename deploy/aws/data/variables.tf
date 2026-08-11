# Every variable has a default except none — this root can be applied with an empty
# tfvars file. That is deliberate: nothing here is secret, nothing here is
# machine-specific, and a layer that cannot be applied without being configured is a
# layer somebody configures wrongly at 1 a.m.
#
# The one variable that *is* machine-specific — the operator's home /32 — belongs to
# `compute`, because it is the ALB's allow-list and the ALB is compute.

variable "region" {
  description = "AWS region. One region, deliberately: multi-region is in what production adds."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "The Project cost-allocation tag, and the prefix every name is built from."
  type        = string
  default     = "headroom"
}

variable "phase" {
  description = <<-EOT
    The `Phase` cost-allocation tag. Provenance — which phase created a resource — not a
    date range. It stays `p9` on this layer through Phase 10's window, because that is
    when these resources were created; "what did the data layer cost during P10" is a
    date filter on `Layer=data`, which is what that tag is for.
  EOT
  type        = string
  default     = "p9"
}

variable "vpc_cidr" {
  description = "The VPC address space. /16, carved into two public and two private /20s."
  type        = string
  default     = "10.42.0.0/16"
}

variable "db_instance_class" {
  description = <<-EOT
    RDS instance class. `db.t4g.micro` is the smallest Graviton class Postgres 16 offers
    and it is sized for a demo, not for the H2 suite: at ~$0.016/hr it is the second
    largest line on this stack's daily bill after the ALB. Raising it is a deliberate act
    with a cost stated in deploy/aws/README.md's table.
  EOT
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "Gigabytes of gp3. 20 is the RDS minimum; the ledger is measured in megabytes."
  type        = number
  default     = 20
}

variable "db_engine_version" {
  description = <<-EOT
    Postgres major version. **16, and it is not a free choice**: BUILD_PLAN L2 fixes it,
    H-001 pins the local image to `pgvector/pgvector:pg16`, and `migrations/0005` runs
    `CREATE EXTENSION vector` — which RDS supports on 16 and which is the one thing about
    this instance that the compose stack cannot prove in advance.
  EOT
  type        = string
  default     = "16"
}

variable "image_retention_count" {
  description = "How many images each ECR repository keeps. Enough to roll back once, twice over."
  type        = number
  default     = 5
}
