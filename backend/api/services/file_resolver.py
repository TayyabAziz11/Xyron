"""
file_resolver.py — Phase 1.5: Context-Aware Filesystem resolution engine.

Turns smart_open from "search a semantic index" into "behave like a human
assistant" — a strict priority cascade tries the *context* Xyron already
has before ever touching the filename/semantic indexes:

  0. Learned resolution   (exact query the user has confirmed before — NEW,
                            justified by the "Usage Learning" mandate: a
                            repeatedly-confirmed choice should be promoted
                            above every other signal, not just re-ranked)
  1. Current project/workspace   (VS Code/Visual Studio window's open folder)
  2. Current folder               (focused Explorer window's real path)
  3. Recent files                 (fs_index.last_opened, newest first)
  4. Frequently opened files      (fs_index.open_count, highest first)
  5. Recent conversation context  (memory_service last_file/last_folder/last_action)
  6. Active application context   (foreground app's typical file extensions)
  7. Current screen context       (stub — Phase 2 not implemented yet)
  8. Semantic index               (fs_index.search_semantic_ranked)
  9. Filename/path index          (fs_index.search_ranked — existing OS-wide fuzzy)

Tier 10 (slow filesystem `find`) stays in system_tools.py — it's reached
only when this resolver returns decision="none" (nothing matched anywhere).

Each tier is only evaluated if the previous one failed to "clear" — produce
a candidate whose local match score beats that tier's threshold — so the
common case (repeat behavior, working in one project) short-circuits after
tier 0-2 and never touches the semantic/filename indexes at all. This is
what keeps the extra reasoning from slowing down deterministic commands.

Confidence model (applied to whichever tier clears, or the best overall
candidate if none clears):
    confidence = tier_prior[tier] + match_score * 0.5 + learned_boost + usage_affinity * 0.1
    >= 0.75  -> HIGH   -> open immediately
    0.45-0.75 -> MEDIUM -> ask for confirmation (reuses the existing
                           `confirm_required` yes/no gate in voice_ws.py)
    < 0.45   -> LOW    -> present ranked choices (reuses the existing
                           `multiple_matches` disambiguation gate)

Logs: [FILE_RESOLVER_TIER_CLEAR] [FILE_RESOLVER_DECISION] [FILE_RESOLVER_MS]
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_JUNK_PARTS = {
    "appdata", "cache", "temp", "temporary internet files",
    "node_modules", "programdata", "$recycle.bin", ".tmp", "__pycache__",
}

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic"}

# Base trust each tier contributes to confidence before match quality is
# even considered — earlier tiers are trusted more than raw string/semantic
# similarity, per the mandated priority order.
_TIER_PRIOR = {0: 0.55, 1: 0.35, 2: 0.30, 3: 0.20, 4: 0.15, 5: 0.15, 6: 0.10, 8: 0.05, 9: 0.0}

# Local match-score a tier's best candidate must clear to short-circuit the
# cascade. Semantic (8) runs numerically lower than rapidfuzz tiers, hence
# the lower bar — see the Phase 1 report's live test scores (~0.4-0.5 for
# clearly-correct matches).
_TIER_CLEAR = {1: 0.55, 2: 0.55, 3: 0.6, 4: 0.6, 5: 0.6, 6: 0.5, 8: 0.35, 9: 0.55}

_TIER_NAMES = {
    0: "learned", 1: "workspace", 2: "explorer_folder", 3: "recent",
    4: "frequent", 5: "conversation", 6: "active_app", 7: "screen",
    8: "semantic", 9: "filename_index",
}

HIGH_CONFIDENCE = 0.75
MEDIUM_CONFIDENCE = 0.45


@dataclass
class Candidate:
    path: Path
    score: float
    tier: int
    entry_id: Optional[int] = None


@dataclass
class ResolveResult:
    decision: str  # "open" | "confirm" | "choices" | "none"
    path: Optional[Path] = None
    confidence: float = 0.0
    tier: Optional[int] = None
    tier_name: Optional[str] = None
    candidates: list = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)
    snapshot: dict = field(default_factory=dict)


def _normalize_query(query: str) -> str:
    q = query.strip().lower()
    for suffix in (" file", " folder", " document", " video", " image"):
        if q.endswith(suffix):
            q = q[: -len(suffix)]
    return q.strip()


def _name_score(query_norm: str, name: str) -> float:
    name_l = name.lower()
    if name_l == query_norm:
        return 1.0
    if query_norm in name_l:
        return 0.9
    try:
        from rapidfuzz import fuzz
        return max(fuzz.token_set_ratio(query_norm, name_l), fuzz.partial_ratio(query_norm, name_l)) / 100.0
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, query_norm, name_l).ratio()


def _is_junk(path_str: str) -> bool:
    parts = path_str.lower().replace("\\", "/").split("/")
    return any(p in _JUNK_PARTS for p in parts)


def _filter_open_type(path: Path, open_type: str) -> bool:
    if open_type == "video":
        return path.suffix.lower() in VIDEO_EXTS
    if open_type == "image":
        return path.suffix.lower() in IMAGE_EXTS
    return True


def _type_filter_sql(open_type: str) -> Optional[str]:
    if open_type == "folder":
        return "folder"
    if open_type in ("file", "video", "image"):
        return "file"
    return None


# ---------------------------------------------------------------------------
# Context snapshot — fetched once per resolve() call, shared by every tier.
# ---------------------------------------------------------------------------

def get_context_snapshot() -> dict:
    """
    Thin wrapper over the World State Engine (world_state.py) — this used
    to independently call window_context/workspace_context/explorer_context/
    active_context itself; now it reads the single aggregated snapshot so
    file_resolver never queries a sensor directly. refresh=True because a
    resolve() in progress needs current data, not whatever the background
    refresh loop last happened to publish.
    """
    from .world_state import world_state
    return world_state.get_context(refresh=True)


# ---------------------------------------------------------------------------
# Per-tier candidate gathering
# ---------------------------------------------------------------------------

def _score_id_path_rows(rows: list, query_norm: str, tier: int, open_type: str) -> list[Candidate]:
    out = []
    for entry_id, path_str, *_ in rows:
        if _is_junk(path_str):
            continue
        p = Path(path_str)
        if not _filter_open_type(p, open_type):
            continue
        out.append(Candidate(path=p, score=_name_score(query_norm, p.name), tier=tier, entry_id=entry_id))
    return out


def _conversation_candidates(query_norm: str, open_type: str) -> list[Candidate]:
    try:
        from .memory_service import memory_service
        ctx = memory_service.get_context()
    except Exception:
        return []

    out = []
    for slot_name in ("last_file", "last_folder"):
        slot = ctx.get(slot_name)
        if not slot or not slot.get("path"):
            continue
        p = Path(slot["path"])
        if not _filter_open_type(p, open_type) or _is_junk(str(p)):
            continue
        out.append(Candidate(path=p, score=_name_score(query_norm, p.name), tier=5))
    return out


def _app_context_candidates(query_norm: str, snapshot: dict, open_type: str) -> list[Candidate]:
    """Boost recently-modified files matching the foreground app's typical extensions."""
    try:
        from .workspace_context import APP_EXTENSIONS
        from .fs_index import _get_thread_conn, fs_index
    except Exception:
        return []

    ws = snapshot.get("current_workspace")
    if not ws:
        return []
    exts = APP_EXTENSIONS.get(ws["app"])
    if not exts:
        return []

    try:
        cutoff = time.time() - 3 * 86400
        conn = _get_thread_conn(fs_index._db_path)
        rows = conn.execute(
            "SELECT id, path FROM entries WHERE type='file' AND modified_time > ? "
            "ORDER BY modified_time DESC LIMIT 500",
            (cutoff,),
        ).fetchall()
    except Exception:
        return []

    out = []
    for entry_id, path_str in rows:
        p = Path(path_str)
        if p.suffix.lower() not in exts or _is_junk(path_str) or not _filter_open_type(p, open_type):
            continue
        out.append(Candidate(path=p, score=_name_score(query_norm, p.name), tier=6, entry_id=entry_id))
    return out


