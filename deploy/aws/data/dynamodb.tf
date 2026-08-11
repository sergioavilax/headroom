# The two tables Phase 4 and Phase 4b run on, for real this time.
#
# **This is where assumption A1 gets its second half.** §0.4 says
# `amazon/dynamodb-local` behaves like DynamoDB for `ConditionExpression` conditional
# writes, "verified at P4 gate … then identically against real DynamoDB in P9". Nothing
# in `headroom/db/{budgets,buckets}.py` changes for that verification, and nothing here
# is shaped to make it easier: the key names, the billing mode, and the TTL attribute are
# the ones the code already writes. The only difference between the two environments is
# that `DYNAMODB_ENDPOINT_URL` is unset here, which is a line the task definition does
# *not* have rather than a branch anybody added.
#
# ── The names are the code's own defaults ──────────────────────────────────────
# `headroom_budgets` and `headroom_buckets` are `DEFAULT_BUDGETS_TABLE` and
# `DEFAULT_BUCKETS_TABLE` in `headroom/db/dynamo.py`. The task definition passes them
# explicitly anyway — a deployment should not depend on a default staying put — and
# `tests/test_deploy_aws.py` holds the two to each other so a rename in either place
# turns the suite red instead of producing a gateway that creates its own empty table
# beside the one Terraform made.
#
# ── On-demand, because the plan says pennies and means it ──────────────────────
# `PAY_PER_REQUEST` has no provisioned capacity to size, no autoscaling target, and no
# floor: a stack serving a few hundred requests a day costs cents. Provisioned capacity
# would be cheaper at a scale this is nowhere near and would need a decision about
# read/write units that nothing here justifies.

resource "aws_dynamodb_table" "budgets" {
  name         = "headroom_budgets"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "scope_id"

  attribute {
    name = "scope_id"
    type = "S"
  }

  # No TTL, and that is the load-bearing asymmetry with the table below. H-032 rejects
  # DynamoDB TTL for reservations by name: it *deletes an item*, and deleting a budget
  # item because a reservation inside it expired would strand the tenant's cap and
  # destroy the evidence at the same time. Expiry here is a compensating write on the
  # refusal path, not a garbage collector.

  point_in_time_recovery {
    enabled = false
  }

  deletion_protection_enabled = false

  tags = { Name = "headroom_budgets" }
}

resource "aws_dynamodb_table" "buckets" {
  name         = "headroom_buckets"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "bucket_id"

  attribute {
    name = "bucket_id"
    type = "S"
  }

  # H-035, cashed: "the `expires_at` attribute is written on every consumption;
  # *enabling* TTL on the table is Terraform's job in Phase 9, and nothing here depends
  # on the reaper ever running." Safe precisely because an absent bucket and a full
  # bucket are the same state to the cold branch of `consume`, which is why this table
  # may be reaped and the one above may not.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }

  deletion_protection_enabled = false

  tags = { Name = "headroom_buckets" }
}
