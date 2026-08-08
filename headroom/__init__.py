"""Headroom — an LLM gateway and control plane.

Every request from an application to a model provider flows through here, which is
what makes virtual keys, budgets, rate limits, caching, failover, and per-tenant cost
attribution possible in one place. Built phase by phase from ``BUILD_PLAN.md``.
"""

__version__ = "0.1.0"
