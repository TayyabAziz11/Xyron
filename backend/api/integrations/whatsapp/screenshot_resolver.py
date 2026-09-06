"""
screenshot_resolver.py — resolve a conversational screenshot reference
("the screenshot I just took") to an exact local file on the user's machine.

Reuses the existing Xyron filesystem primitives:

  * ``file_send._known_folders()`` — canonical Windows screenshot locations
    (Pictures\\Screenshots, OneDrive mirrors, Videos\\Captures) plus
    Desktop / Downloads / Pictures.
  * ``file_send._IMAGE_EXTS`` / ``_SCREENSHOT_HINTS`` — extension and name
    pattern dictionaries already in production.

The resolver classifies each candidate image by *where* it lives (a
dedicated screenshot directory is a strong signal) and *what* it is named
(Windows Snipping Tool / Snip & Sketch / PrintScreen conventions), then
scores recency. Selection rules prefer strong screenshot candidates over
generic recent images — a 2-minute-old Snipping Tool capture always beats
a 10-minute-old arbitrary desktop wallpaper save.

Ambiguity handling:
  * If two (or more) strong candidates are within ``ambiguity_window_s``
    of each other (default 120 s) in creation time, the resolver refuses
    to guess and returns all top candidates — the caller must present them
    for user choice.
  * If only weak (non-screenshot-like) recent images exist, the resolver
    reports ``needs_clarification`` with those candidates rather than
    selecting a generic image as "the screenshot".

This module is hermetic: all directories, the system clock, and thresholds
are injectable via the constructor for deterministic tests.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("wa_screenshot_resolver")

# Reuse the canonical file-send primitives (safe: no side effects on import).
from .file_send import (
    _IMAGE_EXTS,
    _SCREENSHOT_HINTS,
    _known_folders,
    _mime_of,
)

# Thresholds (all in seconds unless noted).
_DEFAULT_AMBIGUITY_WINDOW_S = 120.0     # candidates within this window of the newest → ambiguous
_DEFAULT_RECENT_WINDOW_S = 24.0 * 3600.0  # "just took" — candidates older than this aren't selectable
_MAX_CANDIDATES_REPORT = 5


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ScreenshotCandidate:
    path: str
    filename: str
    mime_type: Optional[str]
    size_bytes: int
    created_ts: float              # st_ctime (creation time on Windows)
    modified_ts: float             # st_mtime
    taken_ts: float                # max(ctime, mtime) — used for recency / ordering
    directory: str                 # containing directory (as passed)
    in_screenshot_dir: bool
    name_is_screenshot_like: bool
    classification: str            # "screenshot_dir+name" | "screenshot_dir" | "name_pattern" | "generic_image"
    class_score: float
    recency_score: float
    confidence: float
    reason: str


@dataclass
class ScreenshotResolution:
    status: str                    # selected | ambiguous | needs_clarification | not_found | error
    selected: Optional[ScreenshotCandidate] = None
    candidates: List[ScreenshotCandidate] = field(default_factory=list)
    detail: Optional[str] = None
    reference: str = "the screenshot I just took"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_dirs() -> tuple:
    """Return (primary_dirs, secondary_dirs) — both lists of existing Paths."""
    folders = _known_folders()
    primary = [d for d in folders.get("screenshots", []) if d.exists() and d.is_dir()]
    secondary = []
    for key in ("desktop", "downloads", "pictures"):
        for d in folders.get(key, []):
            if d.exists() and d.is_dir() and d not in primary and d not in secondary:
                secondary.append(d)
    return primary, secondary


def _is_screenshot_name(name_lower: str) -> bool:
    return any(hint in name_lower for hint in _SCREENSHOT_HINTS)


def _classify(in_shot_dir: bool, name_like: bool) -> tuple:
    if in_shot_dir and name_like:
        return "screenshot_dir+name", 1.0
    if in_shot_dir:
        return "screenshot_dir", 0.85
    if name_like:
        return "name_pattern", 0.70
    return "generic_image", 0.30


def _recency_score(age_s: float) -> float:
    if age_s <= 0.0:
        return 1.0
    h = age_s / 3600.0
    if h <= 0.25:   return 1.00   # ≤ 15 min — "just took"
    if h <= 1.0:    return 0.85   # ≤ 1 h
    if h <= 6.0:    return 0.60   # ≤ 6 h
    if h <= 24.0:   return 0.35   # ≤ 24 h
    if h <= 72.0:   return 0.15   # ≤ 72 h
    return 0.05                   # older (still reported, but very low)


def _confidence_score(class_score: float, recency_score: float) -> float:
    return round(0.65 * class_score + 0.35 * recency_score, 3)


def _human_age(age_s: float) -> str:
    if age_s < 60:
        return f"{int(age_s)} s ago"
    if age_s < 3600:
        return f"{int(age_s / 60)} min ago"
    if age_s < 86400:
        return f"{age_s / 3600:.1f} h ago"
    return f"{age_s / 86400:.1f} days ago"


def _build_reason(c: "ScreenshotCandidate", now: float) -> str:
    age = _human_age(now - c.taken_ts)
    where = "dedicated Screenshots folder" if c.in_screenshot_dir else (
        "non-standard location (filename matches screenshot pattern)" if c.name_is_screenshot_like
        else "arbitrary recent image"
    )
    name_bit = (
        "filename matches Windows screenshot convention" if c.name_is_screenshot_like
        else "generic filename"
    )
    mime_bit = f"{c.mime_type} image" if c.mime_type and c.mime_type.startswith("image/") else (
        f"{c.mime_type or 'unknown MIME'}"
    )
    return (
        f"newest candidate in {where} (taken {age}); "
        f"{name_bit}; {mime_bit}, {c.size_bytes:,} bytes"
    )


# ---------------------------------------------------------------------------
# ScreenshotResolver
# ---------------------------------------------------------------------------

class ScreenshotResolver:
    """
    Resolve a conversational screenshot reference to an exact local file.

    Parameters
    ----------
    dirs :
        Explicit list of directories to scan. When None, defaults to the
        canonical Windows screenshot folders plus Desktop / Downloads / Pictures.
    primary_dirs :
        Dirs that should be considered "dedicated screenshot folders" (candidates
        inside them are classified as screenshot_dir regardless of filename).
        When None, derived from file_send._known_folders()["screenshots"].
    now :
        Injected system time (epoch seconds). When None, time.time() is used.
    ambiguity_window_s :
        Two strong candidates whose taken_ts are within this many seconds of
        each other are reported as ambiguous instead of auto-picking the newest.
    recent_window_s :
        Candidates older than this are not considered "just taken" — they are
        listed under needs_clarification rather than auto-selected.
    max_candidates_report :
        Maximum candidates returned in the report (strong list is truncated
        but the selected field is always populated when status is 'selected').
    """

    def __init__(
        self,
        dirs: Optional[List[Path]] = None,
        primary_dirs: Optional[List[Path]] = None,
        now: Optional[float] = None,
        ambiguity_window_s: float = _DEFAULT_AMBIGUITY_WINDOW_S,
        recent_window_s: float = _DEFAULT_RECENT_WINDOW_S,
        max_candidates_report: int = _MAX_CANDIDATES_REPORT,
    ):
        if dirs is None or primary_dirs is None:
            _primary, _secondary = _default_dirs()
            if dirs is None:
                dirs = _primary + _secondary
            if primary_dirs is None:
                primary_dirs = _primary
        self._dirs = [Path(d) for d in dirs]
        self._primary_set = {str(Path(d).resolve()) for d in primary_dirs}
        self._now = now
        self._ambiguity_window_s = ambiguity_window_s
        self._recent_window_s = recent_window_s
        self._max_candidates_report = max_candidates_report

    # ── public API ────────────────────────────────────────────────────────

    def resolve(self, reference: str = "the screenshot I just took") -> ScreenshotResolution:
        now = self._now if self._now is not None else time.time()
        try:
            candidates = self._collect(now)
        except Exception as e:
            logger.exception("[SCREENSHOT_RESOLVE] collection failed: %s", e)
            return ScreenshotResolution(
                status="error",
                detail=f"screenshot collection failed: {e}",
                reference=reference,
            )

        if not candidates:
            return ScreenshotResolution(
                status="not_found",
                detail="no image files found in any screenshot folder",
                reference=reference,
            )

        strong = [c for c in candidates if c.class_score >= 0.70]
        strong.sort(key=lambda c: -c.taken_ts)
        weak = [c for c in candidates if c.class_score < 0.70]
        weak.sort(key=lambda c: -c.taken_ts)

        if not strong:
            # Only weak/generic recent images — not a screenshot.
            return ScreenshotResolution(
                status="needs_clarification",
                candidates=weak[: self._max_candidates_report],
                detail=(
                    f"no screenshot-like files found (no file in a dedicated "
                    f"Screenshots folder and no screenshot-convention filename); "
                    f"{len(weak)} generic recent image(s) available for manual pick"
                ),
                reference=reference,
            )

        newest = strong[0]
        age = now - newest.taken_ts

        # Staleness check: if the newest strong candidate is too old, it
        # cannot plausibly be "the screenshot I just took".
        if age > self._recent_window_s:
            return ScreenshotResolution(
                status="needs_clarification",
                candidates=strong[: self._max_candidates_report],
                detail=(
                    f"screenshots exist but the newest is {_human_age(age)} old — "
                    f"'just took' requires a recent capture "
                    f"(max window = {self._recent_window_s / 3600:.0f} h)"
                ),
                reference=reference,
            )

        # Ambiguity: other strong candidates within the ambiguity window
        # of the newest — cannot rank by recency alone.
        close = [
            c for c in strong[1:]
            if (newest.taken_ts - c.taken_ts) <= self._ambiguity_window_s
        ]
        if close:
            group = [newest] + close
            for c in group:
                c.reason = _build_reason(c, now)
            return ScreenshotResolution(
                status="ambiguous",
                candidates=group[: self._max_candidates_report],
                detail=(
                    f"{len(group)} screenshots within "
                    f"{int(self._ambiguity_window_s)} s of each other — "
                    f"cannot determine which is 'the one you just took'"
                ),
                reference=reference,
            )

        # Exactly one strong recent candidate — select.
        newest.reason = _build_reason(newest, now)
        return ScreenshotResolution(
            status="selected",
            selected=newest,
            candidates=strong[: self._max_candidates_report],
            detail=(
                f"exactly one strong recent screenshot: {newest.filename} "
                f"(confidence {newest.confidence})"
            ),
            reference=reference,
        )

    # ── internals ─────────────────────────────────────────────────────────

    def _collect(self, now: float) -> List[ScreenshotCandidate]:
        out: List[ScreenshotCandidate] = []
        seen: set = set()
        for d in self._dirs:
            try:
                with os.scandir(d) as entries:
                    for entry in entries:
                        try:
                            if not entry.is_file():
                                continue
                            p = Path(entry.path)
                            ext = p.suffix.lower()
                            if ext not in _IMAGE_EXTS:
                                continue
                            resolved = str(p.resolve())
                            if resolved in seen:
                                continue
                            seen.add(resolved)

                            name_lower = p.name.lower()
                            in_shot_dir = resolved.rpartition(os.sep)[0] in self._primary_set or \
                                str(p.parent.resolve()) in self._primary_set
                            name_like = _is_screenshot_name(name_lower)
                            classification, class_score = _classify(in_shot_dir, name_like)

                            stat = entry.stat()
                            ctime = stat.st_ctime
                            mtime = stat.st_mtime
                            taken = max(ctime, mtime)
                            age = max(0.0, now - taken)
                            recency = _recency_score(age)
                            conf = _confidence_score(class_score, recency)

                            out.append(ScreenshotCandidate(
                                path=resolved,
                                filename=p.name,
                                mime_type=_mime_of(p),
                                size_bytes=stat.st_size,
                                created_ts=ctime,
                                modified_ts=mtime,
                                taken_ts=taken,
                                directory=str(d),
                                in_screenshot_dir=in_shot_dir,
                                name_is_screenshot_like=name_like,
                                classification=classification,
                                class_score=class_score,
                                recency_score=recency,
                                confidence=conf,
                                reason="",
                            ))
                        except OSError:
                            continue
            except OSError as e:
                logger.debug("[SCREENSHOT_RESOLVE] scandir failed for %s: %s", d, e)
        out.sort(key=lambda c: -c.taken_ts)
        return out
