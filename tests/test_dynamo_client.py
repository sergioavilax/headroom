"""The DynamoDB boundary: lazy, credential-free by default, and one code path.

Assumption **A1** is *"``amazon/dynamodb-local`` in compose behaves like DynamoDB for
``ConditionExpression`` conditional writes via boto3 ``endpoint_url``"*, and the budget
store's contract suite converts the behavioural half of it. This file covers the
*plumbing* half — the parts that would make the Phase 9 promise ("the same code against
real DynamoDB") false if they were wrong, plus the one emulator quirk that cost real
time at this phase's gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from headroom.core.budgets import BudgetScope
from headroom.core.errors import ControlPlaneUnavailable
from headroom.db.budgets import DynamoBudgetStore
from headroom.db.dynamo import (
    BUDGETS_TABLE_ENV,
    DEFAULT_BUDGETS_TABLE,
    DYNAMODB_ENDPOINT_ENV,
    DynamoClient,
    _client_kwargs,
    budgets_table_name,
    configured_region,
    translate_dynamo_error,
)

from .support.budgets import dynamo_budget_store, require_dynamodb

WHEN = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


# --- construction is free ---------------------------------------------------------------


def test_building_a_client_opens_nothing() -> None:
    """The lazy-pool rule (H-021), one datastore over.

    CI's ``image`` job builds the container and smokes ``/healthz`` with no DynamoDB
    anywhere in the job, and ``/healthz`` is liveness only (H-000). A client that
    connected — or created a table — at construction would break both.
    """
    client = DynamoClient(endpoint_url="http://127.0.0.1:1")

    assert client.endpoint_url == "http://127.0.0.1:1"
    # No socket, no thread pool, no boto3 client until something actually asks.
    assert client._client is None
    assert client._executor is None


def test_building_a_store_opens_nothing_either() -> None:
    store = DynamoBudgetStore(DynamoClient(endpoint_url="http://127.0.0.1:1"))
    assert store.table == DEFAULT_BUDGETS_TABLE


async def test_an_unreachable_endpoint_is_503_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gateway that cannot check a budget must not fail *open* — that is how a tenant
    spends a month's cap during an outage — and it must not claim it is broken forever
    either. 503, the same answer an unreachable Postgres gets (H-020)."""
    store = DynamoBudgetStore(DynamoClient(endpoint_url="http://127.0.0.1:1"))
    try:
        with pytest.raises(ControlPlaneUnavailable):
            await store.get(BudgetScope.tenant("nobody"), when=WHEN)
    finally:
        await store.aclose()


# --- credentials --------------------------------------------------------------------------


def test_no_credential_is_invented_when_there_is_no_endpoint_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Against real DynamoDB nothing is fabricated: the deployment's own role supplies
    the credential, and a missing one fails loudly (BUILD_PLAN §0.2 invariant 3)."""
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)

    kwargs = _client_kwargs(None)

    assert "aws_access_key_id" not in kwargs
    assert "endpoint_url" not in kwargs


def test_a_dummy_credential_is_supplied_only_for_an_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)

    kwargs = _client_kwargs("http://localhost:8001")

    assert kwargs["aws_access_key_id"] == kwargs["aws_secret_access_key"]
    assert kwargs["endpoint_url"] == "http://localhost:8001"


def test_a_real_credential_in_the_environment_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAREAL")

    kwargs = _client_kwargs("http://localhost:8001")

    assert "aws_access_key_id" not in kwargs, "an operator's own credential is not overridden"


# --- the region, under either of its two names ------------------------------------------


def _no_region(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(name, raising=False)


def test_a_deployment_that_states_only_aws_region_still_gets_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Phase 10 scar, one layer below the chart that also fixes it (H-094).

    `AWS_REGION` is the name every AWS runtime documents and the only one an operator
    writing a manifest would think to set. It is *not* the name botocore's environment
    resolution reads — that is `AWS_DEFAULT_REGION`, which ECS Fargate injects for free
    and Kubernetes does not inject at all. The first cluster smoke therefore failed with
    `NoRegionError` while `AWS_REGION` was plainly set in the pod, which points an
    operator at the one thing that is already correct.

    Resolving it here means no fourth runtime has to know any of that.
    """
    _no_region(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    assert configured_region() == "us-east-1"
    assert _client_kwargs(None)["region_name"] == "us-east-1"


def test_the_other_name_works_too_and_is_not_quietly_preferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fargate and the Phase 9 runbook both set `AWS_DEFAULT_REGION`; that path must not
    regress while the new name is being taught."""
    _no_region(monkeypatch)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")

    assert _client_kwargs(None)["region_name"] == "eu-west-1"


def test_no_region_anywhere_is_left_to_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant 3's shape, applied to configuration rather than to a credential.

    A deployment that states no region must raise `NoRegionError` from botocore. Filling
    one in here would mean a misconfigured production pod silently addressing `us-east-1`
    — writing budget reservations to a table in the wrong region, and looking healthy.
    """
    _no_region(monkeypatch)

    assert configured_region() is None
    assert "region_name" not in _client_kwargs(None)


def test_an_exported_but_empty_region_is_not_a_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """`export AWS_REGION=$SOMETHING_UNSET` sets the variable to the empty string, which
    boto3 accepts and then fails on somewhere further away."""
    _no_region(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "   ")

    assert configured_region() is None


def test_the_emulator_still_has_a_region_to_sign_against(monkeypatch: pytest.MonkeyPatch) -> None:
    """DynamoDB Local validates no signature but SigV4 still needs a scope to build one,
    and a developer with no AWS configuration at all runs `make up && make test`."""
    _no_region(monkeypatch)

    assert _client_kwargs("http://localhost:8001")["region_name"] == "us-east-1"


def test_the_emulator_defers_to_a_stated_region(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_region(monkeypatch)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")

    assert _client_kwargs("http://localhost:8001")["region_name"] == "ap-south-1"


def test_the_emulator_credential_has_no_hyphen() -> None:
    """The finding this phase's gate paid for, pinned so it cannot come back.

    DynamoDB Local *does* parse the access key id out of the SigV4 credential scope and
    rejects one containing a ``-`` with ``UnrecognizedClientException: The Access Key ID
    or security token is invalid`` — an error that reads exactly like a real AWS
    authentication failure and sends you looking in entirely the wrong place. The first
    version of this constant was ``"headroom-local"`` and every DynamoDB-backed test in
    the suite failed with it.
    """
    from headroom.db.dynamo import _EMULATOR_CREDENTIALS

    assert "-" not in _EMULATOR_CREDENTIALS
    assert _EMULATOR_CREDENTIALS


async def test_the_emulator_really_accepts_it() -> None:
    """And the pin above is not merely a string check — it is asserted against the
    container, so a future image that tightens the rule fails here."""
    client = DynamoClient(endpoint_url=require_dynamodb())
    try:
        result = await client.call("list_tables", Limit=1)
        assert "TableNames" in result
    finally:
        await client.aclose()


# --- the table ------------------------------------------------------------------------------


def test_the_table_name_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BUDGETS_TABLE_ENV, "prod_budgets")
    assert budgets_table_name() == "prod_budgets"
    monkeypatch.delenv(BUDGETS_TABLE_ENV)
    assert budgets_table_name() == DEFAULT_BUDGETS_TABLE


def test_the_endpoint_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DYNAMODB_ENDPOINT_ENV, "http://elsewhere:9999")
    assert DynamoClient().endpoint_url == "http://elsewhere:9999"


