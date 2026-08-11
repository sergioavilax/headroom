# What the operator needs after an apply: two URLs, and the four values the runbook's
# `run-task` commands take. Everything else is in the console.

output "gateway_url" {
  description = "The gateway, reachable only from the CIDR in home_cidr."
  value       = "http://${aws_lb.main.dns_name}:${var.gateway_port}"
}

output "console_url" {
  description = "The operator console. Sign in with the root admin token; it is never deployed (H-055)."
  value       = "http://${aws_lb.main.dns_name}:${var.ui_port}"
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "gateway_task_definition" {
  description = "Family:revision. The migration runner and any one-off use this exact definition."
  value       = "${aws_ecs_task_definition.gateway.family}:${aws_ecs_task_definition.gateway.revision}"
}

output "gateway_log_group" {
  value = aws_cloudwatch_log_group.gateway.name
}

output "rollup_function_name" {
  description = "aws lambda invoke --function-name this fires the rollup by hand."
  value       = aws_lambda_function.rollup.function_name
}

output "rollup_log_group" {
  value = aws_cloudwatch_log_group.rollup.name
}

output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

# The three arguments `aws ecs run-task --network-configuration` wants, pre-assembled.
# The migration step is the one command in this runbook where getting a subnet list wrong
# produces a task that starts, cannot reach anything, and times out — so it is an output
# rather than an instruction to go and look.
output "run_task_network_configuration" {
  description = "Paste into aws ecs run-task --network-configuration."
  value = jsonencode({
    awsvpcConfiguration = {
      subnets        = local.data.public_subnet_ids
      securityGroups = [aws_security_group.service.id, local.data.workload_security_group_id]
      assignPublicIp = "ENABLED"
    }
  })
}

output "account_id" {
  description = "For the docker login line, so the runbook never asks anyone to remember it."
  value       = data.aws_caller_identity.current.account_id
}
