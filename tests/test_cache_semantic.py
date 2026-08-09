"""The semantic layer, against real ``bge-small-en-v1.5`` vectors, with no torch in CI.

Every similarity number in this file came out of the real model
(``tests/support/build_semantic_corpus.py``) and is committed as a content-hashed
artifact. That is what lets the keyless suite make claims about *meaning* rather than
about arithmetic: a paraphrase hitting and a near-miss missing are statements about an
embedding space, and asserting them against a bag-of-words stand-in would prove nothing
about the space this gateway actually runs in (BUILD_PLAN L6).

The corpus is §P8.H1's shape in miniature — templates crossed with entities, so the
dangerous collision class ships with it and no "hard negative" had to be invented. The
last section replays the admission decision across the whole threshold range offline,
which is exactly the mechanism the headline experiment will use at 133 questions.
"""

from __future__ import annotations

import pytest

from headroom.cache.replay import CACHE_SIMILARITY_HEADER, CACHE_SOURCE_HEADER
from headroom.core.cache import (
    CACHE_EXACT,
    CACHE_SEMANTIC,
    DEFAULT_SIMILARITY_THRESHOLD,
    DISPOSITION_HIT_SEMANTIC,
    DISPOSITION_MISS,
)
from headroom.providers.mock import MockScript
from tests.support.corpus import load_corpus
from tests.support.fixtures import anthropic_request
from tests.support.harness import GatewayHarness

#: The corpus this file's every claim rests on. Pinned so a regenerated fixture is a
#: deliberate, reviewable change to what the safety tests mean rather than a silent one.
CORPUS_HASH = "5a8d39135188a7c85ca4d56c4bb82fa594ace1d6a032252f2367e7e6590c4e5c"


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


# --- the corpus itself --------------------------------------------------------------------


def test_the_corpus_is_the_one_these_tests_were_written_against() -> None:
    corpus = load_corpus()
    assert corpus.corpus_hash == CORPUS_HASH
    assert corpus.embedding_model == "BAAI/bge-small-en-v1.5"
    assert corpus.dimensions == 384
    assert len(corpus.questions) == 12
    assert len(corpus.probes) == 24


def test_every_probe_knows_its_source_question() -> None:
    """The provenance *is* the answer key for cache correctness (§P8.H1)."""
    corpus = load_corpus()
    ids = {question.id for question in corpus.questions}
    assert all(probe.source in ids for probe in corpus.probes)


def test_every_vector_is_unit_length() -> None:
    """Cosine is a dot product only if this holds — in Postgres, in the in-memory store,
    and in the offline sweep alike."""
    for row in load_corpus().rows:
        assert cosine(row.vector, row.vector) == pytest.approx(1.0, abs=1e-5)


# --- the two bands, measured -------------------------------------------------------------------


def test_the_right_and_wrong_bands_do_not_overlap() -> None:
    """The measurement the default threshold is derived from, asserted rather than quoted.

    A paraphrase against **its own** question never scores below 0.9237; against **any
    other** question it never scores above 0.8511. Everything else in this phase — the
    default, the margins, the claim that a near-miss is refusable at all — is downstream
    of these two numbers, so they are checked here and the constant is checked against
    them below.
    """
    corpus = load_corpus()
    right: list[float] = []
    wrong: list[float] = []
    for probe in corpus.probes:
        for question in corpus.questions:
            score = cosine(probe.vector, question.vector)
            (right if question.id == probe.source else wrong).append(score)

    assert min(right) == pytest.approx(0.9237, abs=5e-4)
    assert max(wrong) == pytest.approx(0.8511, abs=5e-4)
    assert min(right) > max(wrong)


def test_the_shipped_default_sits_in_the_gap_with_the_margin_on_the_poison_side() -> None:
    """0.90, and *why* 0.90 rather than the midpoint.

    The failure directions are not symmetric — a false hit is a wrong answer served with
    confidence, a false miss is an upstream call somebody was going to make anyway — so
    the larger margin belongs above the wrong band.
    """
    corpus = load_corpus()
    right = [cosine(probe.vector, corpus.question(probe.source).vector) for probe in corpus.probes]
    wrong = [
        cosine(probe.vector, question.vector)
        for probe in corpus.probes
        for question in corpus.questions
        if question.id != probe.source
    ]

    assert max(wrong) < DEFAULT_SIMILARITY_THRESHOLD < min(right)
    poison_margin = DEFAULT_SIMILARITY_THRESHOLD - max(wrong)
    hit_margin = min(right) - DEFAULT_SIMILARITY_THRESHOLD
    assert poison_margin > hit_margin


def test_the_near_misses_really_are_near() -> None:
    """The dangerous collision class, quantified: two questions from the same template
    with different artists are each other's closest wrong answers."""
    corpus = load_corpus()
    same_template = [
        cosine(probe.vector, question.vector)
        for probe in corpus.probes
        for question in corpus.questions
        if question.id != probe.source and question.template == probe.template
    ]
    assert max(same_template) > 0.80


# --- through the gateway -------------------------------------------------------------------------


