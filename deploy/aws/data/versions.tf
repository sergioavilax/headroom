# The DATA layer: everything whose lifetime is longer than one deployment.
#
# ── Why there are two roots ────────────────────────────────────────────────────
# BUILD_PLAN §P9's gate says "destroy the same day"; §P10 reuses this phase's RDS and
# DynamoDB for a three-day EKS window. Both cannot be true of one state file, so the
# split is by **lifetime**, not by service kind:
#
#   deploy/aws/data     RDS, DynamoDB, the VPC they sit in, ECR, the secret containers.
#                       Created once in P9, survives P9's teardown, carries P10, and is
#                       destroyed at the end of P10.
#   deploy/aws/compute  ALB, ECS, the rollup Lambda, the alarms, the log groups.
#                       Created and destroyed inside a day, twice if need be.
#
# ECR is here rather than in compute because an image is *state*: P10's Helm chart pulls
# the same image this phase pushed, and a teardown that deleted it would make "destroy
# compute, keep data" a lie in the one place it costs an hour of upload to discover.
#
# **Nothing in `compute` is referenced from here.** That is the property that makes the
# targeted destroy a first-class operation rather than surgery: `terraform -chdir=…
# /compute destroy` touches no resource this root owns, and this root's plan is empty
# afterwards. Where compute needs to be *reachable* by something here — the database
# security group, say — the rule lives on a group this root creates and compute's own
# members join it, so the dependency points one way and the wrong way round is
# impossible to write.
#
# ── State ──────────────────────────────────────────────────────────────────────
# Local backend, deliberately. One operator, one machine, two roots; an S3 backend plus
# a lock table would be two more resources to create before the first apply and two more
# to destroy after the last, and the plan's whole cost discipline is about not paying for
# scaffolding. `terraform.tfstate` is gitignored (state can carry secrets; the lock file
# cannot, and is committed). It also means the state file is the only copy: **do not
# `git clean -xdf` between an apply and its destroy.**

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  # ── The cost-allocation tags, on everything ─────────────────────────────────
  # `default_tags` rather than a `tags` block per resource: the one lesson BUILD_PLAN
  # §0.4's A7 names by hand is that Backline's cost chase failed because resources were
  # not tagged from the first apply. A tag added to eleven resources by hand is a tag
  # missing from the twelfth.
  #
  # `Layer` is the one that earns its place: it is how the bill answers "what is the
  # data layer costing me while P10's cluster runs", which is the number §P10's
  # estimate-vs-actual table needs. `Phase` records what *created* a resource and
  # deliberately stays `p9` on this layer through P10's window — it is provenance, not a
  # date range; the date range is Cost Explorer's job.
  #
  # These keys must be ACTIVATED in Billing → Cost allocation tags before they appear in
  # Cost Explorer, and AWS will not offer a key it has never seen on a resource. The
  # runbook's step 1 therefore creates the two ECR repositories first — free, and enough
  # to make the keys visible — and activates them before anything is charged by the hour.
  default_tags {
    tags = {
      Project   = var.project
      Layer     = "data"
      Phase     = var.phase
      ManagedBy = "terraform"
    }
  }
}
