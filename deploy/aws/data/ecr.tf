# Two repositories, in the data layer, because an image is state.
#
# Phase 10 pulls the *same* gateway image this phase pushed — that is what "RDS and
# DynamoDB reused from P9's Terraform" implies for the thing that runs against them —
# and the gateway image with baked `bge-small` weights (BUILD_PLAN L6) is a ~1.5 GB
# upload from a home connection. Putting these in `compute` would make "destroy compute,
# keep data" cost that upload again, and would make the cost invisible until the moment
# somebody is trying to bring a cluster up.
#
# They are also the **tag seed**. AWS will not let a cost-allocation tag key be
# activated until it has seen the key on a resource, so the runbook applies exactly these
# two resources first (`-target`), activates `Project`/`Layer`/`Phase`/`ManagedBy` in
# Billing, and only then applies anything charged by the hour. An empty ECR repository
# costs nothing, which is what makes it the right thing to create first.

resource "aws_ecr_repository" "gateway" {
  name = "${var.project}/gateway"

  # A destroy flag, and the one people forget: without it `terraform destroy` fails on a
  # repository that still holds images, which is every repository that was ever used.
  force_delete = true

  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${var.project}-gateway" }
}

resource "aws_ecr_repository" "ui" {
  name                 = "${var.project}/ui"
  force_delete         = true
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${var.project}-ui" }
}

# Storage is $0.10/GB-month, so the gateway image alone is about $0.15 a month per copy
# kept. Five is enough to roll back twice and cheap enough not to think about.
locals {
  retention_policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last ${var.image_retention_count} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.image_retention_count
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "gateway" {
  repository = aws_ecr_repository.gateway.name
  policy     = local.retention_policy
}

resource "aws_ecr_lifecycle_policy" "ui" {
  repository = aws_ecr_repository.ui.name
  policy     = local.retention_policy
}
