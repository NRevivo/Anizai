"""
Build the Keyword Sniper reference vector for Phase 7B semantic rescue.

Embeds every term in the post-A1 MASTER_KEYWORD_LIST with OpenAI's
text-embedding-3-small model, mean-pools the term vectors to a single
1536-dim centroid vector, and saves the result to SNIPER_REFERENCE_VECTOR_PATH.

The reference vector is loaded once at Gold Job startup by
GlobalNewsGoldFunction.open() and used to compute cosine similarity with
low-signal articles during semantic rescue (B1, B2, §4.1A Phase 7B).

Usage (from data-pipeline/ with venv active):
    python processing/build_sniper_reference_vector.py

Commit the resulting sniper_reference_vector.npy to the repo (T7B.6).
Re-run whenever MASTER_KEYWORD_LIST is materially changed — the SHA256
of the keyword set printed here can be compared against a stored baseline
to detect staleness (B2 drift mitigation).

Cost: ~600 tokens at text-embedding-3-small pricing ≈ $0.000012 per run.
Idempotent — safe to re-run; overwrites the existing file.

References:
    - Phase 7B decisions B1, B2, B4 (phase7_intelligent_filtering.md)
    - Section 4.1A: Keyword Sniper Filter
    - Section 3.3:  Service Isolation — reads processing/ only
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

# Allow running from data-pipeline/ root
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import OPENAI_API_KEY
from processing.keyword_sniper import MASTER_KEYWORD_LIST, SNIPER_REFERENCE_VECTOR_PATH


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_of_keyword_set(keywords: frozenset[str]) -> str:
    """Stable hash of the keyword set — use to detect MASTER_KEYWORD_LIST drift."""
    joined = "\n".join(sorted(keywords))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def build_reference_vector() -> np.ndarray:
    """
    Embed all MASTER_KEYWORD_LIST terms and mean-pool into a 1536-dim vector.

    Why mean-pooling: the centroid of the keyword embeddings represents the
    'average semantic direction' of the domain vocabulary. An article that is
    semantically close to this centroid is likely domain-relevant even if it
    misses exact keyword matches — the basis for semantic rescue (B1).

    Why text-embedding-3-small: same model used for knowledge_vectors embeddings
    (Section 4.2A, Gold Job). Consistent model ensures the cosine similarity
    distribution is stable across the rescue threshold tuning (B3).
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("[build_sniper_vec] ERROR: openai package not installed.")
        sys.exit(1)

    if not OPENAI_API_KEY:
        print("[build_sniper_vec] ERROR: OPENAI_API_KEY not set in .env.")
        sys.exit(1)

    client = OpenAI(api_key=OPENAI_API_KEY)

    terms = sorted(MASTER_KEYWORD_LIST)
    print(f"[build_sniper_vec] Embedding {len(terms)} keywords ...")
    print(f"[build_sniper_vec] Keyword set SHA256 : {_sha256_of_keyword_set(MASTER_KEYWORD_LIST)}")

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=terms,
    )

    total_tokens = response.usage.total_tokens
    print(f"[build_sniper_vec] Tokens used        : {total_tokens}")

    vectors = np.array(
        [item.embedding for item in response.data],
        dtype=np.float32,
    )  # shape: (n_terms, 1536)

    # Mean-pool — centroid of the keyword embedding space
    ref_vector = vectors.mean(axis=0)  # shape: (1536,)

    # L2-normalize the reference vector so cosine similarity
    # reduces to a dot product at inference time (optional optimisation)
    norm = np.linalg.norm(ref_vector)
    if norm > 0:
        ref_vector /= norm

    return ref_vector


def main() -> None:
    print("=" * 60)
    print("Keyword Sniper Reference Vector Builder  [T7B.5/T7B.6]")
    print(f"Output: {SNIPER_REFERENCE_VECTOR_PATH}")
    print("=" * 60)

    ref_vector = build_reference_vector()

    np.save(str(SNIPER_REFERENCE_VECTOR_PATH), ref_vector)
    print(f"[build_sniper_vec] Saved to             : {SNIPER_REFERENCE_VECTOR_PATH}")

    file_sha = _sha256_of_file(SNIPER_REFERENCE_VECTOR_PATH)
    print(f"[build_sniper_vec] File SHA256          : {file_sha}")
    print(f"[build_sniper_vec] Vector shape         : {ref_vector.shape}")
    print(f"[build_sniper_vec] Vector dtype         : {ref_vector.dtype}")
    print(f"[build_sniper_vec] Vector L2-norm       : {np.linalg.norm(ref_vector):.6f}")
    print("=" * 60)
    print("DONE — commit processing/sniper_reference_vector.npy (T7B.6)")


if __name__ == "__main__":
    main()