# ---------------------------------------------------------------------------
# Confidence + finalization
# ---------------------------------------------------------------------------

def _entry_id_for(path: Path) -> Optional[int]:
    try:
        from .fs_index import _get_thread_conn, fs_index
        conn = _get_thread_conn(fs_index._db_path)
        row = conn.execute("SELECT id FROM entries WHERE path = ?", (str(path),)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _learned_boost(query_norm: str, path: Path) -> float:
    try:
        from .fs_index import _get_thread_conn, fs_index
        conn = _get_thread_conn(fs_index._db_path)
        row = conn.execute(
            "SELECT hits FROM learned_resolutions WHERE query_norm = ? AND path = ?",
            (query_norm, str(path)),
        ).fetchone()
        return min(0.3, 0.1 * row[0]) if row else 0.0
    except Exception:
        return 0.0


def _finalize(query_norm: str, candidates: list[Candidate], tier: int, snapshot: dict) -> ResolveResult:
    candidates = sorted(candidates, key=lambda c: -c.score)
    top = candidates[0]
    if top.entry_id is None:
        top.entry_id = _entry_id_for(top.path)

    usage_aff = 0.0
    if top.entry_id is not None:
        try:
            from .fs_index import fs_index
            aff = fs_index.get_usage_affinity([top.entry_id], snapshot)
            usage_aff = aff.get(top.entry_id, 0.0)
        except Exception:
            pass

    boost = _learned_boost(query_norm, top.path)
    confidence = min(1.0, _TIER_PRIOR.get(tier, 0.0) + top.score * 0.5 + boost + usage_aff * 0.1)

    breakdown = {
        "tier_prior": _TIER_PRIOR.get(tier, 0.0), "match_score": round(top.score, 3),
        "learned_boost": round(boost, 3), "usage_affinity": round(usage_aff, 3),
    }

    if confidence >= HIGH_CONFIDENCE:
        decision = "open"
    elif confidence >= MEDIUM_CONFIDENCE:
        decision = "confirm"
    else:
        decision = "choices"

    logger.info(
        "[FILE_RESOLVER_DECISION] tier=%d(%s) decision=%s path=%s confidence=%.3f breakdown=%s",
        tier, _TIER_NAMES.get(tier, "?"), decision, top.path, confidence, breakdown,
    )

    return ResolveResult(
        decision=decision, path=top.path, confidence=confidence,
        tier=tier, tier_name=_TIER_NAMES.get(tier), candidates=candidates[:4], breakdown=breakdown,
        snapshot=snapshot,
    )


def _maybe_clear(query_norm: str, tier: int, candidates: list[Candidate], snapshot: dict) -> Optional[ResolveResult]:
    if not candidates:
        return None
    best = max(c.score for c in candidates)
    if best >= _TIER_CLEAR.get(tier, 1.0):
        logger.debug("[FILE_RESOLVER_TIER_CLEAR] tier=%d score=%.3f", tier, best)
        return _finalize(query_norm, candidates, tier, snapshot)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(query: str, open_type: str = "any", drive: str = "") -> ResolveResult:
    t0 = time.monotonic()
    query_norm = _normalize_query(query)

    try:
        from .fs_index import fs_index
    except Exception:
        return ResolveResult(decision="none")

    snapshot = get_context_snapshot()

    # ── Tier 0 — learned resolution ────────────────────────────────────────
    learned = fs_index.get_learned_resolution(query_norm)
    if learned:
        path_str, hits = learned
        p = Path(path_str)
        if p.exists():
            confidence = min(1.0, 0.6 + 0.1 * hits)
            logger.info("[FILE_RESOLVER_DECISION] tier=0(learned) decision=open path=%s confidence=%.3f hits=%d",
                        p, confidence, hits)
            logger.info("[FILE_RESOLVER_MS] tier=0 ms=%.1f", (time.monotonic() - t0) * 1000)
            return ResolveResult(decision="open", path=p, confidence=confidence, tier=0,
                                  tier_name="learned", breakdown={"hits": hits}, snapshot=snapshot)
        logger.debug("[FILE_RESOLVER] learned path vanished: %s", path_str)

    all_candidates: list[Candidate] = []
    type_sql = _type_filter_sql(open_type)

    # ── Tier 1 — current project/workspace (read from World State — the
    # sensor call already happened once inside get_context_snapshot()) ─────
    try:
        ws = snapshot.get("current_workspace")
        if ws and ws.get("root"):
            rows = fs_index.get_candidates_under_root(ws["root"], type_filter=type_sql, name_hint=query_norm)
            cands = _score_id_path_rows(rows, query_norm, tier=1, open_type=open_type)
            r = _maybe_clear(query_norm, 1, cands, snapshot)
            if r: return _log_ms(r, t0)
            all_candidates += cands
    except Exception:
        logger.debug("[FILE_RESOLVER] workspace tier failed", exc_info=True)

    # ── Tier 2 — current folder (focused Explorer window, from World State) ─
    try:
        explorer_folder = snapshot.get("current_explorer_folder")
        if explorer_folder:
            folder = Path(explorer_folder)
            rows = fs_index.get_candidates_under_root(folder, type_filter=type_sql, name_hint=query_norm)
            cands = _score_id_path_rows(rows, query_norm, tier=2, open_type=open_type)
            r = _maybe_clear(query_norm, 2, cands, snapshot)
            if r: return _log_ms(r, t0)
            all_candidates += cands
    except Exception:
        logger.debug("[FILE_RESOLVER] explorer tier failed", exc_info=True)

    if open_type != "folder":
        # ── Tier 3 — recent files ────────────────────────────────────────────
        cands = _score_id_path_rows(fs_index.get_recent_files(), query_norm, tier=3, open_type=open_type)
        r = _maybe_clear(query_norm, 3, cands, snapshot)
        if r: return _log_ms(r, t0)
        all_candidates += cands

        # ── Tier 4 — frequently opened files ─────────────────────────────────
        cands = _score_id_path_rows(fs_index.get_frequent_files(), query_norm, tier=4, open_type=open_type)
        r = _maybe_clear(query_norm, 4, cands, snapshot)
        if r: return _log_ms(r, t0)
        all_candidates += cands

    # ── Tier 5 — recent conversation context ────────────────────────────────
    cands = _conversation_candidates(query_norm, open_type)
    r = _maybe_clear(query_norm, 5, cands, snapshot)
    if r: return _log_ms(r, t0)
    all_candidates += cands

    # ── Tier 6 — active application context ─────────────────────────────────
    cands = _app_context_candidates(query_norm, snapshot, open_type)
    r = _maybe_clear(query_norm, 6, cands, snapshot)
    if r: return _log_ms(r, t0)
    all_candidates += cands

    # ── Tier 7 — current screen context — Phase 2 not implemented yet ──────
    # Intentional no-op hook: once screen_context_service exposes a structured
    # "current document/product" entity, plug its candidate(s) in here at
    # this exact priority slot (between active-app and semantic).

    if open_type != "folder":
        # ── Tier 8 — semantic index ──────────────────────────────────────────
        sem_hits = fs_index.search_semantic_ranked(query, limit=10, active_folder=snapshot["active_folder"] or None)
        cands = [Candidate(path=p, score=max(0.0, min(1.0, s)), tier=8) for s, p, _ in sem_hits
                 if not _is_junk(str(p)) and _filter_open_type(p, open_type)]
        r = _maybe_clear(query_norm, 8, cands, snapshot)
        if r: return _log_ms(r, t0)
        all_candidates += cands

    # ── Tier 9 — filename/path index (existing OS-wide fuzzy) ───────────────
    fname_hits = fs_index.search_ranked(query, type_filter=type_sql, drive=drive or None, limit=10)
    cands = [Candidate(path=p, score=s, tier=9) for s, p in fname_hits
             if not _is_junk(str(p)) and _filter_open_type(p, open_type)]
    r = _maybe_clear(query_norm, 9, cands, snapshot)
    if r: return _log_ms(r, t0)
    all_candidates += cands

    if not all_candidates:
        logger.info("[FILE_RESOLVER_DECISION] decision=none query=%r", query)
        logger.info("[FILE_RESOLVER_MS] ms=%.1f (no tier produced any candidate)", (time.monotonic() - t0) * 1000)
        return ResolveResult(decision="none")

    return _log_ms(_finalize(query_norm, all_candidates, all_candidates[0].tier, snapshot), t0)


def _log_ms(result: ResolveResult, t0: float) -> ResolveResult:
    logger.info("[FILE_RESOLVER_MS] tier=%s ms=%.1f", result.tier, (time.monotonic() - t0) * 1000)
    return result


def record_confirmed_choice(query_text: str, path: str) -> None:
    """
    Call this whenever the user has *actually confirmed* a candidate —
    accepted a medium-confidence "did you mean X?" prompt, or picked one
    from a disambiguation list. Promotes that (query -> path) pairing so
    the next identical request resolves instantly via tier 0.
    """
    try:
        from .fs_index import fs_index
        fs_index.record_learned_resolution(_normalize_query(query_text), path)
    except Exception:
        logger.debug("[FILE_RESOLVER] failed to record learned resolution", exc_info=True)
