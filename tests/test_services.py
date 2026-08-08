"""Service-container proofs: the backing stores are up and are what the plan assumes.

Keyless, but they need endpoints, so each skips unless its env var is set. CI sets
both (the workflow's service containers); locally they run after `make up` with
`DATABASE_URL` / `DYNAMODB_ENDPOINT_URL` exported — see `.env.example`.
"""

import os

import asyncpg
import httpx
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
DYNAMODB_ENDPOINT_URL = os.environ.get("DYNAMODB_ENDPOINT_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL unset — no Postgres to talk to")
async def test_postgres_is_reachable_and_offers_pgvector() -> None:
    assert DATABASE_URL is not None
    conn = await asyncpg.connect(DATABASE_URL, timeout=10)
    try:
        assert await conn.fetchval("SELECT 1") == 1
        available = await conn.fetchval(
            "SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'"
        )
    finally:
        await conn.close()

    assert available == 1, "the Postgres image must ship pgvector (BUILD_PLAN L2)"


@pytest.mark.skipif(
    not DYNAMODB_ENDPOINT_URL, reason="DYNAMODB_ENDPOINT_URL unset — no dynamodb-local"
)
def test_dynamodb_local_is_listening() -> None:
    assert DYNAMODB_ENDPOINT_URL is not None
    response = httpx.get(DYNAMODB_ENDPOINT_URL, timeout=10)

    # An unsigned GET is a malformed DynamoDB request by definition; a 400 is the
    # service answering, which is exactly what "the container is up" means here.
    assert response.status_code == 400
