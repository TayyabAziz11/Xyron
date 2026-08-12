"""
STT media title corrector — repairs Whisper phonetic mishearings of media titles.

Pipeline:
  1. Detect if transcript is a media play command.
  2. Extract the garbled title portion.
  3. Fuzzy-match against local video filenames from fs_index (pure sync SQLite, < 5 ms).
  4. If a confident unique match is found (score ≥ 0.65), rewrite the transcript.

Designed for cases like:
  "play zoumi movie"    → "play suzume movie"   (Suzume.mkv on disk)
  "play zoom in movie"  → "play suzume movie"
  "play zuma movie"     → "play suzume movie"
  "place zoom in movie" → "play suzume movie"   (after normalizer: place→play)

Matching uses a consonant-skeleton algorithm that is robust to vowel substitutions,
which is exactly the error pattern Whisper makes with proper nouns.
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

_VIDEO_EXTS = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".m4v"})

# Detect a media play command
_PLAY_CMD_RE = re.compile(
    r'\b(?:play|open|watch|find\s+and\s+play)\b',
    re.IGNORECASE,
)

# Detect a media type qualifier word
_MEDIA_QUAL_RE = re.compile(
    r'\b(?:movie|film|video|clip)\b'
    r'|\.(?:mp4|mkv|mov|avi|webm|wmv|m4v)\b',
    re.IGNORECASE,
)

# Ordered from most-specific to least-specific to extract the title portion
_TITLE_EXTRACT_PATTERNS = [
    re.compile(r'\b(?:play|open|watch)\s+(?:a\s+|the\s+)?(.+?)\s+(?:movie|film|video|clip)\s*[.,!?]*\s*$', re.I),
    re.compile(r'\b(?:play|open|watch)\s+(?:the\s+|a\s+)?(?:movie|film)\s+(.+?)\s*[.,!?]*\s*$', re.I),
    re.compile(r'\bfind\s+and\s+play\s+(.+?)\s*(?:movie|film|video)?\s*[.,!?]*\s*$', re.I),
    re.compile(r'\b(?:play|open|watch)\s+(.+?)\s*[.,!?]*\s*$', re.I),
]

_TITLE_STOP_RE = re.compile(
    r'[\.\s_]('
    r'\d{4}|\d{3,4}p|bluray|blu.ray|bdrip|dvdrip|webrip|web.dl|hdtv|'
    r'x264|x265|h264|h265|hevc|avc|xvid|aac|ac3|dts|mp3|flac|'
    r'hindi|english|japanese|urdu|dual|multi|10bit|hdr|sdr|'
    r'yts|yify|vegamovies|rarbg|esubs|subs|sub'
    r')',
    re.IGNORECASE,
)

_VOWELS = frozenset('aeiou')


def _consonant_skeleton(s: str) -> str:
    """Return consonant-only skeleton of a string — vowels stripped, spaces removed."""
    return ''.join(ch for ch in s.lower() if ch.isalpha() and ch not in _VOWELS)


def _stem_to_clean_title(stem: str) -> str:
    """Extract a human-readable lowercase title from a messy release filename stem."""
    m = _TITLE_STOP_RE.search(stem)
    raw = stem[:m.start()] if m else stem
    clean = re.sub(r'[._\-]', ' ', raw).strip()
    return re.sub(r'\s{2,}', ' ', clean).lower()


def _similarity(a: str, b: str) -> float:
    """
    Composite similarity between two short strings.
    Uses full-string ratio + consonant-skeleton ratio; takes the max.
    Consonant matching handles vowel-substitution STT errors (e.g. zoumi ≈ suzume).
    """
    a_low, b_low = a.lower(), b.lower()

    # Full character ratio
    full = SequenceMatcher(None, a_low, b_low).ratio()

    # Consonant-skeleton ratio
    a_csk = _consonant_skeleton(a_low)
    b_csk = _consonant_skeleton(b_low)
    if a_csk and b_csk:
        csk = SequenceMatcher(None, a_csk, b_csk).ratio()
    else:
        csk = 0.0

    # Rapidfuzz partial_ratio if available (catches partial matches)
    partial = 0.0
    try:
        from rapidfuzz import fuzz as _rf
        partial = _rf.partial_ratio(a_low, b_low) / 100.0
    except ImportError:
        pass

    return max(full, csk, partial)


def _extract_garbled_title(transcript: str) -> str | None:
    """
    Extract the title portion from a play command.
    Returns None if the transcript doesn't look like a media play command.
    """
    t = transcript.strip()
    for pat in _TITLE_EXTRACT_PATTERNS:
        m = pat.search(t)
        if m:
            raw = m.group(1).strip().rstrip('.,!?')
            # Filter out trivial fragments
            words = raw.split()
            filtered = [w for w in words if w.lower() not in {
                'a', 'an', 'the', 'that', 'it', 'this', 'some', 'my',
            }]
            if len(filtered) >= 1 and len(raw) >= 2:
                return ' '.join(filtered)
    return None


def _get_video_candidates() -> list[tuple[str, str]]:
    """
    Return (clean_title_lower, original_filename) for every video file in fs_index.
    Pure sync SQLite read — < 5 ms on a warm index.
    Returns [] if index not ready or unavailable.
    """
    try:
        from api.services.fs_index import fs_index, _get_thread_conn  # type: ignore[attr-defined]
        if not fs_index.is_ready:
            return []
        conn = _get_thread_conn(fs_index._db_path)
        rows = conn.execute(
            "SELECT name FROM entries WHERE type = 'file' LIMIT 100000"
        ).fetchall()
        out: list[tuple[str, str]] = []
        for (name,) in rows:
            if PurePosixPath(name.lower()).suffix in _VIDEO_EXTS:
                stem = PurePosixPath(name).stem
                clean = _stem_to_clean_title(stem)
                if clean:
                    out.append((clean, name))
        return out
    except Exception as exc:
        logger.debug("[MEDIA_TITLE_CORRECT] fs_index unavailable: %s", exc)
        return []


def correct_media_title(transcript: str) -> str:
    """
    If transcript is a media play command with a garbled title, correct it.

    Returns the corrected transcript, or the original unchanged if:
      - transcript is not a media play command
      - no video files are indexed
      - no candidate scores ≥ 0.65
      - the garbled title already exactly matches a candidate (no correction needed)
    """
    t = transcript.strip()

    # Fast exit: not a play command
    if not _PLAY_CMD_RE.search(t):
        return transcript

    # Only activate when there is a media qualifier OR the play verb is present
    # (avoids running on generic "open file.txt" commands)
    has_qualifier = bool(_MEDIA_QUAL_RE.search(t))
    is_bare_play  = bool(re.search(r'\b(?:play|watch)\b', t, re.I))
    if not (has_qualifier or is_bare_play):
        return transcript

    garbled = _extract_garbled_title(t)
    if not garbled or len(garbled) < 2:
        return transcript

    garbled_lower = garbled.lower()

    candidates = _get_video_candidates()
    if not candidates:
        return transcript

    # Score every candidate
    scored: list[tuple[float, str, str]] = []  # (score, clean_title, filename)
    for clean_title, filename in candidates:
        # Skip if the garbled title is already a perfect match (no correction needed)
        if garbled_lower == clean_title:
            return transcript
        if garbled_lower in clean_title or clean_title in garbled_lower:
            # Partial substring match — probably already correct enough
            return transcript
        score = _similarity(garbled_lower, clean_title)
        if score >= 0.60:
            scored.append((score, clean_title, filename))

    if not scored:
        return transcript

    # Sort descending by score
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_title, best_filename = scored[0]

    # Only correct if there is ONE clear winner (gap ≥ 0.10 from second place)
    confident = (
        best_score >= 0.65
        and (len(scored) == 1 or (scored[0][0] - scored[1][0]) >= 0.10)
    )
    if not confident:
        logger.debug(
            "[MEDIA_TITLE_CORRECT] ambiguous: garbled=%r best=%r score=%.2f candidates=%d",
            garbled, best_title, best_score, len(scored),
        )
        return transcript

    # Rebuild the transcript: replace the garbled title with the corrected one
    corrected = transcript
    for pat in _TITLE_EXTRACT_PATTERNS:
        m = pat.search(transcript.lower())
        if m:
            start, end = m.span(1)
            corrected = transcript[:start] + best_title + transcript[end:]
            break

    logger.info(
        "[MEDIA_TITLE_CORRECT] raw=%r garbled=%r corrected=%r match=%r score=%.2f",
        transcript, garbled, corrected, best_filename, best_score,
    )
    return corrected
