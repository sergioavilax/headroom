"""Embedders for the semantic cache: what turns a question into a vector.

BUILD_PLAN L6 fixes the production answer — ``BAAI/bge-small-en-v1.5``, on CPU, weights
baked into the deploy image — and H-004 fixes the constraint that makes it interesting:
**CI never installs the ``embed`` extra**, because a 200 MB torch download per job buys
nothing on the overwhelming majority of them. So this module has to serve two masters,
and the way it does is the decision worth reading.

**Two embedders, chosen by name, never by what happens to be importable.**
:func:`load_embedder` resolves ``HEADROOM_EMBEDDER`` to exactly one implementation and
raises if it cannot build it. There is deliberately **no fallback**: a gateway that
silently degraded from ``bge-small`` to a bag-of-words hash would keep answering, keep
hitting, and quietly stop being the system anybody measured. The model id is part of the
semantic cache's namespace, so a *swap* can never serve one model's entries to another
model's queries — but a silent swap would still change what "similar" means, and that is
the kind of change a cache must never make by itself.

* :class:`BGEEmbedder` — the real one. ``sentence-transformers``, CPU, L2-normalized
  vectors, model loaded once and lazily, so importing this module never pulls in torch.
* :class:`HashingEmbedder` — deterministic, dependency-free, and a *real* similarity
  function rather than a stub: it is the hashing trick over word unigrams, so texts that
  share vocabulary really are close and texts that do not really are not. It exists so
  the keyless suite can exercise every path around the vectors without pretending to be
  a language model. What it cannot do is tell a paraphrase from a near-miss, which is
  precisely why the similarity assertions in the suite run against committed
  ``bge-small`` vectors instead (``tests/support/corpus.py``).

**Vectors are unit-length, always.** Cosine similarity is then a dot product, pgvector's
``<=>`` operator is ``1 - similarity`` exactly, and a threshold means the same number in
Postgres, in Python, and in the offline sweep §P8.H1 will run over the same rows.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Final, Protocol

from headroom.core.errors import ConfigurationError

__all__ = [
    "EMBEDDER_ENV",
    "HASHING_EMBEDDER",
    "BGEEmbedder",
    "CacheEmbedder",
    "Embedder",
    "HashingEmbedder",
    "LazyEmbedder",
    "build_embedder",
    "load_embedder",
    "model_id_for",
]

#: Which embedder a deployment uses. A model name (or a local path to baked weights —
#: the L6 pattern Phase 9 will use) selects :class:`BGEEmbedder`; the literal
#: ``hashing`` selects the dependency-free one. Named here beside the other
#: environment knobs' homes, and read exactly once, at gateway construction.
EMBEDDER_ENV: Final = "HEADROOM_EMBEDDER"

#: The one non-model value ``HEADROOM_EMBEDDER`` accepts.
HASHING_EMBEDDER: Final = "hashing"

#: BUILD_PLAN L6's model, and therefore the default.
DEFAULT_EMBEDDER: Final = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    """Text in, unit-length vectors out. Deterministic for a given model id."""

    #: Recorded on every cached row and matched on every semantic query, so entries
    #: embedded by one model can never be returned to a query embedded by another.
    #: For a real model this is its name; changing it invalidates nothing and matches
    #: nothing, which is the correct behaviour for a different vector space.
    model_id: str
    #: Must equal ``headroom.core.cache.EMBEDDING_DIMENSIONS`` — the column is
    #: ``vector(384)`` and a different width is a migration, not a config change.
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch. Empty input returns an empty list without doing any work."""

    def load(self) -> None:
        """Do whatever expensive setup this embedder needs, now, or raise.

        Separate from :meth:`embed` because "can this model be built at all" is the
        question ``PUT /admin/cache`` has to answer, and it must not be answerable by a
        constructor that has not touched a weight file — see :meth:`LazyEmbedder.resolve`
        for the bug that made this a method rather than an assumption.
        """


class CacheEmbedder(Embedder, Protocol):
    """What the cache gate and ``/admin/cache`` need: an embedder plus a way to *probe* it.

    :meth:`resolve` exists so enabling semantic caching can fail in the request that
    asked for it rather than on some tenant's traffic later. It is separate from
    :meth:`Embedder.embed` because "can this model be built at all" and "embed these
    texts" are different questions with different costs, and only the first one belongs
    on an admin route.
    """

    def resolve(self) -> Embedder:
        """Build (or return) the underlying embedder, raising if it cannot be built."""


# --- the deterministic one ------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    """Lowercased alphanumeric runs. Deliberately crude — see the class docstring."""
    out: list[str] = []
    current: list[str] = []
    for char in text.lower():
        if char.isalnum():
            current.append(char)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


class HashingEmbedder:
    """The hashing trick over word unigrams, L2-normalized. No dependencies, no model.

    Each token is hashed to a bucket and a sign, and the signs are what keep unrelated
    texts near-orthogonal rather than merely non-identical: without them every vector
    would live in the positive orthant and nothing would ever score below about 0.5.

    Deterministic across processes and platforms, because BLAKE2b is, and because
    nothing here consults a clock, a random seed, or a dictionary that could be
    regenerated differently. That is the property the keyless suite depends on.
    """

    __slots__ = ("dimensions", "model_id")

    def __init__(self, *, dimensions: int = 384) -> None:
        self.dimensions = dimensions
        # Versioned: a change to the tokenizer or the hash is a change to the vector
        # space, and it has to invalidate cached rows rather than silently reinterpret
        # them. Bumping this string is how that is done.
        self.model_id = f"headroom-hashing-v1-{dimensions}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def load(self) -> None:
        """Nothing to load: it is arithmetic over a hash function."""

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            # No tokens at all (empty text, or punctuation only). A zero vector has no
            # direction and would make cosine undefined, so the text's own digest picks
            # one deterministically instead.
            digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dimensions] = 1.0
            return vector
        return [value / norm for value in vector]


