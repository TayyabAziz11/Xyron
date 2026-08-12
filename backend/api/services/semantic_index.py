"""
semantic_index.py — Embedding + FAISS vector index for semantic filesystem search.

Lets Xyron resolve "open my tax file" / "open the logo" without exact
filenames. Reuses the same all-MiniLM-L6-v2 model/device pattern already
used by intent_router.py's Tier-3 classifier so we don't double-load a
second copy of sentence-transformers on the GPU.

Vector IDs == fs_index `entries.id` (via faiss.IndexIDMap2), so no separate
id-mapping table is needed — a FAISS hit is a direct row lookup.

Persistence: ~/.ai-operator/fs_semantic.index (binary FAISS index).
Thread safety: a single write lock guards add/remove/save; search is
read-only against an in-memory index so it never blocks on that lock.

Logs: [SEMANTIC_INDEX_READY] [SEMANTIC_INDEX_ADD] [SEMANTIC_INDEX_REMOVE]
      [SEMANTIC_INDEX_SEARCH] [SEMANTIC_INDEX_SAVE] [SEMANTIC_INDEX_LOAD_FAIL]
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

EMBED_DIM = 384  # all-MiniLM-L6-v2 output dimension
INDEX_DIR = Path.home() / ".ai-operator"
INDEX_PATH = INDEX_DIR / "fs_semantic.index"

_MODEL_NAME = "all-MiniLM-L6-v2"


class SemanticIndex:
    """Singleton FAISS-backed semantic index over fs_index entries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._np = None
        self._index = None
        self._ready = False
        self._load_failed_permanently = False
        INDEX_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Lazy model + index loading — never blocks import time.
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        if self._ready:
            return True
        if self._load_failed_permanently:
            return False

        with self._lock:
            if self._ready:
                return True
            # Defer this constructor (heavy, mostly-single-threaded tensor/
            # tokenizer deserialization that can starve the asyncio event
            # loop of the GIL — see gpu_coordinator.py) if a voice session's
            # TTS/STT is actively in flight right now. Cooperative only —
            # proceeds regardless after a bounded wait.
            try:
                from api.services.gpu_coordinator import wait_for_voice_idle as _wait_voice_idle
                _wait_voice_idle("semantic_index", timeout=15.0)
            except Exception:
                pass
            try:
                import numpy as np
                import torch
                import faiss
                from sentence_transformers import SentenceTransformer

                device = "cuda" if torch.cuda.is_available() else "cpu"
                try:
                    model = SentenceTransformer(_MODEL_NAME, device=device, local_files_only=True)
                except Exception:
                    model = SentenceTransformer(_MODEL_NAME, device=device)

                base = faiss.IndexFlatIP(EMBED_DIM)
                index = faiss.IndexIDMap2(base)

                if INDEX_PATH.exists():
                    try:
                        loaded = faiss.read_index(str(INDEX_PATH))
                        index = loaded
                    except Exception as exc:
                        logger.warning("[SEMANTIC_INDEX_LOAD_FAIL] %s — starting fresh", exc)

                self._model = model
                self._np = np
                self._index = index
                self._faiss = faiss
                self._ready = True
                logger.info(
                    "[SEMANTIC_INDEX_READY] device=%s vectors=%d",
                    device, index.ntotal,
                )
                return True
            except Exception:
                logger.exception("[SEMANTIC_INDEX] failed to initialize — semantic search disabled")
                self._load_failed_permanently = True
                return False

    @property
    def is_ready(self) -> bool:
        return self._ready

    # ------------------------------------------------------------------
    # Embedding helper
    # ------------------------------------------------------------------

    def _embed(self, texts: List[str]):
        vecs = self._model.encode(
            texts, show_progress_bar=False, convert_to_numpy=True, normalize_embeddings=True
        )
        return vecs.astype(self._np.float32, copy=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, entry_id: int, text: str) -> bool:
        """Add or replace the embedding for a single entry_id."""
        if not text or not self._ensure_loaded():
            return False
        with self._lock:
            try:
                vec = self._embed([text])
                ids = self._np.array([entry_id], dtype="int64")
                # Remove any existing vector for this id first (IDMap2 supports
                # duplicate-safe replace via remove_ids then add).
                self._index.remove_ids(ids)
                self._index.add_with_ids(vec, ids)
                logger.debug("[SEMANTIC_INDEX_ADD] entry_id=%d", entry_id)
                return True
            except Exception as exc:
                logger.debug("[SEMANTIC_INDEX] add failed for entry_id=%d: %s", entry_id, exc)
                return False

    def add_batch(self, items: List[Tuple[int, str]]) -> int:
        """Bulk add — used during full rebuilds. Returns count added."""
        items = [(i, t) for i, t in items if t]
        if not items or not self._ensure_loaded():
            return 0
        with self._lock:
            try:
                ids = self._np.array([i for i, _ in items], dtype="int64")
                vecs = self._embed([t for _, t in items])
                self._index.remove_ids(ids)
                self._index.add_with_ids(vecs, ids)
                logger.info("[SEMANTIC_INDEX_ADD] batch=%d total=%d", len(items), self._index.ntotal)
                return len(items)
            except Exception:
                logger.exception("[SEMANTIC_INDEX] batch add failed")
                return 0

    def remove(self, entry_id: int) -> None:
        if not self._ready:
            return
        with self._lock:
            try:
                self._index.remove_ids(self._np.array([entry_id], dtype="int64"))
                logger.debug("[SEMANTIC_INDEX_REMOVE] entry_id=%d", entry_id)
            except Exception as exc:
                logger.debug("[SEMANTIC_INDEX] remove failed for entry_id=%d: %s", entry_id, exc)

    def search(self, query: str, k: int = 20) -> List[Tuple[int, float]]:
        """Return [(entry_id, cosine_score), ...] sorted best-first."""
        if not query or not self._ensure_loaded() or self._index.ntotal == 0:
            return []
        try:
            qvec = self._embed([query])
            scores, ids = self._index.search(qvec, min(k, self._index.ntotal))
            results = [
                (int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1
            ]
            logger.debug("[SEMANTIC_INDEX_SEARCH] query=%r hits=%d", query, len(results))
            return results
        except Exception:
            logger.exception("[SEMANTIC_INDEX] search failed")
            return []

    def save(self) -> None:
        if not self._ready:
            return
        with self._lock:
            try:
                self._faiss.write_index(self._index, str(INDEX_PATH))
                logger.info("[SEMANTIC_INDEX_SAVE] vectors=%d path=%s", self._index.ntotal, INDEX_PATH)
            except Exception:
                logger.exception("[SEMANTIC_INDEX] save failed")

    @property
    def count(self) -> int:
        return int(self._index.ntotal) if self._ready else 0


semantic_index = SemanticIndex()
