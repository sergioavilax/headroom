"""Reaching DynamoDB Local for the token-bucket tests, on the H-012 terms.

The rules are ``tests/support/budgets.py``'s, unchanged, because they are the repo's
rather than the budget's: an *inferred* endpoint with nothing listening skips loudly, an
*explicit* one that is unreachable fails, and each test gets its own table so that a
``-sharedDb`` emulator cannot leak one test's buckets into the next.

The bucket store's reason for needing the container is the same as the budget store's and
it is worth restating: the claim is that a burst of concurrent consumptions cannot
oversubscribe a bucket, and a dict has nothing to concurrently subscribe.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from uuid import uuid4

from headroom.db.buckets import DynamoRateLimitStore
from headroom.db.dynamo import DynamoClient

from .budgets import require_dynamodb

__all__ = ["dynamo_bucket_store"]


@contextlib.asynccontextmanager
async def dynamo_bucket_store() -> AsyncIterator[DynamoRateLimitStore]:
    """A ``DynamoRateLimitStore`` on its own throwaway table."""
    endpoint = require_dynamodb()
    table = f"headroom_buckets_test_{uuid4().hex[:12]}"
    client = DynamoClient(endpoint_url=endpoint)
    store = DynamoRateLimitStore(client, table=table)
    try:
        yield store
    finally:
        with contextlib.suppress(Exception):
            await client.call("delete_table", TableName=table)
        await client.aclose()
