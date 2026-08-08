"""Service-container proofs: the backing stores are up and are what the plan assumes.

Keyless, but they need a Postgres and a DynamoDB Local. Since Phase 1 they find the
compose stack on their own (H-012): after ``make up``, a bare ``make test`` runs them
against the H-006 host ports with nothing exported. Exporting ``DATABASE_URL`` /
``DYNAMODB_ENDPOINT_URL`` still overrides — and turns an unreachable store into a
failure rather than a skip, which is what keeps CI honest about its service containers.
"""

import asyncpg
import httpx
import pytest

from .support.services import (
    COMPOSE_DATABASE_URL,
    COMPOSE_DYNAMODB_ENDPOINT_URL,
    resolve_endpoint,
)

DATABASE = resolve_endpoint("DATABASE_URL", COMPOSE_DATABASE_URL)
DYNAMODB = resolve_endpoint("DYNAMODB_ENDPOINT_URL", COMPOSE_DYNAMODB_ENDPOINT_URL)


@pytest.mark.skipif(DATABASE.skip_reason is not None, reason=str(DATABASE.skip_reason))
async def test_postgres_is_reachable_and_offers_pgvector() -> None:
    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        assert await conn.fetchval("SELECT 1") == 1
        available = await conn.fetchval(
            "SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'"
        )
    finally:
        await conn.close()

    assert available == 1, "the Postgres image must ship pgvector (BUILD_PLAN L2)"


@pytest.mark.skipif(DYNAMODB.skip_reason is not None, reason=str(DYNAMODB.skip_reason))
def test_dynamodb_local_is_listening() -> None:
    response = httpx.get(DYNAMODB.url, timeout=10)

    # An unsigned GET is a malformed DynamoDB request by definition; a 400 is the
    # service answering, which is exactly what "the container is up" means here.
    assert response.status_code == 400