# --- the real one ---------------------------------------------------------------------


class BGEEmbedder:
    """``sentence-transformers`` on CPU. The model loads on first use, not on import.

    Lazy on purpose: ``headroom.api.gateway`` imports this module at startup, and a
    gateway whose every tenant has caching disabled must not pay for torch — nor should
    CI's image job, which builds the container without the extra and smokes ``/healthz``.
    """

    __slots__ = ("_model", "dimensions", "model_id")

    def __init__(self, model_id: str = DEFAULT_EMBEDDER, *, dimensions: int = 384) -> None:
        self.model_id = model_id
        self.dimensions = dimensions
        self._model: object | None = None

    def _load(self) -> object:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - exercised by the admin probe test
            raise ConfigurationError(
                f"embedder {self.model_id!r} needs sentence-transformers, which is not "
                f"installed; run `uv sync --extra embed`, or set "
                f"{EMBEDDER_ENV}={HASHING_EMBEDDER}"
            ) from exc
        self._model = SentenceTransformer(self.model_id, device="cpu")
        return self._model

    def load(self) -> None:
        """Pull the weights in now. This is the call ``PUT /admin/cache`` makes."""
        self._load()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        # `normalize_embeddings=True` is what makes the dot product a cosine and makes
        # pgvector's `<=>` exactly `1 - similarity`.
        vectors = model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
        return [[float(value) for value in vector] for vector in vectors]


# --- resolution -------------------------------------------------------------------------


def build_embedder(name: str, *, dimensions: int = 384) -> Embedder:
    """One name to one embedder. Unknown names are models, not errors.

    A name this module does not recognise is handed to :class:`BGEEmbedder`, because the
    space of sentence-transformers model ids and local weight paths is open and a
    gateway has no business maintaining an allow-list of them. The failure, when there is
    one, arrives from the loader and names the extra.
    """
    if name == HASHING_EMBEDDER:
        return HashingEmbedder(dimensions=dimensions)
    return BGEEmbedder(name, dimensions=dimensions)


class LazyEmbedder:
    """The gateway's handle on an embedder: named at startup, built on first use.

    Two things fall out of the laziness and both are requirements rather than niceties.
    Building a gateway must never import torch (``build_gateway`` runs in CI's image job
    with no extra installed), and a deployment whose tenants all have caching **disabled**
    must do no embedding work at all — which is a measurement
    ``tests/test_cache_gate.py`` takes off :attr:`calls`, not a claim.
    """

    __slots__ = ("_inner", "calls", "dimensions", "model_id", "name")

    def __init__(self, name: str | None = None, *, dimensions: int = 384) -> None:
        self.name = name if name is not None else os.environ.get(EMBEDDER_ENV) or DEFAULT_EMBEDDER
        self.dimensions = dimensions
        #: Known without loading anything, because a semantic query has to *filter* on
        #: it and a gateway with no semantic tenants must never build the model.
        self.model_id = model_id_for(self.name, dimensions=dimensions)
        self._inner: Embedder | None = None
        #: Texts embedded so far. Read by the disabled-tenant proof.
        self.calls = 0

    def resolve(self) -> Embedder:
        """Build **and load** the embedder, or raise ``ConfigurationError``.

        The ``load()`` call is the whole point and it was missing at first: constructing
        a :class:`BGEEmbedder` touches no weight file, so a ``resolve`` that only built
        one returned happily on a container with no ``sentence-transformers`` installed
        and ``PUT /admin/cache {"mode": "semantic"}`` answered 200. The end-to-end
        container run in ``docs/PHASE_LOG.md`` is what caught it. A probe that does not
        probe is worse than no probe, because it reads as a guarantee.

        ``_inner`` is assigned only after a successful load, so a retry after a fixed
        environment really retries rather than returning a half-built object.
        """
        if self._inner is None:
            built = build_embedder(self.name, dimensions=self.dimensions)
            if built.dimensions != self.dimensions:  # pragma: no cover - guard
                raise ConfigurationError(
                    f"embedder {self.name!r} produces {built.dimensions} dimensions; "
                    f"the schema stores vector({self.dimensions})"
                )
            built.load()
            self._inner = built
        return self._inner

    def load(self) -> None:
        """Satisfy :class:`Embedder`; the work is :meth:`resolve`'s."""
        self.resolve()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.calls += len(texts)
        return self.resolve().embed(texts)

    @property
    def loaded(self) -> bool:
        """Whether the model has actually been built. Read by the zero-compute proof."""
        return self._inner is not None


def model_id_for(name: str, *, dimensions: int = 384) -> str:
    """The namespace value a given embedder name stamps on rows, without building it.

    For a real model the name *is* the id. The hashing embedder's id carries a version
    and its width instead, because two hashing embedders of different widths are two
    different vector spaces wearing one name.
    """
    if name == HASHING_EMBEDDER:
        return HashingEmbedder(dimensions=dimensions).model_id
    return name


def load_embedder(name: str | None = None, *, dimensions: int = 384) -> LazyEmbedder:
    """The gateway's embedder handle, from ``HEADROOM_EMBEDDER`` unless told otherwise."""
    return LazyEmbedder(name, dimensions=dimensions)
