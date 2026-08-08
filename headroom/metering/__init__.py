"""Metering: dated price schedules, usage parsing, and the cost ledger.

Prices carry effective-date ranges and the meter resolves the price for the
request's date; every request writes one ledger row, which is the single source of
truth for the dashboard and the experiments. Filled in Phase 3.
"""
