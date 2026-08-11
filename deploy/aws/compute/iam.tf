# Three roles, and the split between the first two is the one worth reading.
#
# The **execution role** belongs to the ECS agent: it pulls the image, writes to the log
# group, and — the part that matters here — resolves the `secrets` block of a task
# definition. It is the only principal that reads the three Secrets Manager values, and
# it hands them to the container as environment variables that never existed in
# Terraform, in the task definition's plain `environment`, or in any file.
#
# The **task role** belongs to the process: it is what `boto3` inside the gateway signs
# with. It has DynamoDB and nothing else — no secrets, no logs, no ECR.
#
# The console gets no task role at all. It makes no AWS call, and a role attached "just
# in case" is a permission nobody can later argue was needed.

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# --- execution role ------------------------------------------------------------------

resource "aws_iam_role" "execution" {
  name               = "${var.project}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid     = "ReadTheThreeSecretsTheTaskDefinitionNames"
    actions = ["secretsmanager:GetSecretValue"]
    # Enumerated, not `${project}/*`. A wildcard would quietly grant the RDS-managed
    # master-password secret too, which no task has any business reading — the operator
    # reads it once, by hand, to compose the URL these tasks actually use.
    resources = [
      local.data.database_url_secret_arn,
      local.data.admin_token_secret_arn,
      local.data.anthropic_api_key_secret_arn,
    ]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# --- the gateway's own role ------------------------------------------------------------

resource "aws_iam_role" "gateway_task" {
  name               = "${var.project}-gateway-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "gateway_dynamodb" {
  statement {
    sid = "TheBudgetGateAndTheTokenBuckets"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Scan",
    ]
    resources = [
      local.data.budgets_table_arn,
      local.data.buckets_table_arn,
    ]
  }

  statement {
    sid = "EnsureTableStaysTheSameCodePathItIsLocally"
    # `DescribeTable` is what `DynamoClient.ensure_table` calls on every cold client, and
    # against this stack it always succeeds — Terraform made both tables. `CreateTable`
    # is granted anyway, scoped to exactly these two ARNs, because BUILD_PLAN §P9 asks
    # for "the P4/4b conditional-write code path UNCHANGED" and a permission the code
    # would need is part of the path. It should never fire; if it ever does, the table's
    # `CreationDateTime` says so.
    actions   = ["dynamodb:DescribeTable", "dynamodb:CreateTable"]
    resources = [local.data.budgets_table_arn, local.data.buckets_table_arn]
  }
}

resource "aws_iam_role_policy" "gateway_dynamodb" {
  name   = "dynamodb"
  role   = aws_iam_role.gateway_task.id
  policy = data.aws_iam_policy_document.gateway_dynamodb.json
}

# --- the rollup Lambda's role -----------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "rollup" {
  name               = "${var.project}-rollup"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# ENI management for the VPC config, plus CloudWatch Logs. The managed policy is the
# documented one for a VPC Lambda and hand-writing it buys nothing but a chance to get
# `ec2:CreateNetworkInterface` subtly wrong.
resource "aws_iam_role_policy_attachment" "rollup_vpc" {
  role       = aws_iam_role.rollup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "rollup_secrets" {
  statement {
    sid = "ReadTheDatabaseUrlAtInvocation"
    # One secret, and not the two the gateway also reads: the rollup has no upstream
    # provider to call and no admin API to authenticate. `headroom/rollup/handler.py`
    # fetches this at invocation rather than caching it — a nightly function is cold
    # every time, and a cached connection string is a credential held across a rotation.
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.data.database_url_secret_arn]
  }
}

resource "aws_iam_role_policy" "rollup_secrets" {
  name   = "secrets"
  role   = aws_iam_role.rollup.id
  policy = data.aws_iam_policy_document.rollup_secrets.json
}
