# Four alarms that would actually page, and one that is deliberately not built.
#
# §P9 names three — 5xx rate, provider-down, budget-gate failures. The fourth is H-027's,
# which said in Phase 3 that `writer.dropped` and `writer.failed` are "numbers worth
# alerting on in Phase 9; non-zero means the ledger is now an undercount". That is a
# decision written for this phase, and the gateway already says it out loud on stdout, so
# it costs one metric filter to honour.
#
# ── Three of the four read the gateway's own log line ──────────────────────────
# Not a new metrics endpoint, not StatsD, not a sidecar. `headroom/core/log.py` has
# emitted one line of bare JSON per request since Phase 1 and every field these filters
# name has been stable since the phase that added it — which is what makes this
# observability *additive*: nothing in `headroom/` changed to be observed here.
#
# The cost of that is a coupling nobody can see from either side: rename `budget_status`
# and this alarm silently stops firing, with no test red anywhere and no error in any
# log. So `tests/test_deploy_aws.py` parses these patterns, extracts every `$.field` and
# every literal, and asserts each one is a field the gateway really emits and a value it
# can really produce. That is H-072's lesson — *pin the reader to the artifact* — applied
# to infrastructure instead of to a runbook's SQL.
#
# ── What is not alarmed, and why ───────────────────────────────────────────────
# H-032's `expired_releases` and `expired_released_picos` — "non-zero means requests are
# dying between admission and settlement" — are in-process counters on the budget store,
# reported by `GET /admin/budgets/{tenant}` and by nothing else. There is no log line to
# filter and no metric to alarm on, so an alarm here would need the gateway to start
# emitting one. That is a gateway change in a deploy phase, and it is recorded as
# follow-up work in the phase log rather than smuggled in.

resource "aws_sns_topic" "alarms" {
  name = "${var.project}-alarms"

  tags = { Name = "${var.project}-alarms" }
}

resource "aws_sns_topic_subscription" "email" {
  # No email, no subscription — the topic still exists and the alarms still change state,
  # which is a demo rather than a page. Stated in `variables.tf` so the difference is a
  # choice rather than a surprise.
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  # Both lists, on every alarm. `ok_actions` matters more than it looks: an alarm that
  # only ever mails on the way down leaves the operator to guess whether it recovered,
  # and "did it clear" is the second question every page produces.
  alarm_actions = [aws_sns_topic.alarms.arn]

  metric_namespace = "Headroom"
}

# --- 1. the 5xx rate --------------------------------------------------------------------
#
# A *rate*, not a count, and the guard is the interesting half: one failure out of one
# request is a 100% error rate and is not an incident, so the expression evaluates to zero
# below `alarm_5xx_min_requests`. Above it, the percentage of requests the load balancer
# answered 5xx to — its targets' and its own.
#
# §P8.H3's published headline is "zero caller-visible 5xx at every intensity". This alarm
# firing means something this repo publishes as impossible has happened, which is exactly
# the bar "would actually page" should be set at.

