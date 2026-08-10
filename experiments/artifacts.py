"""Golden artifacts: stable JSON, content hashes over *meaning*, provenance blocks.

Three small services, shared by every experiment, each of which exists because of a
specific way an experiment stops being reproducible.

**Stable JSON.** An artifact whose formatting drifts is an artifact whose diff is
unreadable, and an unreadable diff is a review nobody does. One writer, one set of
options, `ensure_ascii=False` so a `ö` stays a `ö` on the page.

**Content hashes cover what the artifact *means*, never everything it contains.** The
Phase 5 corpus settled this and the reason generalises: hashing the vectors would make a
rounding change look like a corpus change, while hashing the texts and the provenance
mapping pins the thing a reader has to trust. Each artifact names its own material.

**Provenance is a block, not a comment.** Every artifact records what produced it, from
which inputs, at which commits — because the first question anybody asks about a number
in `results/` is "which corpus, which gateway, when", and the second is "can I get back
to it".
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "ARTIFACTS_DIR",
    "EXPERIMENTS_DIR",
    "REPO_ROOT",
    "RESULTS_DIR",
    "canonical_bytes",
    "content_hash",
    "git_sha",
    "provenance",
    "read_json",
    "write_json",
]

EXPERIMENTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXPERIMENTS_DIR.parent
ARTIFACTS_DIR = EXPERIMENTS_DIR / "artifacts"
RESULTS_DIR = EXPERIMENTS_DIR / "results"


def canonical_bytes(material: Any) -> bytes:
    """The one serialization a hash is ever taken over: sorted keys, no whitespace."""
    return json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def content_hash(material: Any) -> str:
    """SHA-256 over :func:`canonical_bytes`. Callers choose the material deliberately."""
    return hashlib.sha256(canonical_bytes(material)).hexdigest()


def git_sha(repo: Path, *, short: bool = False) -> str | None:
    """The repo's current commit, or ``None`` when it is not a git checkout.

    ``None`` rather than a raise: a stranger's unpacked tarball should still be able to
    rebuild an artifact, and the missing sha is recorded as missing rather than faked.
    """
    args = ["git", "-C", str(repo), "rev-parse"] + (["--short"] if short else []) + ["HEAD"]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment-dependent
        return None
    return out.stdout.strip() or None


def provenance(
    *,
    produced_by: str,
    inputs: Mapping[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """The block every artifact and every result file carries.

    ``generated_at`` is deliberately *outside* every content hash in this package: an
    artifact rebuilt from identical inputs must hash identically, or "unchanged" stops
    being checkable.
    """
    block: dict[str, Any] = {
        "produced_by": produced_by,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "headroom_commit": git_sha(REPO_ROOT),
    }
    if inputs:
        block["inputs"] = dict(inputs)
    if notes:
        block["notes"] = notes
    return block


def write_json(path: Path, payload: Any) -> Path:
    """Write an artifact or a result. One trailing newline, LF, UTF-8, indent 1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
