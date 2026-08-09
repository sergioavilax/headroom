"""The DynamoDB client, its table, and the one awkward fact about boto3.

BUILD_PLAN L2 puts token buckets and budget reservations on DynamoDB conditional
writes, with ``amazon/dynamodb-local`` in compose so the code path is identical to the
cloud one (assumption **A1**). This module is where "identical" is made true: one
client factory, one table definition, and no branch anywhere that asks whether it is
talking to a container or to AWS.

**boto3 is synchronous, and that is not a detail here.** Every call below is a blocking
socket round trip, and the gateway is an asyncio process whose whole product is
first-token latency. Calling boto3 directly from a coroutine would block the event loop
for the duration of the round trip — and would do something worse to this phase
specifically: it would *serialise* the budget gate. Sixty-four concurrent requests would
queue behind one another, the reservation race would never occur, and the stampede test
would pass against a design that had never actually been raced. So every call is
dispatched to a small thread pool, and the concurrency the gate is tested under is real.

An async client (``aioboto3``) would remove the pool, and is not worth a dependency the
plan did not budget for: two thread hops per request cost tens of microseconds against a
network call measured in milliseconds.

**Credentials.** DynamoDB Local requires *a* credential to sign against and validates
none of it (compose supplies the string ``local``). Rather than making every developer
export two meaningless variables, this module supplies dummies — but only when an
explicit ``DYNAMODB_ENDPOINT_URL`` says an emulator is being addressed *and* the
environment has no credential of its own. Against real DynamoDB there is no endpoint
override, nothing is invented, and a missing credential fails loudly, which is what
BUILD_PLAN §0.2 invariant 3 requires of everything on this path.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final, TypeVar

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from headroom.core.errors import ControlPlaneUnavailable

__all__ = [
    "BUCKETS_TABLE_ENV",
    "BUDGETS_TABLE_ENV",
    "DYNAMODB_ENDPOINT_ENV",
    "DynamoClient",
    "buckets_table_name",
    "budgets_table_name",
    "dynamodb_endpoint_url",
]

#: Where DynamoDB is. Set by compose and by CI to the local container; unset in Phase
#: 9, where boto3 resolves the regional endpoint itself.
DYNAMODB_ENDPOINT_ENV: Final = "DYNAMODB_ENDPOINT_URL"

#: The table holding budget scopes. One name, one table, both locally and on AWS.
BUDGETS_TABLE_ENV: Final = "HEADROOM_BUDGETS_TABLE"
DEFAULT_BUDGETS_TABLE: Final = "headroom_budgets"

#: The table holding token buckets (Phase 4b). A *second* table rather than a second
#: item shape in the first one: the two have nothing in common but a datastore. Budget
#: items are long-lived, one per budgeted tenant, and must never be garbage-collected;
#: bucket items are ephemeral, one per scope per dimension, and are *safe* to delete
#: because an absent bucket and a full bucket are the same state (docs/DECISIONS.md
#: H-035). One table cannot have both retention policies.
BUCKETS_TABLE_ENV: Final = "HEADROOM_BUCKETS_TABLE"
DEFAULT_BUCKETS_TABLE: Final = "headroom_buckets"

#: Region when nothing states one. Only reached on the emulator path — a real
#: deployment has a region from its environment or its task metadata.
_DEFAULT_REGION: Final = "us-east-1"

#: Ignored by DynamoDB Local, which never validates a *signature*. Present so a
#: developer with no AWS configuration at all can run `make up && make test`.
#:
#: **No hyphen, and that is not cosmetic.** DynamoDB Local does parse the access key id
#: out of the SigV4 credential scope, and rejects one containing a ``-`` with
#: ``UnrecognizedClientException: The Access Key ID or security token is invalid`` —
#: which reads exactly like a real authentication failure against real AWS and sends an
#: operator looking in entirely the wrong place. Found at this phase's gate, with
#: ``"headroom-local"``; ``tests/test_dynamo_client.py`` pins it so it cannot come back.
_EMULATOR_CREDENTIALS: Final = "headroomlocal"

#: Threads for the blocking boto3 calls. Sized so a burst of concurrent admissions
#: really is concurrent — the stampede test is meaningless if the pool serialises it —
#: and bounded so a stuck endpoint cannot spawn threads without limit.
DEFAULT_MAX_WORKERS: Final = 32

#: One attempt per call from botocore's side. Retries are the *gate's* business: a
#: conditional write that failed its condition must not be retried blindly, and a
#: refusal that botocore quietly retried would double-count nothing but would waste the
#: latency budget of a request that is about to be refused anyway.
_BOTO_CONFIG: Final = Config(
    retries={"max_attempts": 2, "mode": "standard"},
    connect_timeout=3,
    read_timeout=5,
)

T = TypeVar("T")


def dynamodb_endpoint_url() -> str | None:
    """The configured endpoint, or ``None`` to let boto3 resolve the regional one."""
    return os.environ.get(DYNAMODB_ENDPOINT_ENV) or None


def budgets_table_name() -> str:
    """The budgets table's name."""
    return os.environ.get(BUDGETS_TABLE_ENV) or DEFAULT_BUDGETS_TABLE


def buckets_table_name() -> str:
    """The token-bucket table's name."""
    return os.environ.get(BUCKETS_TABLE_ENV) or DEFAULT_BUCKETS_TABLE


def _client_kwargs(endpoint_url: str | None) -> dict[str, Any]:
    """Constructor arguments for the DynamoDB client. See the module docstring."""
    kwargs: dict[str, Any] = {"config": _BOTO_CONFIG}
    if endpoint_url is not None:
        kwargs["endpoint_url"] = endpoint_url
        kwargs.setdefault("region_name", os.environ.get("AWS_REGION") or _DEFAULT_REGION)
        if not os.environ.get("AWS_ACCESS_KEY_ID"):
            kwargs["aws_access_key_id"] = _EMULATOR_CREDENTIALS
            kwargs["aws_secret_access_key"] = _EMULATOR_CREDENTIALS
    return kwargs


