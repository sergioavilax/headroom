"""The three values a request is looked up by, and what does and does not move them.

The exact key's job is to be *paranoid*: any difference in the request that could
possibly change the answer must change the hash. The context hash's job is narrower and
sharper — it must move for every field except the one the semantic layer is allowed to
vary. These tests are mostly a list of things that must not collide.
"""

from __future__ import annotations

from typing import Any

import pytest

from headroom.cache.keys import (
    canonical_json,
    context_hash,
    namespace_for,
    normalise_probe,
    request_hash,
)
from headroom.core.cache import TRANSPORT_BODY, TRANSPORT_STREAM, CacheNamespace
from headroom.dialects.anthropic import ANTHROPIC
from headroom.dialects.openai import OPENAI
from tests.support.fixtures import anthropic_request, openai_request

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


def ns(
    *,
    tenant: str = TENANT,
    dialect: str = "anthropic",
    model: str = "mock-model-1",
    stream: bool = False,
) -> CacheNamespace:
    return namespace_for(tenant_id=tenant, dialect=dialect, model=model, stream=stream)


# --- canonicalisation ------------------------------------------------------------------


def test_key_order_and_whitespace_do_not_change_the_canonical_form() -> None:
    assert canonical_json({"b": 1, "a": {"d": 2, "c": 3}}) == canonical_json(
        {"a": {"c": 3, "d": 2}, "b": 1}
    )


def test_a_literal_and_an_escaped_non_ascii_character_canonicalise_the_same() -> None:
    """The H-016 observation, pointed the other way.

    On the wire the difference between ``ö`` and ``\\u00f6`` is load-bearing and the proxy
    preserves it byte for byte. In a *key* it is noise: the two are the same string, and
    two clients whose JSON encoders disagree about non-ASCII must not miss each other's
    cache entries.
    """
    literal = canonical_json({"text": "Björk"})
    escaped = canonical_json({"text": "Björk"})
    assert literal == escaped
    assert "Björk".encode() in literal


def test_the_canonical_form_carries_no_whitespace() -> None:
    assert b" " not in canonical_json({"a": 1, "b": [1, 2]})


# --- the exact key ---------------------------------------------------------------------


def test_the_same_request_hashes_the_same_every_time() -> None:
    body = anthropic_request()
    assert request_hash(ns(), body) == request_hash(ns(), dict(body))


def test_two_tenants_never_share_an_exact_key() -> None:
    """Isolation's first mechanism. The second is the SQL predicate; see
    ``tests/test_cache_isolation.py``, which removes both at once."""
    body = anthropic_request()
    assert request_hash(ns(), body) != request_hash(ns(tenant=OTHER_TENANT), body)


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"temperature": 0.1}, id="temperature"),
        pytest.param({"max_tokens": 65}, id="max_tokens"),
        pytest.param({"system": "you are terse"}, id="system"),
        pytest.param({"top_p": 0.9}, id="top_p"),
        pytest.param({"stop_sequences": ["\n"]}, id="stop_sequences"),
        # A field this gateway has never heard of still moves the key. That is the whole
        # value of "nothing is dropped": the rule survives the next API version.
        pytest.param({"some_future_field": {"nested": [1, 2]}}, id="an_unknown_field"),
        pytest.param({"metadata": {"user_id": "u1"}}, id="metadata"),
    ],
)
def test_any_difference_in_the_body_moves_the_exact_key(changed: dict[str, object]) -> None:
    base = anthropic_request()
    assert request_hash(ns(), base) != request_hash(ns(), {**base, **changed})


def test_a_different_question_moves_the_exact_key() -> None:
    assert request_hash(ns(), anthropic_request(text="one")) != request_hash(
        ns(), anthropic_request(text="two")
    )


def test_dialect_model_and_transport_are_all_in_the_namespace() -> None:
    body = anthropic_request()
    base = request_hash(ns(), body)
    assert base != request_hash(ns(dialect="openai"), body)
    assert base != request_hash(ns(model="mock-model-2"), body)
    assert base != request_hash(ns(stream=True), body)


def test_a_streaming_request_and_a_body_request_are_different_entries() -> None:
    """Transport is part of the key, not a rendering option (H-043).

    An entry is replayed as the exact bytes the provider sent, so the two transports are
    two different stored things and neither is ever converted into the other.
    """
    assert ns(stream=True).transport == TRANSPORT_STREAM
    assert ns(stream=False).transport == TRANSPORT_BODY


# --- the context hash -------------------------------------------------------------------


