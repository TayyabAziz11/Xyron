"""
Build / rebuild the ChromaDB intent index used by semantic_understanding.py (Tier 2).

Run once after setup, and again any time intent_examples.py changes:
  python3 backend/scripts/build_brain_intent_index.py

Uses nomic-embed-text via Ollama. Requires:
  ollama pull nomic-embed-text
  pip install chromadb
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

# Make backend importable
BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

from brain.intent_examples import INTENT_EXAMPLES

CHROMA_PATH = BACKEND / "data" / "chroma"
COLLECTION  = "brain_intents"
OLLAMA_URL  = "http://localhost:11434/api/embeddings"


def embed(text: str) -> list[float]:
    payload = json.dumps({"model": "nomic-embed-text", "prompt": text}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["embedding"]


def main() -> None:
    import chromadb

    print(f"[build_index] chromadb at {CHROMA_PATH}")
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    # Delete existing collection to rebuild clean
    try:
        client.delete_collection(COLLECTION)
        print(f"[build_index] deleted existing '{COLLECTION}' collection")
    except Exception:
        pass

    col = client.create_collection(name=COLLECTION, metadata={"hnsw:space": "cosine"})
    print(f"[build_index] created collection '{COLLECTION}'")
    print(f"[build_index] embedding {len(INTENT_EXAMPLES)} examples...")

    ids, embeddings, docs, metas = [], [], [], []
    errors = 0

    for i, ex in enumerate(INTENT_EXAMPLES):
        try:
            vec = embed(ex.utterance)
            ids.append(f"intent_{i:04d}")
            embeddings.append(vec)
            docs.append(ex.utterance)
            metas.append({
                "intent":       ex.intent,
                "route":        ex.route,
                "target":       ex.target,
                "emotion_hint": ex.emotion_hint,
            })
            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(INTENT_EXAMPLES)} embedded...")
        except Exception as exc:
            print(f"  [WARN] failed to embed '{ex.utterance[:40]}': {exc}")
            errors += 1
        time.sleep(0.02)  # gentle rate limiting

    if ids:
        col.add(ids=ids, embeddings=embeddings, documents=docs, metadatas=metas)
        print(f"[build_index] added {len(ids)} vectors ({errors} errors)")
    else:
        print("[build_index] ERROR: no vectors added")
        sys.exit(1)

    # Quick verification
    test = embed("open chrome")
    results = col.query(query_embeddings=[test], n_results=3)
    print("[build_index] verification query 'open chrome':")
    for j, (doc, dist) in enumerate(zip(results["documents"][0], results["distances"][0])):
        print(f"  {j+1}. [{1-dist:.3f}] {doc}")

    print(f"\n[build_index] DONE. Index ready at {CHROMA_PATH}")


if __name__ == "__main__":
    main()
