"""Admission policy: virtual keys, rate limits, budgets, routing and failover.

Keys and tenancy land in Phase 2; token buckets and reservation-based budget gates
on DynamoDB conditional writes in Phase 4; routing/failover chains in Phase 6.
"""
