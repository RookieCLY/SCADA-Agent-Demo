"""Tool RAG — Hybrid (BM25 + dense) ranking with optional rerank.

Design constraints
==================

1. **Importable without heavy deps.** ``rank_bm25`` is the only mandatory dep
   beyond ``numpy``. ``sentence-transformers`` / ``chromadb`` stay optional —
   the module is built so the experiment can run end-to-end without ever
   downloading a model. Phase 4 swaps in bge-m3 by flipping a flag.

2. **Deterministic dense encoder.** When no real embedder is available we use
   a **hashing TF-IDF** projection (numpy-only) that:
     * hashes tokens to a fixed-width vector (default ``dim=512``)
     * weights by ``1 + log(tf)`` to mute repetition
     * L2-normalises so cosine similarity reduces to a dot product

   It's not bge-m3 quality, but it's **reproducible**, **fast**, and **good
   enough** to satisfy the Phase-2 verification target ("Top-K召回中黄金 Tool
   命中率 ≥ 80% on the dev set"). When sentence-transformers is installed and
   a model id is configured, we swap the implementation by passing a different
   encoder to ``ToolIndex.build``.

3. **State-machine hard filter is layered above ranking.** RAG never tries to
   answer "should this Tool be visible in state X" — that question is
   black-and-white. The orchestrator intersects RAG output with the
   state-machine whitelist (the `select_tools` helper does this in one call).

4. **Indexed text** is ``name + description + " ".join(examples)`` per atomic.
   Domain-tool ranking is obtained by *aggregating* its child atomics
   (max-pool similarity), so hierarchical and flat configs share a single
   underlying index — no double bookkeeping.

Public surface
==============

* ``ToolIndex`` — index over atomic tools; ``rank(query, candidates) → list[Score]``
* ``HashingTfIdfEncoder`` — default dense encoder
* ``simple_rerank`` — cheap deterministic re-scorer
* ``select_tools`` — convenience helper combining hard filter + soft rank
"""
from __future__ import annotations

import math
import re
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from rank_bm25 import BM25Okapi

from agent.tool_registry import ToolMeta, ToolRegistry

# ============================================================ tokenisation
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")  # alnum-ish + each CJK char


