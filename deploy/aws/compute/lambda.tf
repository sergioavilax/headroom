# The one Lambda. BUILD_PLAN §P9: *"ONE Lambda: the nightly cost-rollup (EventBridge
# schedule → aggregate the day's ledger into `daily_rollups` → the dashboard's history
# view reads it) — a genuine, small, defensible Lambda, not decoration."*
#
# ── What it costs to make it a Lambda, stated ──────────────────────────────────
# A scheduled ECS task on the gateway's own task definition would do the same work with
# no new packaging, no VPC endpoint, and ~$0.48/day less — it would run in a public
# subnet and reach Secrets Manager over the internet like every other task here. The
# Lambda runs in the *private* subnets because that is where it can reach RDS, and a
# Lambda in a subnet with no default route needs an interface endpoint to make one AWS
# call (security.tf).
#
# It is a Lambda anyway, and the reason is in BUILD_PLAN's own second paragraph: DynamoDB
# and Lambda are the two listing gaps this project exists to close, and closing one of
# them with "we used a cron task instead" closes nothing. The endpoint is what that costs
# and it is a line in the runbook's cost table rather than a footnote.
#
# ── The handler is not new code ────────────────────────────────────────────────
# `headroom.rollup.handler.handler` resolves which days to roll up and calls
# `LedgerStore.write_daily_rollup`, which is a store method implemented on both stores and
# asserted by the same contract suite as `totals` and `series`. The Lambda ships the
# `headroom` package itself — `make lambda-build` copies it beside `asyncpg` — so there is
# no second definition of what a day of the ledger sums to, and `python -m headroom.rollup`
# on a laptop runs the identical path.

data "archive_file" "rollup" {
  type = "zip"
  # Built by `make lambda-build`, which is the runbook's §3. If this errors with "no such
  # file or directory", that is the build step, not a Terraform problem.
  source_dir  = "${path.module}/../lambda/build"
  output_path = "${path.module}/../lambda/rollup.zip"
}

resource "aws_lambda_function" "rollup" {
  function_name = "${var.project}-rollup"
  role          = aws_iam_role.rollup.arn

  handler = "headroom.rollup.handler.handler"
  runtime = "python3.12"
  # One architecture across the whole stack, matching the Fargate tasks and the machine
  # the wheels were built on. arm64 is ~20% cheaper and would mean cross-building
  # asyncpg's manylinux wheel for a function that runs twice a day.
  architectures = ["x86_64"]

  filename         = data.archive_file.rollup.output_path
  source_code_hash = data.archive_file.rollup.output_base64sha256

  timeout     = var.rollup_timeout_s
  memory_size = var.rollup_memory

  environment {
    variables = {
      # An **ARN**, not a value. It names a secret without being one, so it is safe here,
      # in Terraform state, and in a `GetFunctionConfiguration` screenshot — which is the
      # whole reason `headroom/rollup/handler.py` reads the secret at invocation instead
      # of taking a connection string from its environment.
      DATABASE_URL_SECRET_ARN = local.data.database_url_secret_arn
    }
  }

  vpc_config {
    # Private subnets: it needs Postgres, and Postgres is not reachable from anywhere
    # else. It joins the data layer's `workload` group for exactly that permission.
    subnet_ids         = local.data.private_subnet_ids
    security_group_ids = [local.data.workload_security_group_id]
  }

  # The log group is created in logs.tf so it carries a retention and so it goes away
  # with the rest; without this the function would race it and create an unmanaged one.
  depends_on = [
    aws_cloudwatch_log_group.rollup,
    aws_iam_role_policy_attachment.rollup_vpc,
  ]

  tags = { Name = "${var.project}-rollup" }
}

# --- the schedule ---------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "rollup_nightly" {
  name                = "${var.project}-rollup-nightly"
  description         = "Aggregate yesterday and today into daily_rollups"
  schedule_expression = var.rollup_schedule

  tags = { Name = "${var.project}-rollup-nightly" }
}

resource "aws_cloudwatch_event_target" "rollup_nightly" {
  rule = aws_cloudwatch_event_rule.rollup_nightly.name
  arn  = aws_lambda_function.rollup.arn

  # An empty object rather than no input at all: `resolve_days` reads `day` and `days`
  # out of the event and falls back to `DEFAULT_ROLLUP_DAYS` when neither is there, so
  # the scheduled invocation and a bare `aws lambda invoke` take the same branch. Passing
  # EventBridge's own scheduled-event envelope would work too — it has neither key — but
  # `{}` is what the tests exercise and what the runbook fires by hand.
  input = jsonencode({})
}

resource "aws_lambda_permission" "rollup_nightly" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rollup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.rollup_nightly.arn
}
