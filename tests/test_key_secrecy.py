"""The plaintext key appears in the creation response and **nowhere else**.

This is the gate item BUILD_PLAN spells out and the one property of Phase 2 that cannot
be added later: once a system has ever been able to show a key back, every operator
assumes it can, and the storage design that made it possible is load-bearing by the time
anybody objects.

So the claim is checked four ways, in the four places a secret actually leaks:

1. **The creation response** — it is there, exactly once, and it is a real key.
2. **Every other admin response** — the shape has no field for it, checked against the
   model rather than against one sampled response.
3. **The store** — every string held anywhere in it is searched, including the ones a
   future field might add.
4. **The logs** — every record the gateway emitted while a key was minted *and* used.

The stored ``key_prefix`` is a deliberate exception and is called out as one:
``hk_`` plus 8 of the 43 secret characters, kept so a key can be recognised in a list
(docs/DECISIONS.md H-017). The tests below assert that the *rest* — the ~208 bits that
matter — appears nowhere at all.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from headroom.api.admin import KeyCreated, KeyView
from headroom.core.log import PACKAGE_LOGGER, REQUEST_LOGGER, configure_logging
from headroom.policy.keys import DISPLAY_PREFIX_LEN, display_prefix, hash_key, mint_key
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness


class _CaptureEverything(logging.Handler):
    """Every record the ``headroom`` logger tree emits, formatted."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def all_logs() -> Iterator[_CaptureEverything]:
    configure_logging()
    handler = _CaptureEverything()
    PACKAGE_LOGGER.addHandler(handler)
    REQUEST_LOGGER.addHandler(handler)
    try:
        yield handler
    finally:
        PACKAGE_LOGGER.removeHandler(handler)
        REQUEST_LOGGER.removeHandler(handler)


def strings_in(value: Any) -> Iterator[str]:
    """Every string anywhere inside a nested structure or a dataclass."""
    if isinstance(value, str):
        yield value
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from strings_in(getattr(value, field.name))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings_in(key)
            yield from strings_in(item)
    elif isinstance(value, list | tuple | set):
        for item in value:
            yield from strings_in(item)


def store_strings(harness: GatewayHarness) -> list[str]:
    """Everything the store holds, however it holds it.

    Reaching into ``__slots__`` on purpose: the point is to search fields nobody has
    thought of yet, including ones a future phase adds, rather than the ones this test
    remembers to name.
    """
    store = harness.store
    found: list[str] = []
    for slot in getattr(type(store), "__slots__", ()):
        found.extend(strings_in(getattr(store, slot, None)))
    return found


# --- 1. the creation response -------------------------------------------------------


async def test_the_creation_response_carries_a_usable_plaintext_key(
    gateway: GatewayHarness,
) -> None:
    response = await gateway.admin(
        "POST", "/admin/keys", json={"tenant_id": gateway.tenant.id, "name": "svc"}
    )

    plaintext = response.json()["key"]
    assert plaintext.startswith("hk_")
    assert len(plaintext) > DISPLAY_PREFIX_LEN

    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    used = await gateway.post("/v1/messages", anthropic_request(), script="ok", api_key=plaintext)
    assert used.status_code == 200, "the one copy handed out has to actually work"


# --- 2. every other response --------------------------------------------------------


def test_only_the_creation_model_has_a_field_for_a_key() -> None:
    """Structural, not incidental: ``GET`` cannot leak what its model cannot hold."""
    assert "key" not in KeyView.model_fields
    assert "key" in KeyCreated.model_fields


