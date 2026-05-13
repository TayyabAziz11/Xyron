"""
Semantic memory — ChromaDB vector store backed by all-MiniLM-L6-v2.

Persist location: ~/.ai-operator/chroma
Collection:       xyron_memories
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PERSIST_DIR = Path.home() / ".ai-operator" / "chroma"
_COLLECTION  = "xyron_memories"
_MODEL       = "all-MiniLM-L6-v2"


class SemanticMemoryStore:

    def __init__(self) -> None:
        self._client     = None
        self._collection = None
        self._ready      = False
        self._init()

    def _init(self) -> None:
        try:
            import chromadb
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(_PERSIST_DIR))
            ef = SentenceTransformerEmbeddingFunction(model_name=_MODEL)
            self._collection = self._client.get_or_create_collection(
                name=_COLLECTION,
                embedding_function=ef,
            )
            self._ready = True
            logger.info("[SemanticStore] ready — collection=%s count=%d",
                        _COLLECTION, self._collection.count())
        except Exception as exc:
            logger.error("[SemanticStore] init failed: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def remember(self, text: str, metadata: dict[str, Any] | None = None) -> str:
        """Embed and store text. Returns the generated memory id."""
        if not self._ready:
            logger.warning("[SemanticStore] not ready — remember() skipped")
            return ""
        mem_id   = str(uuid.uuid4())
        meta_out = {k: str(v) for k, v in (metadata or {}).items()}
        try:
            self._collection.add(
                documents=[text],
                metadatas=[meta_out],
                ids=[mem_id],
            )
            logger.debug("[SemanticStore] stored id=%s text=%r", mem_id, text[:60])
        except Exception as exc:
            logger.error("[SemanticStore] remember error: %s", exc)
        return mem_id

    def recall(self, query: str, n: int = 5) -> list[dict]:
        """Return the n most semantically similar memories."""
        if not self._ready:
            return []
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(n, max(self._collection.count(), 1)),
            )
            ids       = results.get("ids",       [[]])[0]
            docs      = results.get("documents", [[]])[0]
            metas     = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]
            return [
                {
                    "id":       ids[i],
                    "text":     docs[i],
                    "metadata": metas[i],
                    "distance": round(distances[i], 4),
                }
                for i in range(len(ids))
            ]
        except Exception as exc:
            logger.error("[SemanticStore] recall error: %s", exc)
            return []

    def forget(self, memory_id: str) -> None:
        """Delete a stored memory by id."""
        if not self._ready:
            return
        try:
            self._collection.delete(ids=[memory_id])
            logger.debug("[SemanticStore] deleted id=%s", memory_id)
        except Exception as exc:
            logger.error("[SemanticStore] forget error: %s", exc)

    def count(self) -> int:
        if not self._ready:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0


semantic_store = SemanticMemoryStore()