def tokenise(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


# ============================================================ encoders
class DenseEncoder(Protocol):
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray: ...


@dataclass
class HashingTfIdfEncoder:
    """Deterministic dense encoder — pure numpy, no model downloads."""

    dim: int = 512
    seed: int = 1337

    def _hash(self, token: str) -> int:
        # Stable, salted FNV-1a like reduction
        h = 2166136261 ^ self.seed
        for c in token.encode("utf-8"):
            h ^= c
            h = (h * 16777619) & 0xFFFFFFFF
        return h % self.dim

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            toks = tokenise(t)
            if not toks:
                continue
            tf: dict[int, int] = {}
            for tok in toks:
                idx = self._hash(tok)
                tf[idx] = tf.get(idx, 0) + 1
            for idx, c in tf.items():
                out[i, idx] = 1.0 + math.log(c)
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out


# ============================================================ score record
@dataclass(frozen=True)
class ScoredTool:
    name: str
    score: float
    dense: float
    sparse: float
    domain: str
    action: str

    def to_dict(self) -> dict[str, float | str]:
        return {
            "name": self.name,
            "domain": self.domain,
            "action": self.action,
            "score": round(self.score, 4),
            "dense": round(self.dense, 4),
            "sparse": round(self.sparse, 4),
        }


# ============================================================ ToolIndex
class ToolIndex:
    """Hybrid (BM25 + dense) over atomic tools.

    Build once at startup (see ``scripts/build_index.py``) or reuse the live
    registry directly — both paths produce identical scores.
    """

    def __init__(
        self,
        atomics: list[ToolMeta],
        encoder: DenseEncoder | None = None,
    ) -> None:
        if not atomics:
            raise ValueError("ToolIndex needs at least one atomic tool")
        self.atomics = list(atomics)
        self.names = [m.name for m in self.atomics]
        self.index_of = {n: i for i, n in enumerate(self.names)}

        # ---- corpus text -------------------------------------------------
        self.docs = [
            self._compose_text(m) for m in self.atomics
        ]
        self.token_docs = [tokenise(d) for d in self.docs]

        # ---- sparse: BM25 ------------------------------------------------
        self.bm25 = BM25Okapi(self.token_docs)

        # ---- dense -------------------------------------------------------
        self.encoder = encoder or HashingTfIdfEncoder()
        self.dense_matrix = self.encoder.encode(self.docs)

        # ---- per-query channel-score cache -------------------------------
        # The dense matmul and BM25 scoring run over the *whole* corpus and
        # depend only on the query, not on the per-turn candidate set. Within a
        # single agent run the query is constant, so recomputing them every turn
        # (up to max_turns times) is pure waste. Cache the normalised channels
        # keyed on the query; bound the cache so a long-lived index reused
        # across many queries can't grow without limit.
        self._channel_cache: OrderedDict[str, tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._channel_cache_max = 16

    # ----------------------------------------- corpus composition
    @staticmethod
    def _compose_text(m: ToolMeta) -> str:
        # name occurs first and twice — that's a controlled bias that lets a
        # query mentioning the exact tool name win even at low BM25 weight.
        parts: list[str] = [m.name, m.name.replace("_", " "), m.description]
        parts.extend(m.examples or [])
        # also append the schema field names so "high_limit" matches
        try:
            schema = m.args_model.model_json_schema()
            props = list((schema.get("properties") or {}).keys())
            parts.extend(props)
        except Exception:  # pragma: no cover — pydantic schema is always available
            pass
        return " | ".join(p for p in parts if p)

    # ----------------------------------------- query encoding
    def _dense_scores(self, query: str) -> np.ndarray:
        qv = self.encoder.encode([query])[0]
        return self.dense_matrix @ qv

    def _sparse_scores(self, query: str) -> np.ndarray:
        return np.asarray(self.bm25.get_scores(tokenise(query)), dtype=np.float32)

    @staticmethod
    def _minmax(arr: np.ndarray) -> np.ndarray:
        lo, hi = float(arr.min()), float(arr.max())
        if hi - lo < 1e-9:
            return np.zeros_like(arr)
        return (arr - lo) / (hi - lo)

    def _normalised_channels(self, query: str) -> tuple[np.ndarray, np.ndarray]:
        """Return the min-max-normalised (dense, sparse) score vectors for the
        whole corpus, memoised per query. These are candidate-independent, so a
        run that filters different candidate sets each turn still hits the cache.
        """
        cached = self._channel_cache.get(query)
        if cached is not None:
            self._channel_cache.move_to_end(query)
            return cached
        dn = self._minmax(self._dense_scores(query))
        sn = self._minmax(self._sparse_scores(query))
        self._channel_cache[query] = (dn, sn)
        if len(self._channel_cache) > self._channel_cache_max:
            self._channel_cache.popitem(last=False)
        return dn, sn

    # ----------------------------------------- ranking
    def rank(
        self,
        query: str,
        *,
        candidates: Iterable[str] | None = None,
        top_n: int = 30,
        alpha: float = 0.6,
    ) -> list[ScoredTool]:
        """Return the top-N atomic tools for the query.

        Parameters
        ----------
        candidates : optional set of atomic names — typically the state-machine
            whitelist. ``None`` means "all atomics".
        alpha : weight on the dense channel; ``score = α·dense + (1−α)·sparse``.
        top_n : truncation.
        """
        dn, sn = self._normalised_channels(query)
        hybrid = alpha * dn + (1.0 - alpha) * sn

        # §4.2.4: an exact whole tool-name mention in the query must be recalled,
        # not diluted out by a large corpus. Both channels are min-max
        # normalised to [0, 1], so a +1.0 bump guarantees such a tool clears the
        # noise floor and survives Top-N truncation (the reranker then pins it).
        q_tokens = set(tokenise(query))
        if q_tokens:
            for i, name in enumerate(self.names):
                if name in q_tokens:
                    hybrid[i] += 1.0

        keep = set(self.names) if candidates is None else set(candidates)
        scored: list[ScoredTool] = []
        for i, name in enumerate(self.names):
            if name not in keep:
                continue
            scored.append(
                ScoredTool(
                    name=name,
                    score=float(hybrid[i]),
                    dense=float(dn[i]),
                    sparse=float(sn[i]),
                    domain=self.atomics[i].domain,
                    action=self.atomics[i].action,
                )
            )
        scored.sort(key=lambda s: (-s.score, s.name))
        return scored[:top_n]

    # ----------------------------------------- domain-level view
    def rank_domains(
        self,
        query: str,
        *,
        candidate_atomics: Iterable[str] | None = None,
        top_n: int = 10,
        alpha: float = 0.6,
    ) -> list[ScoredTool]:
        """Aggregate atomic scores up to the domain by max-pool.

        The result reuses ``ScoredTool`` but the ``name`` is the *domain* and
        ``action`` is the best-scoring atomic action under that domain.
        """
        atom_scores = self.rank(
            query,
            candidates=candidate_atomics,
            top_n=len(self.atomics),
            alpha=alpha,
        )
        per_domain: dict[str, ScoredTool] = {}
        for s in atom_scores:
            cur = per_domain.get(s.domain)
            if cur is None or s.score > cur.score:
                per_domain[s.domain] = ScoredTool(
                    name=s.domain,
                    score=s.score,
                    dense=s.dense,
                    sparse=s.sparse,
                    domain=s.domain,
                    action=s.action,
                )
        ordered = sorted(per_domain.values(), key=lambda s: (-s.score, s.name))
        return ordered[:top_n]


# ============================================================ reranker
def simple_rerank(
    query: str,
    scored: list[ScoredTool],
    *,
    name_boost: float = 0.40,
    overlap_weight: float = 0.05,
) -> list[ScoredTool]:
    """Cheap deterministic re-scorer.

    + name_boost  — if the tool *name* literally appears in the query as a
      whole token, add a large constant. The intent is that an LLM (or a
      developer) saying ``"please invoke create_analog_alarm now"`` should
      always end up at rank 1 regardless of BM25 noise. The boost is large
      enough to clear the ~0.4 normalised-BM25 gap between adjacent docs.
    + overlap_weight — fraction of query tokens covered by the tool's
      description/examples (already in the dense+sparse channels, but a tiny
      lexical bump fixes a class of ties).

    The reranker preserves order *between* equal scores so downstream tests
    that key off candidate identity stay deterministic.
    """
    q_tokens = set(tokenise(query))
    if not q_tokens:
        return list(scored)
    boosted: list[ScoredTool] = []
    for s in scored:
        bump = 0.0
        # Whole-name match (e.g. query contains "create_analog_alarm")
        if s.name in q_tokens:
            bump += name_boost
        # Partial-name overlap (e.g. "alarm" matches "create_analog_alarm" sub-tokens)
        elif any(tok in q_tokens for tok in s.name.split("_")):
            bump += name_boost * 0.25
        atom_tokens = set(tokenise(s.name) + tokenise(s.action) + tokenise(s.domain))
        if atom_tokens:
            overlap = len(q_tokens & atom_tokens) / max(len(q_tokens), 1)
            bump += overlap_weight * overlap
        boosted.append(
            ScoredTool(
                name=s.name,
                score=s.score + bump,
                dense=s.dense,
                sparse=s.sparse,
                domain=s.domain,
                action=s.action,
            )
        )
    boosted.sort(key=lambda s: (-s.score, s.name))
    return boosted


# ============================================================ orchestrator helper
def select_tools(
    query: str,
    *,
    index: ToolIndex,
    allowed_atomics: Iterable[str] | None,
    top_n: int = 30,
    top_k: int = 12,
    alpha: float = 0.6,
    use_reranker: bool = True,
) -> list[ScoredTool]:
    """Hard filter ∘ soft rank ∘ optional rerank ∘ truncate to top_k.

    ``allowed_atomics`` may be ``None`` to disable hard filtering (i.e. the
    state machine is off). The caller is responsible for projecting the result
    up to Domain Tools when hierarchical mode is on.
    """
    ranked = index.rank(query, candidates=allowed_atomics, top_n=top_n, alpha=alpha)
    if use_reranker:
        ranked = simple_rerank(query, ranked)
    return ranked[:top_k]


# ============================================================ factory
def build_index_from_registry(
    registry: ToolRegistry,
    encoder: DenseEncoder | None = None,
) -> ToolIndex:
    return ToolIndex(atomics=registry.all_atomics(), encoder=encoder)


__all__ = [
    "DenseEncoder",
    "HashingTfIdfEncoder",
    "ScoredTool",
    "ToolIndex",
    "build_index_from_registry",
    "select_tools",
    "simple_rerank",
    "tokenise",
]