async def seed(gateway: GatewayHarness, text: str, answer: str) -> str:
    """Populate the cache with one canonical question and return its request id."""
    gateway.book.set(answer, MockScript.anthropic_message(answer))
    await gateway.post("/v1/messages", anthropic_request(text=text), script=answer)
    return gateway.last_context().request_id


async def test_a_paraphrase_hits_the_cached_question(gateway: GatewayHarness) -> None:
    """The headline behaviour: different words, same question, no upstream call."""
    corpus = load_corpus()
    question = corpus.question("streaming_revenue:radiohead")
    paraphrase = next(row for row in corpus.probes if row.source == question.id)

    await gateway.set_cache(CACHE_SEMANTIC)
    source_id = await seed(gateway, question.text, question.answer or "")

    response = await gateway.post(
        "/v1/messages", anthropic_request(text=paraphrase.text), script=question.answer or ""
    )

    ctx = gateway.last_context()
    assert ctx.cache_disposition == DISPOSITION_HIT_SEMANTIC
    assert response.headers[CACHE_SOURCE_HEADER] == source_id
    assert float(response.headers[CACHE_SIMILARITY_HEADER]) >= DEFAULT_SIMILARITY_THRESHOLD
    assert (question.answer or "").encode() in response.content
    assert len(gateway.provider.received) == 1


async def test_a_hard_negative_misses(gateway: GatewayHarness) -> None:
    """The same template, a different artist — the shape of the danger §P8.H1 quantifies.

    This is the assertion the whole corpus exists for. The two questions differ by one
    word out of eight and score 0.82 against each other; the answers are different
    numbers about different artists, so a hit here would be a *provably* wrong answer.
    """
    corpus = load_corpus()
    cached = corpus.question("streaming_revenue:radiohead")
    other = corpus.question("streaming_revenue:coldplay")
    assert cached.template == other.template and cached.artist != other.artist

    await gateway.set_cache(CACHE_SEMANTIC)
    await seed(gateway, cached.text, cached.answer or "")
    gateway.book.set("other", MockScript.anthropic_message(other.answer or ""))

    response = await gateway.post(
        "/v1/messages", anthropic_request(text=other.text), script="other"
    )

    assert gateway.last_context().cache_disposition == DISPOSITION_MISS
    assert (other.answer or "").encode() in response.content
    assert (cached.answer or "").encode() not in response.content


async def test_a_paraphrase_of_a_hard_negative_also_misses(gateway: GatewayHarness) -> None:
    """The harder version: a *paraphrase* of the near-miss, so neither the words nor the
    hash can save it. Only the threshold can."""
    corpus = load_corpus()
    cached = corpus.question("royalty_rate:radiohead")
    intruder = next(
        row
        for row in corpus.probes
        if row.template == cached.template and row.artist != cached.artist
    )

    await gateway.set_cache(CACHE_SEMANTIC)
    await seed(gateway, cached.text, cached.answer or "")
    gateway.book.set("other", MockScript.anthropic_message("a different artist's rate"))

    await gateway.post("/v1/messages", anthropic_request(text=intruder.text), script="other")

    assert gateway.last_context().cache_disposition == DISPOSITION_MISS


async def test_every_paraphrase_in_the_corpus_resolves_to_its_own_question(
    gateway: GatewayHarness,
) -> None:
    """The whole corpus through the whole gateway: 12 seeds, 24 probes, zero wrong hits.

    Provenance is what makes this checkable — every hit names the request that populated
    the entry, so "did it resolve to the right question" is a comparison rather than a
    judgement.
    """
    corpus = load_corpus()
    await gateway.set_cache(CACHE_SEMANTIC)

    sources: dict[str, str] = {}
    for question in corpus.questions:
        gateway.book.set(question.id, MockScript.anthropic_message(question.answer or ""))
        await gateway.post(
            "/v1/messages", anthropic_request(text=question.text), script=question.id
        )
        sources[question.id] = gateway.last_context().request_id

    hits = 0
    for probe in corpus.probes:
        gateway.book.set("fallback", MockScript.anthropic_message("upstream answer"))
        response = await gateway.post(
            "/v1/messages", anthropic_request(text=probe.text), script="fallback"
        )
        served = response.headers.get(CACHE_SOURCE_HEADER)
        if served is None:
            continue
        hits += 1
        # The load-bearing line: a hit that resolved to a *different* source question is
        # the silent-wrong-answer event, and there are none.
        assert served == sources[probe.source], f"{probe.id} resolved to the wrong question"

    assert hits == len(corpus.probes), "at the shipped default every paraphrase should hit"


# --- the threshold as a knob ---------------------------------------------------------------------


async def test_raising_the_threshold_turns_a_hit_into_a_miss(
    gateway: GatewayHarness,
) -> None:
    """The config surface, doing something. A tenant at 0.99 refuses what 0.90 admits."""
    corpus = load_corpus()
    question = corpus.question("highest_charting:portishead")
    paraphrase = min(
        (row for row in corpus.probes if row.source == question.id),
        key=lambda row: cosine(row.vector, question.vector),
    )

    await gateway.set_cache(CACHE_SEMANTIC, similarity_threshold=0.99)
    await seed(gateway, question.text, question.answer or "")
    gateway.book.set("fresh", MockScript.anthropic_message("a fresh answer"))

    await gateway.post("/v1/messages", anthropic_request(text=paraphrase.text), script="fresh")
    assert gateway.last_context().cache_disposition == DISPOSITION_MISS


