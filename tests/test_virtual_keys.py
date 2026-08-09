"""The key primitives: minting, hashing, the display prefix, and scope matching.

Small functions, but three of them are the load-bearing part of docs/DECISIONS.md
H-017 — the argument for a fast hash rests entirely on the secret really having the
entropy it is claimed to have, and "the key is 256 bits of CSPRNG output" is a property
worth an assertion rather than a comment.
"""

from __future__ import annotations

import pytest

from headroom.policy.auth import credential_from
from headroom.policy.keys import (
    DISPLAY_PREFIX_LEN,
    KEY_PREFIX,
    SECRET_BYTES,
    display_prefix,
    hash_key,
    looks_like_key,
    mint_key,
    scope_allows,
)


def test_a_minted_key_carries_the_entropy_the_hashing_argument_depends_on() -> None:
    key = mint_key()

    assert key.startswith(KEY_PREFIX)
    assert SECRET_BYTES == 32, "H-017's 256-bit claim"
    # base64url of 32 bytes, unpadded: 43 characters.
    assert len(key) == len(KEY_PREFIX) + 43


def test_minted_keys_do_not_repeat() -> None:
    assert len({mint_key() for _ in range(500)}) == 500


def test_hashing_is_deterministic_and_hex() -> None:
    key = mint_key()

    digest = hash_key(key)

    assert digest == hash_key(key)
    assert len(digest) == 64
    assert int(digest, 16) >= 0
    assert digest != hash_key(mint_key())


def test_the_display_prefix_is_short_and_keeps_the_secret_out() -> None:
    key = mint_key()

    prefix = display_prefix(key)

    assert prefix == key[:DISPLAY_PREFIX_LEN] == key[:11]
    assert prefix.startswith(KEY_PREFIX)
    # 35 of 43 secret characters withheld — the point of storing a prefix at all.
    assert len(key) - len(prefix) == 35


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("hk_abcdefghijklmnop", True),
        ("hk_short", False),
        ("sk-ant-api03-something-long-enough-to-pass-a-length-test", False),
        ("Bearer hk_abcdefghijklmnop", False),
        ("", False),
    ],
)
def test_the_shape_check_is_a_shape_check(value: str, expected: bool) -> None:
    """It exists to answer an obviously-wrong credential without a database round trip,
    not to decide anything about a plausible one."""
    assert looks_like_key(value) is expected


# --- scope --------------------------------------------------------------------------


def test_an_empty_scope_allows_anything() -> None:
    assert scope_allows((), "claude-haiku-4-5") is True
    assert scope_allows([], "anything-at-all") is True


def test_an_entry_matches_exactly() -> None:
    assert scope_allows(("mock-model-1",), "mock-model-1") is True
    assert scope_allows(("mock-model-1",), "mock-model-2") is False


def test_an_exact_entry_is_not_a_prefix() -> None:
    """The trap this rule exists to avoid: ``mock-model-1`` admitting ``mock-model-10``."""
    assert scope_allows(("mock-model-1",), "mock-model-10") is False


def test_a_trailing_star_is_a_prefix() -> None:
    assert scope_allows(("claude-*",), "claude-haiku-4-5") is True
    assert scope_allows(("claude-*",), "gpt-4o") is False


def test_a_bare_star_allows_everything_explicitly() -> None:
    assert scope_allows(("*",), "literally-anything") is True


def test_a_star_anywhere_but_the_end_is_literal() -> None:
    """One wildcard, in one position. A glob engine in an authorization check is how
    permissions end up meaning something nobody predicted."""
    assert scope_allows(("cla*de",), "claude-haiku") is False
    assert scope_allows(("cla*de",), "cla*de") is True


def test_any_matching_entry_is_enough() -> None:
    assert scope_allows(("gpt-4o", "claude-*"), "claude-opus-4-5") is True


# --- reading the credential off a request ------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"authorization": "Bearer hk_key"},
        {"authorization": "bearer hk_key"},
        {"authorization": "  Bearer   hk_key  "},
        {"authorization": "hk_key"},
        {"x-api-key": "hk_key"},
        {"api-key": "hk_key"},
    ],
)
def test_a_credential_is_found_in_every_spelling(headers: dict[str, str]) -> None:
    assert credential_from(headers) == "hk_key"


def test_authorization_wins_when_both_are_present() -> None:
    """Deterministic, so a client that sets both cannot get different answers."""
    assert credential_from({"authorization": "Bearer hk_a", "x-api-key": "hk_b"}) == "hk_a"


@pytest.mark.parametrize(
    "headers", [{}, {"authorization": ""}, {"authorization": "Bearer"}, {"x-api-key": "   "}]
)
def test_nothing_usable_is_no_credential(headers: dict[str, str]) -> None:
    assert credential_from(headers) is None
