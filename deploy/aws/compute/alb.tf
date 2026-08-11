# The two-listener ALB §P9 asks for: 8080 → the gateway, 3001 → the console, both
# admitting one source address (security.tf).
#
# The ports are the compose stack's host ports on purpose. An operator who has spent
# eight phases typing `localhost:8080` should not have to learn a second number to run
# the same curl against AWS, and the runbook's smoke commands differ from the local ones
# by exactly the hostname.

resource "aws_lb" "main" {
  name               = var.project
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = local.data.public_subnet_ids

  # A destroy flag. The default is already `false`; it is written out because every other
  # destroy flag in these two roots is, and a reader checking the list should find it.
  enable_deletion_protection = false

  # 60s is the default and it is the wrong default for a request whose upstream is a
  # model: Backline's per-question p50 is ~12.7 seconds and its tail is much longer, and
  # a streamed answer that takes ninety seconds is a normal answer. 180 is comfortably
  # past anything this gateway will legitimately serve and still short enough that a hung
  # upstream does not hold a connection all afternoon.
  idle_timeout = 180

  tags = { Name = var.project }
}

# --- target groups ---------------------------------------------------------------------

resource "aws_lb_target_group" "gateway" {
  name        = "${var.project}-gateway"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = local.data.vpc_id
  target_type = "ip" # awsvpc networking: targets are task ENIs, not instances.

  # 10 rather than the 300-second default. Nothing here is doing graceful long-lived
  # connection draining, and a destroy that waits five minutes per target group for no
  # reason is five minutes of ALB charges and five minutes of an operator wondering
  # whether it hung.
  deregistration_delay = 10

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = { Name = "${var.project}-gateway" }
}

resource "aws_lb_target_group" "ui" {
  name                 = "${var.project}-ui"
  port                 = 3000
  protocol             = "HTTP"
  vpc_id               = local.data.vpc_id
  target_type          = "ip"
  deregistration_delay = 10

  health_check {
    # The console's own liveness, which deliberately does *not* probe the gateway: it
    # would otherwise take a healthy console out of rotation during a gateway restart
    # (the same rule `docker-compose.yml` states, and H-000's one layer up).
    path                = "/api/healthz"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = { Name = "${var.project}-ui" }
}

# --- listeners ---------------------------------------------------------------------------

resource "aws_lb_listener" "gateway" {
  load_balancer_arn = aws_lb.main.arn
  port              = var.gateway_port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }
}

resource "aws_lb_listener" "ui" {
  load_balancer_arn = aws_lb.main.arn
  port              = var.ui_port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ui.arn
  }
}
