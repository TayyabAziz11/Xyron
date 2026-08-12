"""Phase 1 System Intelligence — filesystem search API (substring + semantic)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/fs", tags=["filesystem"])


@router.get("/search")
async def search(q: str, type: str | None = None, drive: str | None = None, limit: int = 10):
    """Fast substring/fuzzy filename search (existing fs_index engine)."""
    from ..services.fs_index import fs_index

    if not fs_index.is_ready:
        return {"ready": False, "results": []}
    hits = fs_index.search_ranked(q, type_filter=type, drive=drive, limit=limit)
    return {
        "ready": True,
        "results": [{"path": str(p), "score": round(s, 3)} for s, p in hits],
    }


@router.get("/semantic_search")
async def semantic_search(q: str, limit: int = 10, active_folder: str | None = None):
    """Content-aware semantic search: 'open my tax file', 'open the logo', etc."""
    from ..services.fs_index import fs_index
    from ..services.semantic_index import semantic_index

    if not semantic_index.is_ready and semantic_index.count == 0:
        # Trigger lazy load so first call after boot doesn't just 404-empty.
        semantic_index.search("", k=1)

    hits = fs_index.search_semantic_ranked(q, limit=limit, active_folder=active_folder)
    return {
        "results": [
            {"path": str(p), "score": round(s, 3), "breakdown": b}
            for s, p, b in hits
        ]
    }


@router.get("/stats")
async def stats():
    """Index health — entry counts, embedded-file count, readiness."""
    from ..services.fs_index import fs_index, SEMANTIC_ROOTS, _get_thread_conn
    from ..services.semantic_index import semantic_index

    conn = _get_thread_conn(fs_index._db_path)
    total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    files = conn.execute("SELECT COUNT(*) FROM entries WHERE type='file'").fetchone()[0]
    with_content = conn.execute("SELECT COUNT(*) FROM entries WHERE has_content=1").fetchone()[0]

    return {
        "ready": fs_index.is_ready,
        "total_entries": total,
        "files": files,
        "files_with_content": with_content,
        "embedded_vectors": semantic_index.count,
        "semantic_roots": [str(r) for r in SEMANTIC_ROOTS],
    }


@router.post("/reindex")
async def reindex():
    """Trigger a full index rebuild in the background (debug/admin use)."""
    import asyncio
    from ..services.fs_index import fs_index

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, fs_index._rebuild)
    return {"status": "rebuild_started"}