def test_the_context_hash_ignores_the_question_and_nothing_else() -> None:
    """The one field similarity is allowed to move, and the proof that it is the only one."""
    one = ANTHROPIC.cache_probe(anthropic_request(text="what did Radiohead earn?"))
    two = ANTHROPIC.cache_probe(anthropic_request(text="how much did Coldplay make?"))
    assert one is not None and two is not None
    assert context_hash(ns(), one.redacted) == context_hash(ns(), two.redacted)


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"system": "you are terse"}, id="system_prompt"),
        pytest.param({"temperature": 0.15}, id="temperature"),
        pytest.param({"max_tokens": 65}, id="max_tokens"),
        pytest.param({"top_k": 4}, id="top_k"),
    ],
)
def test_everything_but_the_question_moves_the_context_hash(changed: dict[str, Any]) -> None:
    base = ANTHROPIC.cache_probe(anthropic_request())
    other = ANTHROPIC.cache_probe(anthropic_request(**changed))
    assert base is not None and other is not None
    assert context_hash(ns(), base.redacted) != context_hash(ns(), other.redacted)


def test_an_openai_system_message_moves_the_context_hash() -> None:
    """The asymmetry that makes ``cache_probe`` a dialect method rather than a shared one.

    Anthropic's system prompt is a top-level field, the OpenAI dialect's is a message.
    A single implementation that blanked "the messages" would drop the second one out of
    the context entirely, and two different system prompts would share an entry.
    """
    plain = openai_request(text="hello")
    with_system = {
        "model": plain["model"],
        "messages": [{"role": "system", "content": "you are terse"}, *plain["messages"]],
    }
    one = OPENAI.cache_probe(plain)
    two = OPENAI.cache_probe(with_system)
    assert one is not None and two is not None
    assert one.text == two.text == "hello"
    assert context_hash(ns(dialect="openai"), one.redacted) != context_hash(
        ns(dialect="openai"), two.redacted
    )


def test_the_context_hash_and_the_exact_key_are_domain_separated() -> None:
    """Two digests of one request must never be equal by accident."""
    body = anthropic_request()
    probe = ANTHROPIC.cache_probe(body)
    assert probe is not None
    assert request_hash(ns(), body) != context_hash(ns(), body)
    assert context_hash(ns(), probe.redacted) != request_hash(ns(), probe.redacted)


def test_two_tenants_never_share_a_context_hash() -> None:
    probe = ANTHROPIC.cache_probe(anthropic_request())
    assert probe is not None
    assert context_hash(ns(), probe.redacted) != context_hash(
        ns(tenant=OTHER_TENANT), probe.redacted
    )


# --- the probe ---------------------------------------------------------------------------


def test_the_probe_is_the_user_text_with_whitespace_collapsed() -> None:
    probe = ANTHROPIC.cache_probe(anthropic_request(text="  what   was\n  the rate? "))
    assert probe is not None
    assert normalise_probe(probe.text) == "what was the rate?"


def test_text_blocks_join_into_one_probe() -> None:
    body = anthropic_request()
    body["messages"] = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}],
        }
    ]
    probe = ANTHROPIC.cache_probe(body)
    assert probe is not None
    assert probe.text == "one\ntwo"


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param(
            [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
            id="two_turns",
        ),
        pytest.param([{"role": "assistant", "content": "a"}], id="assistant_only"),
        pytest.param(
            [{"role": "user", "content": [{"type": "image", "source": {}}]}], id="an_image"
        ),
        pytest.param([{"role": "user", "content": "   "}], id="blank"),
        pytest.param([], id="no_messages"),
        pytest.param("not a list", id="not_a_list"),
    ],
)
def test_anything_that_is_not_one_plain_question_has_no_probe(messages: object) -> None:
    body = anthropic_request()
    body["messages"] = messages
    assert ANTHROPIC.cache_probe(body) is None


def test_an_openai_assistant_turn_has_no_probe() -> None:
    """A preamble is allowed; history is not. History the probe cannot see is history a
    semantic match would ignore."""
    body = openai_request()
    body["messages"] = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    assert OPENAI.cache_probe(body) is None


def test_an_openai_system_preamble_is_allowed_and_the_user_turn_is_last() -> None:
    body = openai_request()
    body["messages"] = [
        {"role": "system", "content": "be terse"},
        {"role": "developer", "content": "and correct"},
        {"role": "user", "content": "the question"},
    ]
    probe = OPENAI.cache_probe(body)
    assert probe is not None
    assert probe.text == "the question"
