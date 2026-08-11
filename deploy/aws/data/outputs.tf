# What `compute` reads through `terraform_remote_state`, and what Phase 10's Helm values
# will read the same way. Every one of these is an identifier or an endpoint; none is a
# credential, so `terraform output` is safe to paste into a runbook and into a screenshot.
#
# The one that looks like an exception is `db_master_secret_arn`, and it is not: an ARN
# names a secret without being one. Reading its *value* is a separate, audited call, and
# the runbook makes exactly one of those.

output "region" {
  description = "The region everything above lives in."
  value       = var.region
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Where the ALB and the Fargate tasks go: a default route, and a public IP each."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Where RDS and the rollup Lambda go: no default route, no way out."
  value       = aws_subnet.private[*].id
}

output "workload_security_group_id" {
  description = "Join this to be allowed to reach Postgres. Computes tasks and Lambda do."
  value       = aws_security_group.workload.id
}

output "db_endpoint" {
  description = "host:port. The DATABASE_URL secret is built from this by hand, once."
  value       = aws_db_instance.main.endpoint
}

output "db_address" {
  value = aws_db_instance.main.address
}

output "db_name" {
  value = aws_db_instance.main.db_name
}

output "db_username" {
  value = aws_db_instance.main.username
}

output "db_master_secret_arn" {
  description = <<-EOT
    The RDS-managed secret holding the generated master password. Terraform never saw the
    value; this is how the operator reads it once to compose the `database-url` secret.
  EOT
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "budgets_table_name" {
  value = aws_dynamodb_table.budgets.name
}

output "budgets_table_arn" {
  value = aws_dynamodb_table.budgets.arn
}

output "buckets_table_name" {
  value = aws_dynamodb_table.buckets.name
}

output "buckets_table_arn" {
  value = aws_dynamodb_table.buckets.arn
}

output "gateway_repository_url" {
  description = "docker push target for the gateway image."
  value       = aws_ecr_repository.gateway.repository_url
}

output "ui_repository_url" {
  value = aws_ecr_repository.ui.repository_url
}

output "database_url_secret_arn" {
  value = aws_secretsmanager_secret.database_url.arn
}

output "admin_token_secret_arn" {
  value = aws_secretsmanager_secret.admin_token.arn
}

output "anthropic_api_key_secret_arn" {
  value = aws_secretsmanager_secret.anthropic_api_key.arn
}

output "secret_names" {
  description = "The three secrets the operator fills in by hand, by name."
  value       = local.secret_names
}
