"""Serving a cache hit: the stored bytes, unchanged, in the transport they arrived in.

BUILD_PLAN §P5 asks for *"cached responses replay as a simulated stream when the caller
asked for streaming (first token effectively instant — a demo moment)"*, and that is what
happens here — but by a route worth stating, because the obvious implementation is the
dangerous one.

**The obvious design is one canonical entry per question, rendered into whichever
transport the caller wants.** It hits more often, and it requires two pieces of
machinery this repo has spent four phases arguing against: an *assembler* that rebuilds a
JSON message from SSE frames, and a *synthesiser* that emits frames a provider never
sent. H-007 forwards bytes it never re-serialises, H-016 proves by sabotage that a single
re-encode is invisible to every test that does not compare bytes, and H-028 refuses to
rewrite a request body even to close a real metering gap. A cache is a worse place to
start rebuilding payloads than any of those, because its output is served forever.

**So the transport is part of the key** (``headroom/core/cache.py``), an entry stores the
upstream's bytes verbatim, and a replay yields exactly those bytes. A streaming caller is
served by an entry a streaming request populated; a non-streaming caller by one a
non-streaming request populated; neither is ever converted into the other. The demo moment
survives intact — stream a question twice and the second one's first token is already
there — and the fidelity claim gets *stronger* than the live path's: A4 says chunk
boundaries are never meaningful and content equality is the bar, and a replay is
byte-identical, which is a strict superset (H-043).

**Upstream response headers are not stored and not replayed.** A cached
``anthropic-ratelimit-requests-remaining`` describes a call that did not happen, and a
request id from a previous request would send an operator chasing the wrong trace. What a
replay carries instead is Headroom's own namespace, which — since H-038 — is stripped from
every upstream response and can therefore only have been written by this process.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Final

from starlette.responses import Response, StreamingResponse

from headroom.core.cache import TRANSPORT_STREAM, CacheHit
from headroom.core.context import OUTCOME_OK, RequestContext

__all__ = [
    "CACHE_AGE_HEADER",
    "CACHE_SIMILARITY_HEADER",
    "CACHE_SOURCE_HEADER",
    "CACHE_STATUS_HEADER",
    "replay",
]

#: ``cache_hit_exact`` | ``cache_hit_semantic``, so a caller can tell the two apart
#: without a dashboard — and so a Phase 8 harness can count hits from response headers
#: alone when it is not reading the ledger.
CACHE_STATUS_HEADER: Final = "x-headroom-cache"
#: The request id that populated the entry. The audit trail from an answer back to the
#: question it was actually produced for — which for a semantic hit is the whole ball
#: game (§P8.H1's "hit resolved to a *different* source question").
CACHE_SOURCE_HEADER: Final = "x-headroom-cache-source"
#: Cosine similarity, on semantic hits only.
CACHE_SIMILARITY_HEADER: Final = "x-headroom-cache-similarity"
#: Seconds since the entry was created. How stale the answer is, in the one unit a
#: caller can act on.
CACHE_AGE_HEADER: Final = "x-headroom-cache-age"


def _headers(hit: CacheHit, ctx: RequestContext) -> dict[str, str]:
    built = {CACHE_STATUS_HEADER: hit.disposition}
    if hit.entry.source_request_id:
        built[CACHE_SOURCE_HEADER] = hit.entry.source_request_id
    if hit.similarity is not None:
        built[CACHE_SIMILARITY_HEADER] = f"{hit.similarity:.5f}"
    if hit.entry.created_at is not None:
        age = (ctx.started_at - hit.entry.created_at).total_seconds()
        built[CACHE_AGE_HEADER] = str(max(0, int(age)))
    return built


async def replay(
    hit: CacheHit, ctx: RequestContext, *, on_complete: Callable[[], Awaitable[None]]
) -> Response:
    """Build the response for a hit, and close the request's books as a success.

    ``complete`` is called with the same ``ok`` outcome a live 200 gets, because that is
    what the caller experienced. Everything that distinguishes a hit from an upstream
    call lives in columns of its own — ``cache_disposition``, ``cache_similarity``,
    ``cache_source_request_id`` — and in the ones that stay NULL because nothing happened
    to fill them.

    ``on_complete`` is the proxy's metering, handed in rather than imported, so this
    module stays free of the gateway. On a streamed replay it runs **after** the bytes
    have been yielded, which keeps the ledger row's ordering the same as the live path's:
    a request's end never precedes its first output byte.
    """
    headers = _headers(hit, ctx)
    if hit.entry.transport != TRANSPORT_STREAM:
        ctx.mark_first_token_out()
        ctx.complete(OUTCOME_OK, status_code=200)
        await on_complete()
        return Response(
            content=hit.entry.body,
            status_code=200,
            headers=headers,
            media_type=hit.entry.content_type,
        )
    return StreamingResponse(
        _stream(hit.entry.body, ctx, on_complete),
        status_code=200,
        headers=headers,
        media_type=hit.entry.content_type,
    )


async def _stream(
    body: bytes, ctx: RequestContext, on_complete: Callable[[], Awaitable[None]]
) -> AsyncIterator[bytes]:
    """The stored SSE bytes, in one chunk.

    One chunk rather than a re-chopped imitation of the original chunking, and that is
    the honest choice rather than a lazy one: A4 fixed from Phase 1 that chunk boundaries
    are whatever the transport produced and that no test may assert on them. Splitting
    these bytes into plausible-looking pieces would be inventing a shape, and inventing
    shapes is what this module exists not to do. What the caller receives — the frames,
    their order, their contents, their bytes — is identical either way, which is the
    property the replay-fidelity tests assert.
    """
    ctx.mark_first_token_out()
    yield body
    ctx.complete(OUTCOME_OK, status_code=200)
    await on_complete()
