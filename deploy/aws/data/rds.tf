# Postgres 16 + pgvector, the same schema the compose stack runs, applied by the same
# runner (`python -m headroom.db.migrate`) as a one-off ECS task — see
# deploy/aws/README.md §5. Not a bastion, not `psql` from a laptop: same code, same
# image, same `DATABASE_URL`, which is BUILD_PLAN §P9's own wording.
#
# ── The master password never enters Terraform state ───────────────────────────
# `manage_master_user_password = true` hands the credential to RDS, which generates it
# and stores it in a secret of its own. Terraform never sees the value, so invariant 3
# holds *structurally* rather than by anyone remembering not to write a `password =`
# line. The alternatives both fail it: a `password` argument puts the literal in state,
# and `random_password` puts its `result` there too.
#
# The cost of that choice is one manual step, and it is in the runbook: the operator
# reads the generated password once, percent-encodes it, and writes the whole
# `postgresql://…` URL into the `database-url` secret that the tasks actually read. Two
# secrets rather than one, and neither of them is in a state file or a task definition.
#
# ── Built to die ───────────────────────────────────────────────────────────────
# `skip_final_snapshot`, `deletion_protection = false`, and `backup_retention_period = 0`
# are all destroy flags and all three are on from the first apply, per the phase brief.
# Zero retention also removes the automated snapshots that would otherwise outlive the
# instance and quietly bill for storage after the "empty checks" said the account was
# clean — which is exactly the class of leftover those checks exist to catch.

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-db"
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = "${var.project}-db" }
}

resource "aws_db_instance" "main" {
  identifier = "${var.project}-db"

  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage = var.db_allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.project
  username = var.project

  # The whole of invariant 3's answer for this resource. See the header.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]

  # Private subnets, no public address. The door in is an ECS task on the same VPC,
  # which is also the door migrations come through.
  publicly_accessible = false
  multi_az            = false

  # --- destroy flags, from day one ---
  backup_retention_period  = 0
  skip_final_snapshot      = true
  deletion_protection      = false
  delete_automated_backups = true

  apply_immediately          = true
  auto_minor_version_upgrade = true
  copy_tags_to_snapshot      = true

  # Performance Insights is genuinely useful and genuinely not free past its retention
  # floor. Off, and named here so the omission reads as a decision.
  performance_insights_enabled = false

  tags = { Name = "${var.project}-db" }
}
