"""The Lambda entry point — everything about running :mod:`headroom.rollup` on AWS.

Small on purpose. The aggregation is a store method, the day arithmetic is
:func:`headroom.rollup.resolve_days`, and both are asserted by the keyless suite; what
is left here is the three things that are genuinely about the runtime — where the
database URL comes from, how the event becomes a call, and what gets written to
CloudWatch Logs.

**The connection string is fetched, never configured.** BUILD_PLAN §0.2 invariant 3 puts
secrets in Secrets Manager and keeps them out of "a task definition's plain
environment"; a Lambda's environment is the same thing wearing a different name, and it
is additionally visible in ``GetFunctionConfiguration`` and in Terraform state. So the
environment carries an **ARN** — which is not a secret — and the function reads the
value at invocation. ``DATABASE_URL`` still wins when it is set, which is what lets the
identical code run against compose from a terminal.

**Nothing is cached between invocations.** A warm Lambda holding a connection string
across a credential rotation is a nightly job that starts failing at 03:00 for reasons
nobody can see. This one runs twice a day at most, is cold essentially every time, and
one ``GetSecretValue`` is free.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3

from headroom.db.ledger import PostgresLedgerStore
from headroom.db.migrate import database_url as configured_database_url
from headroom.rollup import RollupSummary, resolve_days, run_rollup

__all__ = ["DATABASE_URL_SECRET_ARN_ENV", "database_url", "handler"]

#: The ARN of the Secrets Manager secret whose value is the whole ``postgresql://…``
#: URL. An ARN, not a value: it identifies a secret without being one, so it is safe in
#: a function's environment, in Terraform state, and in a screenshot.
DATABASE_URL_SECRET_ARN_ENV = "DATABASE_URL_SECRET_ARN"


def database_url(env: dict[str, str] | None = None) -> str:
    """The connection string, in three steps: the environment, the secret, the default.

    1. ``DATABASE_URL``, which is what every local run and every test uses — so the
       terminal path never touches AWS at all.
    2. The secret named by :data:`DATABASE_URL_SECRET_ARN_ENV`, fetched now. This is the
       deployed function's branch, and the only one that needs an AWS credential.
    3. Neither: the repo-wide compose default, exactly as ``make migrate`` and the
       gateway's own pool resolve it. So ``python -m headroom.rollup`` works against a
       running ``make up`` with nothing exported, which is the ergonomics every other
       entry point in this repo has.

    Step 3 is unreachable in the deployed function because Terraform always sets the ARN
    — and that is asserted rather than asserted-about:
    ``tests/test_deploy_aws.py::test_the_rollup_lambda_is_given_the_secret_arn_the_handler_reads``
    parses the function's ``environment`` block and holds it to the constant above.
    """
    environ = os.environ if env is None else env
    direct = environ.get("DATABASE_URL")
    if direct:
        return direct
    arn = environ.get(DATABASE_URL_SECRET_ARN_ENV)
    if arn:
        client = boto3.client("secretsmanager")
        secret: str = client.get_secret_value(SecretId=arn)["SecretString"]
        return secret
    return configured_database_url()


async def run(event: dict[str, Any] | None, *, url: str | None = None) -> RollupSummary:
    """Resolve the days, roll them up, close the pool. The whole function."""
    store = PostgresLedgerStore(url=url or database_url())
    try:
        return await run_rollup(store, resolve_days(event, datetime.now(UTC)))
    finally:
        # A Lambda that leaves a pool open leaks a connection into the freeze, and RDS
        # counts it against `max_connections` until the container is reaped.
        await store.aclose()


def handler(event: dict[str, Any] | None = None, context: object = None) -> dict[str, Any]:
    """AWS Lambda entry point. Returns the summary; also emits it as one JSON line.

    Both, deliberately. The return value is what ``aws lambda invoke`` prints on the
    operator's terminal during the Phase 9 gate; the log line is what survives in
    CloudWatch afterwards, in the same one-line-of-JSON shape every request the gateway
    serves is recorded in (``headroom/core/log.py``), so one ``jq`` reads both.
    """
    summary = asyncio.run(run(event))
    print(json.dumps(summary.as_dict(), separators=(",", ":")))
    return summary.as_dict()