resource "aws_cloudwatch_metric_alarm" "five_xx_rate" {
  alarm_name        = "${var.project}-5xx-rate"
  alarm_description = "More than ${var.alarm_5xx_percent}% of requests answered 5xx over five minutes (evaluated above ${var.alarm_5xx_min_requests} requests)."

  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.alarm_5xx_percent
  evaluation_periods  = 1
  # Missing is good: an idle stack has no requests, no 5xx, and nothing to say. Without
  # this the alarm would sit in INSUFFICIENT_DATA whenever nobody was using it, which is
  # how an operator learns to ignore a colour.
  treat_missing_data = "notBreaching"

  metric_query {
    id = "reqs"
    metric {
      metric_name = "RequestCount"
      namespace   = "AWS/ApplicationELB"
      period      = 300
      stat        = "Sum"
      dimensions  = { LoadBalancer = aws_lb.main.arn_suffix }
    }
  }

  metric_query {
    id = "t5xx"
    metric {
      metric_name = "HTTPCode_Target_5XX_Count"
      namespace   = "AWS/ApplicationELB"
      period      = 300
      stat        = "Sum"
      dimensions  = { LoadBalancer = aws_lb.main.arn_suffix }
    }
  }

  metric_query {
    id = "e5xx"
    metric {
      # The load balancer's own 5xx — no healthy target, a target that refused the
      # connection. A gateway can be blameless and this can still be an outage.
      metric_name = "HTTPCode_ELB_5XX_Count"
      namespace   = "AWS/ApplicationELB"
      period      = 300
      stat        = "Sum"
      dimensions  = { LoadBalancer = aws_lb.main.arn_suffix }
    }
  }

  metric_query {
    id          = "rate"
    label       = "5xx %"
    expression  = "IF(reqs >= ${var.alarm_5xx_min_requests}, 100 * (FILL(t5xx, 0) + FILL(e5xx, 0)) / reqs, 0)"
    return_data = true
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = { Name = "${var.project}-5xx-rate" }
}

# --- 2. a provider is down ----------------------------------------------------------------
#
# The vocabulary is the gateway's own, and the *exclusions* are the decision. An upstream
# 4xx that is not 429 is deliberately absent: H-052 counts it as a **success**, because a
# 400 is a healthy provider correctly refusing a bad request, and counting it would let
# one tenant's malformed payloads page an operator about somebody else's outage. So this
# filter inherits the circuit breaker's definition of ill health rather than inventing a
# second one.
#
# `failover_error` appears three times over because a request that *recovered* still
# witnessed a failure: the primary was passed over, the fallback served, the caller saw a
# 200 — and a provider being down is exactly what happened.

resource "aws_cloudwatch_log_metric_filter" "provider_failures" {
  name           = "${var.project}-provider-failures"
  log_group_name = aws_cloudwatch_log_group.gateway.name

  pattern = <<-EOT
    { ($.outcome = "upstream_timeout") || ($.outcome = "upstream_unavailable") || ($.error_reason = "upstream_status_5*") || ($.failover_error = "upstream_timeout") || ($.failover_error = "upstream_unavailable") || ($.failover_error = "upstream_status_5*") || ($.failover_error = "breaker_open") }
  EOT

  metric_transformation {
    name      = "ProviderFailures"
    namespace = local.metric_namespace
    value     = "1"
    # So the metric has a datapoint of 0 in periods that saw traffic and no failures,
    # rather than no datapoint at all. An alarm with a real zero is an alarm somebody
    # believes.
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "provider_down" {
  alarm_name        = "${var.project}-provider-down"
  alarm_description = "${var.alarm_provider_failures} or more provider failures in five minutes — timeouts, unreachable upstreams, upstream 5xx, or a tripped breaker."

  namespace           = local.metric_namespace
  metric_name         = aws_cloudwatch_log_metric_filter.provider_failures.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.alarm_provider_failures
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = { Name = "${var.project}-provider-down" }
}

# --- 3. the budget gate refused something ---------------------------------------------------
#
# Threshold 1, and it is deliberately the most sensitive alarm here. A 402 means a
# tenant's traffic is being dropped on purpose — the gate working exactly as designed —
# and that is a thing an operator should learn about within five minutes rather than at
# the end of the month. `budget_status` rather than `outcome`, because the field says what
# the gate decided regardless of what the request went on to do.

resource "aws_cloudwatch_log_metric_filter" "budget_refusals" {
  name           = "${var.project}-budget-refusals"
  log_group_name = aws_cloudwatch_log_group.gateway.name
  pattern        = "{ $.budget_status = \"exceeded\" }"

  metric_transformation {
    name          = "BudgetRefusals"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "budget_refusals" {
  alarm_name        = "${var.project}-budget-refusals"
  alarm_description = "The budget gate refused at least one request in five minutes: a tenant is at its cap and its traffic is being dropped."

  namespace           = local.metric_namespace
  metric_name         = aws_cloudwatch_log_metric_filter.budget_refusals.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = { Name = "${var.project}-budget-refusals" }
}

# --- 4. the ledger has become an undercount ---------------------------------------------------
#
# H-027's alarm, six phases later. The ledger writer is *at most once, in process, best
# effort*: a full queue drops rows and counts them, a failing store logs and continues, an
# ungraceful stop loses whatever was queued. Each of those already writes one line of JSON
# with an `event` field, and each of them means the invoice is now smaller than the truth
# — which is the one failure in this system that looks like good news.

resource "aws_cloudwatch_log_metric_filter" "ledger_rows_lost" {
  name           = "${var.project}-ledger-rows-lost"
  log_group_name = aws_cloudwatch_log_group.gateway.name

  pattern = <<-EOT
    { ($.event = "ledger_row_dropped") || ($.event = "ledger_write_failed") || ($.event = "ledger_shutdown_incomplete") }
  EOT

  metric_transformation {
    name          = "LedgerRowsLost"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "ledger_rows_lost" {
  alarm_name        = "${var.project}-ledger-rows-lost"
  alarm_description = "A ledger row was dropped, failed to write, or was lost at shutdown. Spend is now under-reported and the log line is the only remaining copy."

  namespace           = local.metric_namespace
  metric_name         = aws_cloudwatch_log_metric_filter.ledger_rows_lost.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = { Name = "${var.project}-ledger-rows-lost" }
}
