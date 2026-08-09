"""The embedders, and the one property both of them must have: determinism.

A semantic cache is a claim that two texts are close. If the vector for a text depends on
which process, machine, or day produced it, the claim is unfalsifiable and the cache is
a random-answer generator with good manners. So: same text, same vector, always.

The other decision under test here is that there is **no fallback**. ``HEADROOM_EMBEDDER``
names one embedder and the gateway builds that one or fails. A deployment that silently
degraded from ``bge-small`` to a bag of words would keep answering and keep hitting, and
would quietly stop being the system anybody measured.
"""

from __future__ import annotations

import math

import pytest

from headroom.cache.embedding import (
    DEFAULT_EMBEDDER,
    EMBEDDER_ENV,
    HASHING_EMBEDDER,
    BGEEmbedder,
    HashingEmbedder,
    LazyEmbedder,
    build_embedder,
    load_embedder,
    model_id_for,
)
from headroom.core.cache import EMBEDDING_DIMENSIONS
from headroom.core.errors import ConfigurationError
from tests.support.corpus import CorpusEmbedder, load_corpus


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


# --- the deterministic one ------------------------------------------------------------


def test_the_same_text_always_gives_the_same_vector() -> None:
    one = HashingEmbedder().embed(["what was the streaming rate?"])
    two = HashingEmbedder().embed(["what was the streaming rate?"])
    assert one == two


def test_vectors_are_unit_length() -> None:
    """Cosine is a dot product only if this holds, in Python and in pgvector alike."""
    for vector in HashingEmbedder().embed(["hello world", "a", "several words here"]):
        assert cosine(vector, vector) == pytest.approx(1.0, abs=1e-9)


def test_the_width_matches_the_column() -> None:
    embedder = HashingEmbedder()
    assert embedder.dimensions == EMBEDDING_DIMENSIONS
    assert len(embedder.embed(["x"])[0]) == EMBEDDING_DIMENSIONS


def test_empty_text_still_gets_a_direction() -> None:
    """A zero vector has no direction and would make cosine undefined."""
    for text in ("", "   ", "!!!"):
        vector = HashingEmbedder().embed([text])[0]
        assert cosine(vector, vector) == pytest.approx(1.0, abs=1e-9)


def test_shared_vocabulary_really_does_move_the_cosine() -> None:
    """It is the hashing trick, not a stub: texts that share words are genuinely closer.

    Stated so nobody mistakes it for a language model — it cannot tell a paraphrase from
    a near-miss, which is precisely why every *similarity* assertion in this suite runs
    against committed ``bge-small`` vectors instead.
    """
    embedder = HashingEmbedder()
    base, overlapping, unrelated = embedder.embed(
        [
            "the streaming revenue of the artist in 2019",
            "the streaming revenue of the artist in 2020",
            "completely different words about unrelated matters",
        ]
    )
    assert cosine(base, overlapping) > 0.8
    assert abs(cosine(base, unrelated)) < 0.3


def test_the_hashing_model_id_carries_its_width() -> None:
    """Two hashing embedders of different widths are two vector spaces wearing one name,
    and the id is what keeps their entries apart."""
    assert HashingEmbedder(dimensions=384).model_id != HashingEmbedder(dimensions=128).model_id


def test_batching_does_not_change_a_vector() -> None:
    embedder = HashingEmbedder()
    alone = embedder.embed(["one"])[0]
    batched = embedder.embed(["one", "two", "three"])[0]
    assert alone == batched


def test_an_empty_batch_does_no_work() -> None:
    assert HashingEmbedder().embed([]) == []


# --- resolution --------------------------------------------------------------------------


def test_the_default_is_the_model_the_plan_names() -> None:
    """BUILD_PLAN L6, asserted where a well-meaning change to a constant would show."""
    assert DEFAULT_EMBEDDER == "BAAI/bge-small-en-v1.5"
    assert load_embedder().name == DEFAULT_EMBEDDER


def test_the_environment_selects_the_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMBEDDER_ENV, HASHING_EMBEDDER)
    assert isinstance(load_embedder().resolve(), HashingEmbedder)


def test_an_unknown_name_is_treated_as_a_model_not_as_an_error() -> None:
    """The space of sentence-transformers ids and local weight paths is open, and a
    gateway has no business keeping an allow-list of it. The failure, when there is one,
    comes from the loader and names the extra."""
    assert isinstance(build_embedder("some-org/some-model"), BGEEmbedder)


@pytest.fixture
def no_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    """An environment where the ``embed`` extra is not installed."""
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> object:
        if name == "sentence_transformers":
            raise ImportError("no module named sentence_transformers")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", refuse)


