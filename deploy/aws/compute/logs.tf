# Log groups, created explicitly rather than left to ECS and Lambda to create implicitly.
#
# Two reasons, and the second is the one that bites. A group Terraform did not create is
# a group `terraform destroy` does not delete — so it survives the teardown, keeps its
# default *never expire* retention, and is exactly the kind of leftover the phase's
# "per-service empty checks" exist to catch and the bill quietly remembers. And the
# metric filters in alarms.tf need a group that exists at plan time regardless.

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/ecs/${var.project}/gateway"
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project}-gateway" }
}

resource "aws_cloudwatch_log_group" "ui" {
  name              = "/ecs/${var.project}/ui"
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project}-ui" }
}

# Lambda writes to `/aws/lambda/<function-name>` whether this exists or not; creating it
# here is what puts a retention on it and what makes it go away with everything else.
resource "aws_cloudwatch_log_group" "rollup" {
  name              = "/aws/lambda/${var.project}-rollup"
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project}-rollup" }
}