def translate_dynamo_error(exc: Exception) -> Exception:
    """Map a DynamoDB failure onto the gateway's error taxonomy.

    Anything about *reaching* DynamoDB is 503 ``control_plane_unavailable``, the same
    answer an unreachable Postgres gets (H-020): the request was well formed, the
    gateway is configured correctly, and the condition is transient. And a gateway that
    cannot check a budget must **not** fail open — failing open here is how a tenant
    spends a month's cap during an outage. Everything else (a validation error, a
    condition failure) is the caller's own code path and is handled where it is raised.
    """
    if isinstance(exc, BotoCoreError | ConnectionError | TimeoutError | OSError):
        return ControlPlaneUnavailable(f"cannot reach the budget store: {exc}")
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _UNAVAILABLE_CODES:
            return ControlPlaneUnavailable(f"the budget store is unavailable: {exc}")
    return exc


#: Server-side conditions that mean "try again", not "you are wrong".
_UNAVAILABLE_CODES: Final = frozenset(
    {
        "InternalServerError",
        "ProvisionedThroughputExceededException",
        "RequestLimitExceeded",
        "ServiceUnavailable",
        "ThrottlingException",
    }
)


class DynamoClient:
    """A lazily-built boto3 client, its thread pool, and its table.

    Lazy for the same reason ``headroom/db/pool.py`` is (H-021): building a gateway must
    never require a reachable backing service. CI's ``image`` job smokes ``/healthz``
    with no DynamoDB in the job at all, and ``/healthz`` is liveness only — a process
    that connected to three datastores at import time could claim neither.
    """

    __slots__ = ("_client", "_endpoint_url", "_executor", "_lock", "_max_workers", "_table_ready")

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        self._endpoint_url = endpoint_url if endpoint_url is not None else dynamodb_endpoint_url()
        self._max_workers = max_workers
        self._client: Any = None
        self._executor: ThreadPoolExecutor | None = None
        self._table_ready: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def endpoint_url(self) -> str | None:
        return self._endpoint_url

    # --- the blocking boundary -------------------------------------------------------

    def _raw(self) -> Any:
        """The boto3 client, built on first use. Clients are thread-safe; sessions are
        not, so one client is shared across the pool and no session is."""
        if self._client is None:
            self._client = boto3.client("dynamodb", **_client_kwargs(self._endpoint_url))
        return self._client

    def _pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers, thread_name_prefix="headroom-dynamo"
            )
        return self._executor

    async def call(self, operation: str, /, **kwargs: Any) -> dict[str, Any]:
        """Run one DynamoDB operation off the event loop.

        ``ClientError`` is *not* translated here: a ``ConditionalCheckFailedException``
        is the budget gate's normal answer, not a fault, and the caller has to see it
        unchanged. Transport failures are translated, because nothing above this line
        should have to know what a ``BotoCoreError`` is.
        """
        loop = asyncio.get_running_loop()
        client = self._raw()

        def invoke() -> Any:
            return getattr(client, operation)(**kwargs)

        try:
            result = await loop.run_in_executor(self._pool(), invoke)
        except ClientError:
            raise
        except Exception as exc:
            translated = translate_dynamo_error(exc)
            if translated is exc:
                raise
            raise translated from exc
        return dict(result)

    async def run(self, work: Callable[[], T]) -> T:
        """Run an arbitrary blocking callable on the same pool. Used by table setup."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool(), work)

    # --- the table -------------------------------------------------------------------

    async def ensure_table(self, table: str, *, partition_key: str) -> None:
        """Create a table if it is not there. Idempotent, memoized per client.

        One code path for compose, CI, and AWS. In Phase 9 the tables are created by
        Terraform, so the ``DescribeTable`` succeeds and nothing here creates anything —
        which is why this is a lazy check rather than a startup step, and why the
        creation branch is written to survive losing a race with another process.

        ``partition_key`` is named by the caller rather than defaulted, because there
        are now two tables with two different keys (``scope_id`` for budgets,
        ``bucket_id`` for token buckets) and a default would silently create the wrong
        schema for whichever store forgot to pass it.
        """
        if table in self._table_ready:
            return
        async with self._lock:
            if table in self._table_ready:
                return
            await self._create_if_missing(table, partition_key)
            self._table_ready.add(table)

    async def _create_if_missing(self, table: str, partition_key: str) -> None:
        try:
            await self.call("describe_table", TableName=table)
            return
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise translate_dynamo_error(exc) from exc

        try:
            await self.call(
                "create_table",
                TableName=table,
                AttributeDefinitions=[{"AttributeName": partition_key, "AttributeType": "S"}],
                KeySchema=[{"AttributeName": partition_key, "KeyType": "HASH"}],
                # On demand. These tables' traffic is a handful of writes per request and
                # nothing else; provisioning capacity for them would be a number nobody
                # can defend, and Phase 9's cost note says "pennies" for this reason.
                BillingMode="PAY_PER_REQUEST",
            )
        except ClientError as exc:
            # Another process got there first. That is a success, not a collision.
            if exc.response.get("Error", {}).get("Code") != "ResourceInUseException":
                raise translate_dynamo_error(exc) from exc

        await self.run(lambda: self._raw().get_waiter("table_exists").wait(TableName=table))

    # --- lifecycle ---------------------------------------------------------------------

    async def aclose(self) -> None:
        """Shut the pool down. Safe to call more than once."""
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False)
        self._client = None
        self._table_ready.clear()
