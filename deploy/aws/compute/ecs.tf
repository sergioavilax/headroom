# Two Fargate services on one cluster: the gateway and the console.
#
# ── Nothing about the application changed to run here ──────────────────────────
# The image is the one the Dockerfile builds, the entrypoint is the one it declares, and
# the configuration is the environment variables `docker-compose.yml` already sets — with
# exactly one line *missing*. `DYNAMODB_ENDPOINT_URL` is absent, which is how
# `headroom/db/dynamo.py` knows to resolve the regional endpoint and to sign with the
# task role instead of the emulator's dummy credential. That absence is the entire
# difference between the compose stack and this one on the DynamoDB path, and it is what
# makes assumption A1's second half a real verification rather than a re-run.
#
# ── Secrets arrive through the execution role, never through `environment` ─────
# The three values below are `secrets`, not `environment`: ECS resolves them at task
# start with the execution role's permission and injects them into the process. A task
# definition's plain `environment` is readable by anyone with `ecs:DescribeTaskDefinition`
# and is what BUILD_PLAN §0.2 invariant 3 names by hand.
#
# All three must have a value before this root is applied. A secret Terraform created and
# nobody filled in makes the task fail to start with a message naming it — loud, early,
# and in the right place, which is why the runbook fills them in at §4 and applies here
# at §6.

resource "aws_ecs_cluster" "main" {
  name = var.project

  setting {
    # Container Insights is a per-metric charge on a stack whose entire budget is $5–8,
    # and the observability this phase actually needs is three alarms over a log group.
    # Off, and named so the omission reads as a decision.
    name  = "containerInsights"
    value = "disabled"
  }

  tags = { Name = var.project }
}

# --- service discovery ------------------------------------------------------------------
#
# So the console can reach the gateway without going out through a public load balancer
# whose security group admits one address. See data.tf's `gateway_internal_url`.

resource "aws_service_discovery_private_dns_namespace" "main" {
  name        = "${var.project}.local"
  description = "Service-to-service names inside the VPC"
  vpc         = local.data.vpc_id

  tags = { Name = "${var.project}.local" }
}

resource "aws_service_discovery_service" "gateway" {
  name = "gateway"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  # Custom health checking: ECS is the thing that knows whether a task is healthy, and
  # it deregisters the instance itself. No `failure_threshold` — AWS pins it to 1 and the
  # provider deprecates the argument.
  health_check_custom_config {}

  tags = { Name = "${var.project}-gateway" }
}

# --- the gateway --------------------------------------------------------------------------

locals {
  gateway_environment = [
    # boto3 needs a region and Fargate does not reliably supply one. Everything else on
    # this list is a value `docker-compose.yml` sets too.
    { name = "AWS_REGION", value = var.region },
    { name = "HEADROOM_BUDGETS_TABLE", value = local.data.budgets_table_name },
    { name = "HEADROOM_BUCKETS_TABLE", value = local.data.buckets_table_name },
    { name = "HEADROOM_LOG_LEVEL", value = "INFO" },
    # BUILD_PLAN L6: the embedding weights are baked into the deploy image. These two say
    # so at runtime — `HF_HOME` points at where the build put them, and `HF_HUB_OFFLINE`
    # makes a cache miss an error instead of a silent download from a task that should
    # never be talking to HuggingFace. A gateway that pulled weights on first use would
    # turn the first semantic request of the day into a 200 MB fetch.
    { name = "HF_HOME", value = "/opt/hf" },
    { name = "HF_HUB_OFFLINE", value = "1" },
  ]
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${var.project}-gateway"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.gateway_cpu
  memory                   = var.gateway_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.gateway_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "gateway"
      image     = "${local.data.gateway_repository_url}:${var.gateway_image_tag}"
      essential = true

      portMappings = [{ containerPort = 8000, protocol = "tcp" }]

      environment = local.gateway_environment

      secrets = [
        { name = "DATABASE_URL", valueFrom = local.data.database_url_secret_arn },
        { name = "HEADROOM_ADMIN_TOKEN", valueFrom = local.data.admin_token_secret_arn },
        { name = "ANTHROPIC_API_KEY", valueFrom = local.data.anthropic_api_key_secret_arn },
      ]

      # The same command `docker-compose.yml` uses, so "healthy" means one thing across
      # both environments rather than two things that happen to agree.
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=4)\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.gateway.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "gateway"
        }
      }
    }
  ])

  tags = { Name = "${var.project}-gateway" }
}

resource "aws_ecs_service" "gateway" {
  name            = "gateway"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = var.gateway_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets = local.data.public_subnet_ids
    # Two groups: one says who may reach this task, the other says this task may reach
    # Postgres. The second is owned by the *data* layer and joined here, which is what
    # keeps `destroy compute` from touching a data-layer resource.
    security_groups  = [aws_security_group.service.id, local.data.workload_security_group_id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.gateway.arn
    container_name   = "gateway"
    container_port   = 8000
  }

  service_registries {
    registry_arn = aws_service_discovery_service.gateway.arn
  }

  # The image is large (baked weights), so a cold pull is the slow part of a first start.
  # 120s of grace before the load balancer is allowed to call a starting task unhealthy.
  health_check_grace_period_seconds = 120

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # A listener that does not exist yet cannot have a target registered against it.
  depends_on = [aws_lb_listener.gateway]

  tags = { Name = "${var.project}-gateway" }
}

# --- the console ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "ui" {
  family                   = "${var.project}-ui"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ui_cpu
  memory                   = var.ui_memory
  execution_role_arn       = aws_iam_role.execution.arn
  # No task role. The console makes no AWS call — it is a client of `/admin/*` and
  # nothing else (H-054) — and a role attached "just in case" is a permission nobody can
  # later argue was needed.

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "ui"
      image     = "${local.data.ui_repository_url}:${var.ui_image_tag}"
      essential = true

      portMappings = [{ containerPort = 3000, protocol = "tcp" }]

      # One variable, and it is a URL. H-055's property, unchanged on AWS: the console is
      # handed no secret at all — not even by reference — so there is nothing here to
      # rotate, nothing to leak from a `DescribeTaskDefinition`, and no `secrets` block.
      # The root admin token is typed into the sign-in screen and exchanged for an
      # httpOnly cookie by the console's own server.
      environment = [
        { name = "HEADROOM_GATEWAY_URL", value = local.gateway_internal_url },
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "node -e \"fetch('http://localhost:3000/api/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ui.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "ui"
        }
      }
    }
  ])

  tags = { Name = "${var.project}-ui" }
}

resource "aws_ecs_service" "ui" {
  name            = "ui"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.ui.arn
  desired_count   = var.ui_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets = local.data.public_subnet_ids
    # The console joins `service` (reachable from the ALB, and allowed to reach the
    # gateway) but **not** `workload`: it has no database to reach, and the group that
    # opens Postgres is not one to wear by default.
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.ui.arn
    container_name   = "ui"
    container_port   = 3000
  }

  health_check_grace_period_seconds = 60

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [aws_lb_listener.ui]

  tags = { Name = "${var.project}-ui" }
}