async def test_no_read_endpoint_returns_the_key(gateway: GatewayHarness) -> None:
    created = await gateway.admin(
        "POST", "/admin/keys", json={"tenant_id": gateway.tenant.id, "name": "svc"}
    )
    plaintext = created.json()["key"]
    key_id = created.json()["id"]

    responses = [
        await gateway.admin("GET", f"/admin/keys/{key_id}"),
        await gateway.admin("GET", "/admin/keys"),
        await gateway.admin("GET", "/admin/keys", params={"tenant_id": gateway.tenant.id}),
        await gateway.admin("PATCH", f"/admin/keys/{key_id}", json={"name": "renamed"}),
        await gateway.admin("DELETE", f"/admin/keys/{key_id}"),
    ]

    secret = plaintext[DISPLAY_PREFIX_LEN:]
    for response in responses:
        assert response.status_code == 200
        assert '"key":' not in response.text
        assert plaintext not in response.text
        assert secret not in response.text


# --- 3. the store -------------------------------------------------------------------


async def test_the_store_holds_the_hash_and_a_short_prefix_and_nothing_else(
    gateway: GatewayHarness,
) -> None:
    created = await gateway.admin(
        "POST", "/admin/keys", json={"tenant_id": gateway.tenant.id, "name": "svc"}
    )
    plaintext = created.json()["key"]

    held = store_strings(gateway)

    assert plaintext not in held, "the plaintext key reached the store"
    # And not as a substring of anything either — a repr, a cache entry, a log buffer.
    blob = "\n".join(held)
    assert plaintext not in blob
    assert plaintext[DISPLAY_PREFIX_LEN:] not in blob, "the secret tail is recoverable"

    # What *is* there: the hash, and the 11-character display prefix, deliberately.
    assert hash_key(plaintext) in held
    assert display_prefix(plaintext) in held
    assert len(display_prefix(plaintext)) == DISPLAY_PREFIX_LEN == 11


async def test_the_auth_cache_is_keyed_by_hash_not_by_plaintext(
    gateway: GatewayHarness,
) -> None:
    """An auth cache is exactly the sort of long-lived dict that ends up in a heap dump."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    cache = gateway.authenticator.cache
    assert len(cache) == 1
    held = list(strings_in(cache._by_hash))
    assert gateway.api_key not in held
    assert hash_key(gateway.api_key) in held


# --- 4. the logs --------------------------------------------------------------------


async def test_nothing_the_gateway_logs_contains_a_key(
    gateway: GatewayHarness, all_logs: _CaptureEverything
) -> None:
    """Both halves: minting a key, and then using it on a proxied request."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    created = await gateway.admin(
        "POST", "/admin/keys", json={"tenant_id": gateway.tenant.id, "name": "svc"}
    )
    plaintext = created.json()["key"]
    await gateway.post("/v1/messages", anthropic_request(), script="ok", api_key=plaintext)
    await gateway.post("/v1/messages", anthropic_request(), api_key=mint_key())  # a 401

    assert all_logs.lines, "the capture itself has to be working"
    everything = "\n".join(all_logs.lines)
    assert plaintext not in everything
    assert plaintext[DISPLAY_PREFIX_LEN:] not in everything


async def test_the_request_log_line_carries_the_tenant_not_the_credential(
    gateway: GatewayHarness, all_logs: _CaptureEverything
) -> None:
    """The Phase 3 hand-off: the ledger is keyed on identity, never on the secret."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    line = json.loads(all_logs.lines[-1])
    assert line["tenant_id"] == gateway.tenant.id
    assert line["key_id"] == gateway.key.id
    assert gateway.api_key not in json.dumps(line)


async def test_an_error_message_about_scope_names_the_prefix_not_the_key(
    gateway: GatewayHarness,
) -> None:
    """The 403 body is the easiest place to leak a key by being helpful."""
    plaintext = mint_key()
    key = await gateway.store.create_key(
        tenant_id=gateway.tenant.id,
        name="scoped",
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
        allowed_models=("nothing-matches",),
    )
    assert key is not None

    response = await gateway.post("/v1/messages", anthropic_request(), api_key=plaintext)

    assert response.status_code == 403
    assert plaintext not in response.text
    assert plaintext[DISPLAY_PREFIX_LEN:] not in response.text
    assert display_prefix(plaintext) in response.text, "identifiable without being usable"
