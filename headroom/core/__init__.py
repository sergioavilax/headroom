"""Cross-cutting core: request context, tracing/logging, config, storage interfaces.

The request context (request id, tenant, timings) threads through everything from
Phase 1 so that metering and the dashboard never have to retrofit tracing.
"""
