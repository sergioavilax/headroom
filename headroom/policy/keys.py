"""Virtual keys: minting them, hashing them, and deciding what they may reach.

Four small functions, and the reasoning behind three of them is the whole of
docs/DECISIONS.md H-017.

**The secret has full entropy, so the hash does not need to be slow.** A key is
``hk_`` followed by ``secrets.token_urlsafe(32)`` — 256 bits from the OS CSPRNG. A
password KDF (argon2, bcrypt, PBKDF2) exists to make guessing *low-entropy,
human-chosen* inputs expensive. There is nothing to guess here: an attacker holding the
entire ``virtual_keys`` table would need to enumerate 2^256 candidates, and no
work-factor moves that number anywhere interesting. What a KDF would move is the cost
of every authenticated request on a gateway whose product is first-token latency. So:
**SHA-256 over the whole key string, hex-encoded**, which is also what a UNIQUE index
can be built on — one lookup, no per-row work, no new dependency.

**The stored prefix is short on purpose.** ``key_prefix`` is the first 11 characters:
``hk_`` plus 8 of the 43 secret characters. That is enough to tell two keys apart in a
list and ~48 bits short of being useful to anyone who steals the table, since the
remaining 35 characters (≈208 bits) are still missing. It is stored deliberately, and
``tests/test_key_secrecy.py`` asserts the *rest* of the key appears nowhere at all.

**Empty scope means unrestricted.** Not NULL, not a magic ``"*"`` entry — empty. An
entry matches exactly, or as a prefix when it ends in ``*``. Exact-by-default is the
important half: a scope of ``mock-model-1`` must not quietly also admit
``mock-model-10``, and prefix-by-default is precisely the rule that would.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence
from typing import Final

__all__ = [
    "DISPLAY_PREFIX_LEN",
    "KEY_PREFIX",
    "SECRET_BYTES",
    "display_prefix",
    "hash_key",
    "looks_like_key",
    "mint_key",
    "scope_allows",
]

#: Every Headroom virtual key starts with this. Recognisable in a log, a bug report,
#: and a secret scanner's regex.
KEY_PREFIX: Final = "hk_"

#: Bytes of CSPRNG entropy behind each key. 32 → 256 bits → 43 urlsafe characters.
SECRET_BYTES: Final = 32

#: Characters of the key stored in the clear for display: ``hk_`` plus 8.
DISPLAY_PREFIX_LEN: Final = len(KEY_PREFIX) + 8

#: Shortest thing that could conceivably be a key. Anything shorter is malformed, and
#: saying so costs a database round trip less than finding out it is unknown.
_MIN_KEY_LEN: Final = DISPLAY_PREFIX_LEN + 8


def mint_key() -> str:
    """A fresh virtual key. The only place a plaintext key is ever created."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(SECRET_BYTES)}"


def hash_key(key: str) -> str:
    """The stored form: SHA-256 hex of the whole key string.

    Deterministic on purpose — authentication is a single indexed lookup by this
    value, which a per-row salt would make impossible without either a lookup id
    embedded in the key or a full-table scan.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def display_prefix(key: str) -> str:
    """The non-secret slice kept for display: ``hk_`` plus the first 8 characters."""
    return key[:DISPLAY_PREFIX_LEN]


def looks_like_key(value: str) -> bool:
    """Whether this could be a Headroom key at all.

    A shape check, not a security check — it only lets the gateway answer an obviously
    wrong credential (a provider key pasted by mistake, a truncated copy-paste) without
    touching the database, and lets the failure carry a ``reason`` that says *malformed*
    rather than *unknown*. Both are 401; the difference is what the operator reads.
    """
    return value.startswith(KEY_PREFIX) and len(value) >= _MIN_KEY_LEN


def scope_allows(scope: Sequence[str], value: str) -> bool:
    """Whether ``value`` is inside ``scope``. An empty scope allows everything.

    ``"claude-haiku-4-5"`` matches only itself; ``"claude-*"`` matches the family. The
    trailing star is the only wildcard, and it is only honoured at the end.
    """
    if not scope:
        return True
    for entry in scope:
        if entry.endswith("*"):
            if value.startswith(entry[:-1]):
                return True
        elif entry == value:
            return True
    return False
