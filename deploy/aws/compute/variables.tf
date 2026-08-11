# One variable has no default, and it is the one that must never be committed.

variable "home_cidr" {
  description = <<-EOT
    The **only** source address the ALB admits, as a CIDR — the operator's home /32,
    supplied at apply time in a gitignored `terraform.tfvars`.

    No default, deliberately. A default of `0.0.0.0/0` would publish a tenant-and-key
    control plane to the internet the first time somebody forgot a flag, and a default of
    someone's old address would be worse: it would fail closed in a way that reads like a
    networking problem. Terraform refusing to plan is the correct behaviour.

    A CIDR rather than a bare address so a second operator, or a /29 at an office, is a
    value change rather than a code change. `curl -s https://checkip.amazonaws.com`
    prints the address; the runbook's §6 turns it into a /32.
  EOT
  type        = string

  validation {
    # Not a security control — a typo catcher. `10.0.0.1/32` would pass this and be
    # useless; `0.0.0.0/0` would pass this and be dangerous, which is why the second
    # check exists below rather than being implied by this one.
    condition     = can(cidrnetmask(var.home_cidr))
    error_message = "home_cidr must be a CIDR, e.g. 203.0.113.7/32."
  }

  validation {
    condition     = var.home_cidr != "0.0.0.0/0"
    error_message = "home_cidr must not be 0.0.0.0/0: the ALB fronts the admin API and the console."
  }
}

variable "region" {
  description = "AWS region. Must match the data layer's."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix and the `Project` cost-allocation tag. Must match the data layer's."
  type        = string
  default     = "headroom"
}

variable "phase" {
  description = "The `Phase` cost-allocation tag for this layer. `p10` when the EKS window reuses the data layer."
  type        = string
  default     = "p9"
}

variable "data_state_path" {
  description = <<-EOT
    Path to the data layer's state file. Local backend, so this is a relative path rather
    than a bucket — see deploy/aws/data/versions.tf for why there is no remote backend,
    and for the warning about `git clean` that goes with it.
  EOT
  type        = string
  default     = "../data/terraform.tfstate"
}

# --- images -----------------------------------------------------------------------

variable "gateway_image_tag" {
  description = <<-EOT
    Tag of the gateway image in ECR. `latest` works and is what the runbook pushes; ECR
    tags are mutable, so a re-push under the same tag needs
    `aws ecs update-service --force-new-deployment` to be picked up. Passing a git sha
    instead makes the deployment self-describing and the rollback obvious.
  EOT
  type        = string
  default     = "latest"
}

variable "ui_image_tag" {
  description = "Tag of the console image in ECR."
  type        = string
  default     = "latest"
}

# --- sizing -------------------------------------------------------------------------

variable "gateway_cpu" {
  description = "Fargate CPU units for the gateway. 512 = 0.5 vCPU."
  type        = number
  default     = 512
}

variable "gateway_memory" {
  description = <<-EOT
    MiB for the gateway. 2048 rather than 1024 because the deploy image carries the
    `embed` extra and `bge-small-en-v1.5`'s weights (BUILD_PLAN L6): a semantic cache
    that loads a CPU torch model needs the headroom, and a task killed by the OOM reaper
    mid-embedding is a confusing way to learn that.
  EOT
  type        = number
  default     = 2048
}

variable "gateway_desired_count" {
  description = <<-EOT
    Gateway tasks. **1 by default, and 2 is the interesting setting.**

    H-018 fixed the auth cache's 5-second TTL as a *cross-process* bound and said so with
    Phase 9 in mind: "a revoked key is dead on the next request in the process that
    revoked it, and dead within 5 seconds everywhere else." One task cannot show that;
    two can. It costs about $0.70/day, which is why it is not the default on a stack
    whose whole budget is $5–8.
  EOT
  type        = number
  default     = 1
}

variable "ui_cpu" {
  description = "Fargate CPU units for the console. It renders JSON; 0.25 vCPU is plenty."
  type        = number
  default     = 256
}

variable "ui_memory" {
  description = "MiB for the console."
  type        = number
  default     = 512
}

variable "ui_desired_count" {
  type    = number
  default = 1
}

# --- listeners ------------------------------------------------------------------------

variable "gateway_port" {
  description = "ALB listener port for the gateway. 8080 to match the compose stack's host port (H-006)."
  type        = number
  default     = 8080
}

variable "ui_port" {
  description = "ALB listener port for the console. 3001, matching compose, matching Backline's 3000 being taken."
  type        = number
  default     = 3001
}

# --- the rollup Lambda ------------------------------------------------------------------

variable "rollup_schedule" {
  description = <<-EOT
    EventBridge schedule for the nightly rollup, in UTC. 00:15 rather than 00:00: the
    ledger writer is fire-and-forget with a drain queue (H-027), so a request that
    arrived at 23:59:59 can land its row a moment after midnight, and a job that fired on
    the stroke would race it. Fifteen minutes is far more than the queue's drain latency
    and still "nightly" by any reading.

    The handler covers today *and* yesterday on every run anyway
    (`DEFAULT_ROLLUP_DAYS`), so a late row is picked up the following night even if this
    margin is ever not enough.
  EOT
  type        = string
  default     = "cron(15 0 * * ? *)"
}

variable "rollup_timeout_s" {
  description = "Seconds before the rollup is killed. Two aggregate queries; 120 is generous."
  type        = number
  default     = 120
}

variable "rollup_memory" {
  description = "MiB for the rollup. It holds one summary and a connection; 512 is the floor worth using."
  type        = number
  default     = 512
}

# --- observability ----------------------------------------------------------------------

variable "log_retention_days" {
  description = <<-EOT
    CloudWatch Logs retention. 7 days, because this stack is meant to live for one or two
    and the evidence lives in the repo rather than in a log group (invariant 9). Never
    `0` (never expire): a log group nobody deletes is the classic thing an "empty check"
    misses and the bill remembers.
  EOT
  type        = number
  default     = 7
}

variable "alarm_email" {
  description = <<-EOT
    Where the alarms page. Empty means the SNS topic exists with no subscriber — the
    alarms still change state and still show in the console, they just do not reach
    anybody, which is a demo and not a page.

    An email subscription must be **confirmed** from the inbox before it delivers
    anything; the runbook's §7 says so, because an unconfirmed subscription looks
    identical to a working one in `terraform apply`'s output.
  EOT
  type        = string
  default     = ""
}

variable "alarm_5xx_percent" {
  description = <<-EOT
    Percentage of requests answering 5xx that counts as an incident, over five minutes.

    5%, and it only evaluates above `alarm_5xx_min_requests` — because one failure out of
    one request is a 100% error rate and is not an incident. §P8.H3's own headline is
    "zero caller-visible 5xx at every intensity", so this alarm firing means something
    the repo publishes as impossible has happened.
  EOT
  type        = number
  default     = 5
}

variable "alarm_5xx_min_requests" {
  description = "Requests in a five-minute period below which the 5xx rate is not evaluated."
  type        = number
  default     = 20
}

variable "alarm_provider_failures" {
  description = <<-EOT
    Provider-failure log lines in five minutes that count as "a provider is down".

    3, and the number comes from H-052 rather than from taste: the circuit breaker needs
    5 samples and a 0.5 failure ratio before it will trip, so three failures inside five
    minutes is "something real is happening and the breaker may not have noticed yet" —
    early enough to be useful, far enough above one flaky request to be believed.
  EOT
  type        = number
  default     = 3
}
