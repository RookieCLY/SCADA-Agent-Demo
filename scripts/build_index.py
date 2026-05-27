"""Offline Tool-RAG index builder.

Persists a compact, deterministic snapshot of the Tool RAG corpus + scoring
artefacts so the orchestrator can reload the same view repeatedly without
re-encoding. The default ``HashingTfIdfEncoder`` is fast enough that
in-process re-encoding is fine; this script is therefore primarily a
debugging / reproducibility aid:

* dumps the per-atomic-tool *document text* (so reviewers can see exactly
  what was indexed)
* dumps the dense matrix + BM25 inverted-index summary
* writes a hash file so CI can detect drift in the index between commits

Usage::

    python -m scripts.build_index --out indices/default
    python -m scripts.build_index --out indices/default --force

Idempotent — re-running with no flag changes is a no-op (we compare hashes
before writing). With ``--force`` we always rewrite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

from agent.tool_rag import HashingTfIdfEncoder, ToolIndex, build_index_from_registry
from agent.tool_registry import build_default_registry


def _hash_payload(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the offline Tool-RAG index.")
    parser.add_argument(
        "--out", default="indices/default", help="Output directory (created if missing)"
    )
    parser.add_argument("--dim", type=int, default=512, help="Hashing-encoder dimension")
    parser.add_argument(
        "--force", action="store_true", help="Re-write even if the corpus hash matches"
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- build ----
    registry = build_default_registry()
    encoder = HashingTfIdfEncoder(dim=args.dim)
    index: ToolIndex = build_index_from_registry(registry, encoder=encoder)

    # ---- corpus payload ----
    docs = [
        {
            "name": m.name,
            "domain": m.domain,
            "action": m.action,
            "description": m.description,
            "examples": list(m.examples),
            "text": index.docs[i],
        }
        for i, m in enumerate(index.atomics)
    ]
    corpus_json = json.dumps(docs, ensure_ascii=False, sort_keys=True, indent=2)
    corpus_hash = _hash_payload(corpus_json.encode("utf-8"))

    # ---- idempotency check ----
    hash_path = out_dir / "corpus.sha256"
    if hash_path.exists() and not args.force:
        prev = hash_path.read_text(encoding="utf-8").strip()
        if prev == corpus_hash:
            print(f"[index] up to date (hash={corpus_hash[:24]}…)  → no write")
            return 0

    # ---- write artefacts ----
    (out_dir / "corpus.json").write_text(corpus_json, encoding="utf-8")
    np.save(out_dir / "dense.npy", index.dense_matrix.astype(np.float32))
    bm25_summary = {
        "doc_count": len(index.token_docs),
        "avg_doc_len": float(np.mean([len(d) for d in index.token_docs])),
        "vocab_size": len({tok for doc in index.token_docs for tok in doc}),
        "dim": encoder.dim,
        "encoder": encoder.__class__.__name__,
    }
    (out_dir / "stats.json").write_text(
        json.dumps(bm25_summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    hash_path.write_text(corpus_hash + "\n", encoding="utf-8")

    print(
        f"[index] wrote {len(docs)} docs to {out_dir}/ — "
        f"vocab={bm25_summary['vocab_size']}  dim={encoder.dim}  "
        f"hash={corpus_hash[:24]}…"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
