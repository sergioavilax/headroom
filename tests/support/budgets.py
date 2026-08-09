"""Reaching DynamoDB Local from a test, on the H-012 terms.

The budget store is the one part of Headroom whose correctness cannot be demonstrated
against a dict: the claim is that a hundred concurrent admissions cannot oversubscribe a
cap, and a dict has nothing to concurrently subscribe. So the contract suite runs its
DynamoDB half — and the stampede runs *only* its DynamoDB half — against the container
``make up`` starts.

The rule for finding it is Phase 1's, unchanged (docs/DECISIONS.md H-012):

* endpoint **inferred** and nothing listening → skip, saying where it looked. A fresh
  clone with no stack up is not a broken repo.
* endpoint **explicit** and nothing listening → fail. CI states it in its workflow env,
  and a silent skip would make that job a liar about its own service container.

**Each test gets its own table.** DynamoDB Local runs ``-sharedDb``, so every test
process addresses one dataset; a shared table would make ``Scan``-based assertions
depend on what else ran. A table per test costs a few milliseconds against an in-memory
JVM and buys assertions that can be exact.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from headroom.db.budgets import DynamoBudgetStore
from headroom.db.dynamo import DYNAMODB_ENDPOINT_ENV, DynamoClient

from .services import COMPOSE_DYNAMODB_ENDPOINT_URL, resolve_endpoint

__all__ = ["dynamo_budget_store", "require_dynamodb"]


def require_dynamodb() -> str:
    """The DynamoDB Local endpoint, or the right kind of non-result."""
    endpoint = resolve_endpoint(DYNAMODB_ENDPOINT_ENV, COMPOSE_DYNAMODB_ENDPOINT_URL)
    reason = endpoint.skip_reason
    if reason is not None:
        pytest.skip(reason)
    if not endpoint.reachable:
        pytest.fail(
            f"{DYNAMODB_ENDPOINT_ENV} was set to {endpoint.url} and nothing is listening "
            f"there — the budget tests cannot silently skip when someone stated the "
            f"store is running"
        )
    return endpoint.url


@contextlib.asynccontextmanager
async def dynamo_budget_store() -> AsyncIterator[DynamoBudgetStore]:
    """A ``DynamoBudgetStore`` on its own throwaway table."""
    endpoint = require_dynamodb()
    table = f"headroom_budgets_test_{uuid4().hex[:12]}"
    client = DynamoClient(endpoint_url=endpoint)
    store = DynamoBudgetStore(client, table=table)
    try:
        yield store
    finally:
        with contextlib.suppress(Exception):
            await client.call("delete_table", TableName=table)
        await client.aclose()