async def test_lowering_the_threshold_admits_a_wrong_answer(
    gateway: GatewayHarness,
) -> None:
    """The other direction, and the reason the threshold is the number §P8.H1 sweeps.

    At 0.70 the same near-miss that misses at 0.90 is served — a *provably* wrong answer,
    because the corpus carries the answer key. The gateway is doing exactly what it was
    told; the finding is that "what it was told" is the whole safety question.
    """
    corpus = load_corpus()
    cached = corpus.question("streaming_revenue:radiohead")
    other = corpus.question("streaming_revenue:coldplay")

    await gateway.set_cache(CACHE_SEMANTIC, similarity_threshold=0.70)
    await seed(gateway, cached.text, cached.answer or "")
    gateway.book.set("other", MockScript.anthropic_message(other.answer or ""))

    response = await gateway.post(
        "/v1/messages", anthropic_request(text=other.text), script="other"
    )

    assert gateway.last_context().cache_disposition == DISPOSITION_HIT_SEMANTIC
    # Coldplay's question, Radiohead's number. This is what the curve measures.
    assert (cached.answer or "").encode() in response.content
    assert (other.answer or "").encode() not in response.content


def test_the_admission_decision_replays_offline_across_the_whole_range() -> None:
    """§P8.H1's mechanism, in miniature and with no gateway at all.

    One similarity matrix, computed once, replayed across 0.70 → 0.99. This is the
    property that makes the headline experiment cost nothing beyond the paraphrase
    generation: the sweep is arithmetic over committed numbers, not four hundred more
    API calls.
    """
    corpus = load_corpus()
    matrix = [
        (probe, question, cosine(probe.vector, question.vector))
        for probe in corpus.probes
        for question in corpus.questions
    ]

    curve = []
    for step in range(70, 100):
        threshold = step / 100
        hits = 0
        wrong = 0
        for probe in corpus.probes:
            best = max((row for row in matrix if row[0].id == probe.id), key=lambda row: row[2])
            if best[2] >= threshold:
                hits += 1
                if best[1].id != probe.source:
                    wrong += 1
        curve.append((threshold, hits, wrong))

    at_default = next(row for row in curve if row[0] == DEFAULT_SIMILARITY_THRESHOLD)
    assert at_default == (DEFAULT_SIMILARITY_THRESHOLD, 24, 0)
    # Monotone in the direction that makes a curve interpretable: raising the bar never
    # adds hits. (The wrong-hit count is not monotone in general — a wrong neighbour can
    # be displaced by a right one — which is exactly why the sweep is run rather than
    # reasoned about.)
    hit_counts = [row[1] for row in curve]
    assert hit_counts == sorted(hit_counts, reverse=True)
    # And on this corpus the curve has no poison anywhere above the measured wrong band.
    assert all(wrong == 0 for threshold, _, wrong in curve if threshold >= 0.86)


# --- interaction with the exact layer -----------------------------------------------------


async def test_an_exact_tenant_does_not_hit_on_a_paraphrase(
    gateway: GatewayHarness,
) -> None:
    """``exact`` really is exact: the same entry exists and is unreachable by similarity."""
    corpus = load_corpus()
    question = corpus.question("monthly_listeners:coldplay")
    paraphrase = next(row for row in corpus.probes if row.source == question.id)

    await gateway.set_cache(CACHE_EXACT)
    await seed(gateway, question.text, question.answer or "")
    gateway.book.set("fresh", MockScript.anthropic_message("a fresh answer"))

    await gateway.post("/v1/messages", anthropic_request(text=paraphrase.text), script="fresh")

    assert gateway.last_context().cache_disposition == DISPOSITION_MISS
    assert await gateway.cache_entries() == 2


async def test_a_reasoning_response_is_never_reachable_by_similarity(
    gateway: GatewayHarness,
) -> None:
    """H-044 end to end: stored, exact-hittable, and invisible to a paraphrase."""
    corpus = load_corpus()
    question = corpus.question("royalty_rate:coldplay")
    paraphrase = next(row for row in corpus.probes if row.source == question.id)

    await gateway.set_cache(CACHE_SEMANTIC)
    gateway.book.set("reason", MockScript.openai_reasoning_stream())
    from tests.support.fixtures import openai_request

    await gateway.post(
        "/v1/chat/completions", openai_request(text=question.text, stream=True), script="reason"
    )
    assert await gateway.cache_entries() == 1

    # The identical question hits.
    await gateway.post(
        "/v1/chat/completions", openai_request(text=question.text, stream=True), script="reason"
    )
    assert gateway.last_context().cache_disposition == "cache_hit_exact"

    # The paraphrase does not.
    await gateway.post(
        "/v1/chat/completions", openai_request(text=paraphrase.text, stream=True), script="reason"
    )
    assert gateway.last_context().cache_disposition == DISPOSITION_MISS
