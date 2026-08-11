#!/usr/bin/env python
"""kube-proxy in sixty lines: new connections follow the endpoint, established ones don't.

That second clause is the entire reason Phase 10 §8 dropped one request per replaced pod,
and it is the one thing a single-backend laptop cannot show. When Kubernetes removes a pod
from Endpoints, kube-proxy stops sending it *new* connections; conntrack keeps every flow
that already exists pinned to the pod it was given to. So a client holding keep-alive
connections goes on talking to the pod that is about to stop, right through the preStop
sleep, and loses whatever it had written when SIGTERM finally closes them (H-091).

This listens on :9000 and forwards to ``A`` until ``--flip-file`` appears, then forwards to
``B`` — while every connection already established stays with whoever it was given. That is
enough to reproduce the drop, and enough to show it gone.

``--rtt-ms`` is not a garnish. The first version of this proxy had no such flag and measured
zero drops against a build that drops one per pod in us-east-1, because ``httpcore`` checks
whether a pooled socket has become readable before it reuses one: on loopback the server's
FIN always wins the race and the client quietly opens a new connection instead. **The race
window is one RTT.** Delaying the close in the upstream-to-client direction is what lets a
laptop see what a load balancer two milliseconds away sees. 2 ms reproduces §8 exactly,
error string and all.

Run it with ``scripts/rollout_repro.sh``, which brings up the two gateways around it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path


async def _pipe(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    delay_close: float = 0.0,
) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except Exception:
        # Either side going away is the normal end of a proxied connection, and during a
        # pod replacement it is the *expected* end. The measurement is the load loop's.
        pass
    finally:
        if delay_close:
            await asyncio.sleep(delay_close)
        writer.close()


async def _serve(args: argparse.Namespace) -> None:
    flip = Path(args.flip_file)
    rtt_s = args.rtt_ms / 1000.0

    async def handle(
        client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        # Read the endpoint once, when the connection is accepted, and never again for the
        # life of it. This one line is the whole fidelity of the rig.
        target = args.b if flip.exists() else args.a
        host, _, port = target.rpartition(":")
        try:
            up_reader, up_writer = await asyncio.open_connection(host, int(port))
        except OSError:
            client_writer.close()
            return
        await asyncio.gather(
            _pipe(client_reader, up_writer),
            _pipe(up_reader, client_writer, delay_close=rtt_s),
        )

    server = await asyncio.start_server(handle, args.host, args.port)
    print(
        f"endpoints: {args.host}:{args.port} -> {args.a} until {flip} exists, "
        f"then {args.b}; rtt {args.rtt_ms} ms",
        flush=True,
    )
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="endpoints_proxy", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--a", default="127.0.0.1:8080", help="the pod being replaced")
    parser.add_argument("--b", default="127.0.0.1:8081", help="its replacement")
    parser.add_argument(
        "--flip-file",
        default="/tmp/headroom-endpoints-flip",
        help="touch this to move Endpoints from A to B, as a rolling upgrade does",
    )
    parser.add_argument(
        "--rtt-ms",
        type=float,
        default=2.0,
        help="delay on closing the client side, emulating one network round trip. 0 makes "
        "the rig unable to reproduce the very thing it exists to reproduce — see the "
        "module docstring, and H-091.",
    )
    args = parser.parse_args(argv)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
