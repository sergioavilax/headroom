# The COMPUTE layer: everything that is meant to be destroyed at the end of the day.
#
# BUILD_PLAN §P9's gate: *"destroy the same day unless P10 follows immediately"*. This
# root is what that sentence is about. `terraform -chdir=deploy/aws/compute destroy`
# removes the ALB, both Fargate services, the rollup Lambda, its schedule, the alarms,
# the log groups, and the interface endpoint — and leaves RDS, DynamoDB, the images, and
# the secrets standing for Phase 10.
#
# **This root reads the data layer and never writes to it.** Every dependency goes one
# way, through `terraform_remote_state` (see data.tf), and the only resources that name a
# data-layer object are ones this root *owns*: security groups whose members are compute's
# tasks, and IAM policies whose resources are data's table and secret ARNs. Nothing here
# adds a rule to a data-layer security group or a version to a data-layer secret, which is
# what makes the destroy a plan-and-apply rather than an ordering puzzle.
#
# ── There is no TLS on the ALB, and it is a stated limitation ──────────────────
# Both listeners are HTTP. ACM issues a certificate for a domain, and this stack has no
# domain — the security control is the /32 allow-list on the security group, which is
# what §P9 asks for by name. The cost is real and worth writing down rather than
# discovering: the root admin token crosses the operator's own connection to the ALB in
# the clear. What production adds is a hostname, an ACM certificate, HTTPS listeners, and
# an HTTP listener that does nothing but redirect — four resources and a DNS record, none
# of which this phase can honestly claim to have tested.

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}

provider "aws" {
  region = var.region

  # `Layer = "compute"` is the tag that makes the bill answer the question this phase's
  # cost table asks: what does the stack cost with the compute up, and what does the data
  # layer cost alone while P10's cluster runs. See deploy/aws/data/versions.tf for why
  # these keys have to be activated in Billing before the first hourly resource exists.
  default_tags {
    tags = {
      Project   = var.project
      Layer     = "compute"
      Phase     = var.phase
      ManagedBy = "terraform"
    }
  }
}