def test_a_missing_model_fails_loudly_and_names_the_fix(
    no_sentence_transformers: None,
) -> None:
    """No fallback. A gateway that quietly swapped embedders would keep hitting and stop
    being the system anybody measured."""
    with pytest.raises(ConfigurationError) as caught:
        BGEEmbedder("BAAI/bge-small-en-v1.5").embed(["x"])

    assert "uv sync --extra embed" in caught.value.message
    assert EMBEDDER_ENV in caught.value.message


def test_resolving_a_missing_model_raises_rather_than_returning_an_object(
    no_sentence_transformers: None,
) -> None:
    """**The bug the container run found.**

    Constructing a :class:`BGEEmbedder` touches no weight file, so a ``resolve`` that only
    *built* one returned happily on an image with no ``sentence-transformers`` installed —
    and ``PUT /admin/cache {"mode": "semantic"}`` answered 200 on a gateway that could not
    embed a single word. A probe that does not probe is worse than no probe, because it
    reads as a guarantee.
    """
    lazy = LazyEmbedder("BAAI/bge-small-en-v1.5")

    with pytest.raises(ConfigurationError) as caught:
        lazy.resolve()

    assert "uv sync --extra embed" in caught.value.message
    # And it did not half-succeed: a retry after a fixed environment really retries.
    assert lazy.loaded is False


def test_resolving_is_idempotent_and_builds_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMBEDDER_ENV, HASHING_EMBEDDER)
    lazy = load_embedder()

    first = lazy.resolve()
    second = lazy.resolve()

    assert first is second
    assert lazy.loaded is True


def test_the_model_id_is_known_without_building_the_model() -> None:
    """A semantic query has to *filter* on the model id, and a gateway whose tenants all
    have caching disabled must never build the model — so the id cannot come from it."""
    lazy = LazyEmbedder("BAAI/bge-small-en-v1.5")
    assert lazy.model_id == "BAAI/bge-small-en-v1.5"
    assert model_id_for(HASHING_EMBEDDER) == HashingEmbedder().model_id


def test_the_lazy_embedder_builds_nothing_until_it_is_used() -> None:
    lazy = LazyEmbedder(HASHING_EMBEDDER)
    assert lazy.calls == 0
    lazy.embed(["one", "two"])
    assert lazy.calls == 2


def test_an_empty_batch_is_not_counted_as_work() -> None:
    lazy = LazyEmbedder(HASHING_EMBEDDER)
    lazy.embed([])
    assert lazy.calls == 0


# --- the corpus embedder the suite runs on -------------------------------------------------


def test_the_corpus_embedder_returns_the_committed_vectors() -> None:
    corpus = load_corpus()
    row = corpus.questions[0]
    assert CorpusEmbedder().embed([row.text])[0] == list(row.vector)


def test_the_corpus_embedder_is_deterministic_for_invented_text() -> None:
    one = CorpusEmbedder().embed(["a question nobody committed"])
    two = CorpusEmbedder().embed(["a question nobody committed"])
    assert one == two


def test_a_hashed_vector_is_near_orthogonal_to_a_real_one() -> None:
    """The two vector spaces in :class:`CorpusEmbedder` do not interfere.

    Asserted rather than assumed, because the whole reason the hybrid is acceptable is
    that an invented text can never accidentally land near a committed one and turn a
    miss into a hit in some unrelated test.
    """
    corpus = load_corpus()
    embedder = CorpusEmbedder()
    real, hashed = embedder.embed([corpus.questions[0].text, "some entirely invented text"])
    assert abs(cosine(real, hashed)) < 0.3


def test_the_corpus_embedder_carries_the_corpus_model_id() -> None:
    """It inherits the namespace rule rather than opting out of it: entries written under
    the corpus's model id are matched only by queries carrying the same id."""
    assert CorpusEmbedder().model_id == load_corpus().embedding_model


def test_the_corpus_embedder_counts_what_it_embeds() -> None:
    """The counter the disabled-tenant proof reads. "No embedding compute" has to be a
    measurement rather than a claim."""
    embedder = CorpusEmbedder()
    embedder.embed(["a", "b", "c"])
    assert embedder.calls == 3


def test_the_committed_vectors_really_are_normalised() -> None:
    """The generator asked for ``normalize_embeddings=True``; six-place rounding is the
    only thing between that and this assertion, and it is four orders of magnitude below
    any threshold decision."""
    for row in load_corpus().rows:
        assert math.isclose(cosine(list(row.vector), list(row.vector)), 1.0, abs_tol=1e-5)