async def test_the_table_is_created_on_first_use_and_only_once() -> None:
    """One code path for compose, CI, and AWS. In Phase 9 Terraform creates the table,
    the ``DescribeTable`` succeeds, and the creation branch never runs — which is why
    this is a lazy check rather than a startup step."""
    async with dynamo_budget_store() as store:
        # Nothing has used the store yet, so the table genuinely does not exist.
        with pytest.raises(ClientError) as missing:
            await store.client.call("describe_table", TableName=store.table)
        assert missing.value.response["Error"]["Code"] == "ResourceNotFoundException"

        # One ordinary read is enough to bring it into being.
        assert await store.get(BudgetScope.tenant("nobody"), when=WHEN) is None

        described = await store.client.call("describe_table", TableName=store.table)
        assert described["Table"]["TableStatus"] == "ACTIVE"
        # PAY_PER_REQUEST, so nobody has to defend a provisioned-capacity number for a
        # table whose whole traffic is one conditional write per request.
        assert described["Table"]["BillingModeSummary"]["BillingMode"] == "PAY_PER_REQUEST"
        assert described["Table"]["KeySchema"] == [{"AttributeName": "scope_id", "KeyType": "HASH"}]

        # Memoized: a second ensure is a no-op, not a second DescribeTable round trip.
        assert store.table in store.client._table_ready
        await store.client.ensure_table(store.table, partition_key="scope_id")


async def test_creating_a_table_that_already_exists_is_not_a_collision() -> None:
    """Two gateway processes starting together must not fight over it."""
    async with dynamo_budget_store() as store:
        fresh = DynamoClient(endpoint_url=store.client.endpoint_url)
        try:
            await fresh.ensure_table(store.table, partition_key="scope_id")
        finally:
            await fresh.aclose()


# --- error translation -------------------------------------------------------------------------


def test_a_throttle_is_transient_and_a_validation_error_is_not() -> None:
    """The distinction the gate depends on: "try again" must never be confused with
    "your condition failed", which is the budget's normal answer and not a fault."""
    throttled = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "UpdateItem"
    )
    invalid = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad path"}}, "UpdateItem"
    )

    assert isinstance(translate_dynamo_error(throttled), ControlPlaneUnavailable)
    assert translate_dynamo_error(invalid) is invalid


async def test_a_condition_failure_reaches_the_caller_unchanged() -> None:
    """``ConditionalCheckFailedException`` is the gate's answer, not an error. If
    ``DynamoClient.call`` translated it, every refusal would become a 503."""
    async with dynamo_budget_store() as store:
        scope = BudgetScope.tenant("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        await store.set_budget(scope, usd=Decimal("1.00"), window="monthly", when=WHEN)

        with pytest.raises(ClientError) as raised:
            await store.client.call(
                "update_item",
                TableName=store.table,
                Key={"scope_id": {"S": scope.key}},
                UpdateExpression="SET spent_picos = :v",
                ConditionExpression="attribute_not_exists(scope_id)",
                ExpressionAttributeValues={":v": {"N": "1"}},
            )
        assert raised.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
