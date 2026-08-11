"""Assemble the rollup Lambda's deployment directory. Run by ``make lambda-build``.

    python deploy/aws/lambda/build.py

Terraform's ``archive_file`` zips whatever this leaves in ``build/`` — so there is no
``zip`` binary in the loop, nothing platform-specific about the packaging step, and the
artifact's hash is what triggers a redeploy.

**What goes in, and why so little.** ``headroom/`` itself, copied from the working tree,
plus ``asyncpg``. That is the whole dependency: ``headroom.rollup.handler`` imports
``PostgresLedgerStore`` → ``DatabasePool`` → ``asyncpg``, and every other module it
touches is dataclasses and ``decimal``. FastAPI, httpx, pydantic and PyYAML are in the
package's metadata and are never imported on this path, so they are never installed here
— which is why the zip is a couple of megabytes rather than sixty. ``boto3`` is provided
by the Lambda Python runtime and is deliberately not vendored.

**The version is the lockfile's.** ``asyncpg`` is resolved from ``uv.lock`` rather than
left to float, so the driver the Lambda writes rollups with is the driver the gateway
writes ledger rows with. A silent minor difference between two processes talking to one
database is exactly the kind of thing that is only ever noticed as "the nightly job
behaves differently".

**The wheel is built for the runtime, not for this machine.** ``--python-platform
x86_64-manylinux_2_28 --python-version 3.12 --only-binary :all:`` pins the wheel to the
Lambda's architecture and interpreter regardless of what the build host is, and refuses
to fall back to an sdist that would compile against the wrong libc. That is what makes
this reproducible on a machine that is not the operator's — and it is why the last thing
this script does is look for the compiled extension by name: ``asyncpg`` without its C
protocol module imports and then fails at connect time, which is a bad place to find out.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BUILD = HERE / "build"

#: The Lambda's target, and the whole stack's: one architecture, matching the Fargate
#: tasks and the interpreter AWS ships for `python3.12`.
#:
#: `manylinux_2_28` rather than the older `manylinux2014`, because that is what asyncpg
#: publishes for cp312 — `2014` resolves to nothing and uv says so rather than guessing.
#: It is also the right target: the `python3.12` Lambda runtime is Amazon Linux 2023,
#: whose glibc is newer than 2.28.
PLATFORM = "x86_64-manylinux_2_28"
PYTHON_VERSION = "3.12"

#: Present iff the compiled wheel really landed. Checked rather than assumed — see the
#: module docstring's last paragraph.
COMPILED_MARKER = "asyncpg/protocol/protocol.cpython-312-x86_64-linux-gnu.so"

#: The one dependency that is not in the runtime and not part of `headroom`.
VENDORED = ("asyncpg",)

#: Copied but never imported by the handler. Excluded so the artifact says what it is.
EXCLUDE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def locked_version(package: str) -> str:
    """The version ``uv.lock`` pins for ``package``.

    Read rather than assumed: the point of vendoring from the lockfile is that the
    Lambda and the gateway use the same driver, and a hard-coded version here would be
    the second place that fact is written down.
    """
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    for entry in lock.get("package", []):
        if entry.get("name") == package:
            version = entry.get("version")
            if isinstance(version, str):
                return version
    raise SystemExit(f"{package} is not in uv.lock — did the dependency move?")


def main() -> int:
    if BUILD.exists():
        # A fresh directory every time. An incremental build would let a module that has
        # been deleted from `headroom/` survive in the zip, which is a class of bug that
        # only ever shows up in production.
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    shutil.copytree(REPO / "headroom", BUILD / "headroom", ignore=EXCLUDE)

    pinned = [f"{name}=={locked_version(name)}" for name in VENDORED]
    command = [
        "uv",
        "pip",
        "install",
        "--target",
        str(BUILD),
        "--python-platform",
        PLATFORM,
        "--python-version",
        PYTHON_VERSION,
        "--only-binary",
        ":all:",
        *pinned,
    ]
    print(" ".join(command))
    result = subprocess.run(command, cwd=REPO, check=False)
    if result.returncode != 0:
        return result.returncode

    # uv leaves a `.lock` behind in a `--target` directory. Harmless in the zip and still
    # not part of the artifact.
    (BUILD / ".lock").unlink(missing_ok=True)

    if not (BUILD / COMPILED_MARKER).exists():
        raise SystemExit(
            f"{COMPILED_MARKER} is missing: asyncpg installed without its compiled "
            "protocol module, which fails at connect time rather than at import. "
            f"Check that a wheel exists for {PLATFORM} / cp{PYTHON_VERSION.replace('.', '')}."
        )

    modules = sorted(path.name for path in BUILD.iterdir() if not path.name.endswith(".dist-info"))
    size = sum(path.stat().st_size for path in BUILD.rglob("*") if path.is_file())
    print(f"built {BUILD.relative_to(REPO)}: {', '.join(modules)}")
    print(f"{size / 1_048_576:.1f} MiB unzipped · handler headroom.rollup.handler.handler")
    return 0


if __name__ == "__main__":
    sys.exit(main())
