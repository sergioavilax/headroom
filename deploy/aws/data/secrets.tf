# Three secret **containers**. Terraform creates them; it never creates a version, so no
# value this project cares about is ever written to state.
#
# That is the whole design, and it is BUILD_PLAN §0.2 invariant 3 read literally: *"No
# API key ever enters the repo, a compose file's committed env, Terraform state, or a
# task definition's plain environment. `.env` locally, Secrets Manager on AWS — set by
# the human, out of band, leading-space CLI calls."* An `aws_secretsmanager_secret_version`
# resource with a `secret_string` would satisfy the first two clauses and break the third
# in the file that says so.
#
# So the values arrive by hand, once, with a leading space:
#
#     aws secretsmanager put-secret-value --secret-id headroom/anthropic-api-key …
#
# and `deploy/aws/README.md` §4 is where the exact commands live. A secret with no
# version is not a broken state: `terraform plan` is empty, and the *task* that reads it
# fails to start with a message naming the secret, which is the right time and the right
# place to find out.
#
# ── `recovery_window_in_days = 0`, and it is a destroy flag ────────────────────
# Secrets Manager's default is a 30-day recovery window, and a deleted-but-recoverable
# secret still owns its name. So the default turns a teardown-and-rebuild — which this
# phase does at least twice, and which P10 does again — into
# `InvalidRequestException: You can't create this secret because a secret with this name
# is already scheduled for deletion`, four weeks after the mistake was made. Zero deletes
# it immediately, which is the correct behaviour for a secret whose value the operator
# holds anyway and re-enters in one command.

locals {
  secret_names = {
    database_url      = "${var.project}/database-url"
    admin_token       = "${var.project}/admin-token"
    anthropic_api_key = "${var.project}/anthropic-api-key"
  }
}

# The whole `postgresql://user:password@host:5432/db` string, not its parts.
#
# One secret rather than a password plus a hostname, because the gateway, the migration
# runner, and the rollup Lambda all read exactly one thing — `DATABASE_URL` — and a
# deployment that assembled that string from pieces would be code written for the
# deployment rather than the code that ships. RDS's own generated password is in a
# *different*, RDS-managed secret; the runbook reads it once, percent-encodes it, and
# writes the URL here.
resource "aws_secretsmanager_secret" "database_url" {
  name                    = local.secret_names.database_url
  description             = "postgresql:// URL for the gateway, the migration runner, and the rollup Lambda"
  recovery_window_in_days = 0

  tags = { Name = local.secret_names.database_url }
}

# The root admin token (H-019). Unset means the admin API is **off**, not open — so a
# deployment that forgot this secret answers 503 on every `/admin` route and the console
# says so, rather than publishing tenant-and-key CRUD behind an IP allow-list.
resource "aws_secretsmanager_secret" "admin_token" {
  name                    = local.secret_names.admin_token
  description             = "HEADROOM_ADMIN_TOKEN the root credential for /admin/*"
  recovery_window_in_days = 0

  tags = { Name = local.secret_names.admin_token }
}

# The one secret that costs money when it is used. The gateway boots fine without it and
# only a request routed to `claude-*` fails, naming the variable (H-014) — which is why
# the ALB can come up healthy before this value exists.
resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = local.secret_names.anthropic_api_key
  description             = "ANTHROPIC_API_KEY for the deployed gateways live smoke"
  recovery_window_in_days = 0

  tags = { Name = local.secret_names.anthropic_api_key }
}
