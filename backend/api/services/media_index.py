"""
media_index.py — Fast in-memory media file index for Xyron.

Scans video folders at startup, stores normalized_title → win_path mapping so
media lookups take <1 ms instead of spawning a search process.

Scan roots:
  Windows drives (/mnt/c, /mnt/d, /mnt/e, …) — Videos, Movies, Anime,
  Downloads, and the user home directory.

Rebuild cadence: at startup (15 s delay) + every 6 hours.

Log prefixes:
  [MEDIA_INDEX_BUILD]  — build start / finish stats
  [MEDIA_INDEX_HIT]    — fast-path lookup succeeded
  [MEDIA_INDEX_MISS]   — not in index, caller should fall back to search
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".m4v"})

# Cache file so the index survives restarts
_CACHE_PATH = Path.home() / ".ai-operator" / "media_index.json"
_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Regex to strip release-group noise from filenames
_NOISE_RE = re.compile(
    r'[\.\s_]('
    r'\d{4}|'
    r'\d{3,4}p|'
    r'bluray|blu[\.\-]ray|bdrip|dvdrip|webrip|web[\.\-]dl|hdtv|'
    r'x264|x265|h264|h265|hevc|avc|xvid|'
    r'aac|ac3|dts|mp3|flac|'
    r'hindi|english|japanese|urdu|dual|multi|'
    r'10bit|hdr|sdr|'
    r'yts|yify|vegamovies|rarbg|'
    r'esubs|subs|sub'
    r')',
    re.IGNORECASE,
)


def _normalize_title(stem: str) -> str:
    """'Suzume.2022.720p.BluRay.x265' → 'suzume'."""
    m = _NOISE_RE.search(stem)
    raw = stem[:m.start()] if m else stem
    clean = re.sub(r"[._\-]", " ", raw).strip()
    clean = re.sub(r"\s{2,}", " ", clean)
    return clean.lower()


def _wsl_to_win(wsl: str) -> str:
    """'/mnt/e/Movies/a.mkv' → 'E:\\Movies\\a.mkv'."""
    if wsl.startswith("/mnt/") and len(wsl) > 6:
        drive = wsl[5].upper()
        rest = wsl[6:].replace("/", "\\")
        return f"{drive}:{rest}"
    return wsl


def _scan_root(root: Path, index: Dict[str, str]) -> int:
    """Walk `root` and add video files to `index`. Returns count added."""
    added = 0
    for dirpath, dirnames, filenames in os.walk(str(root)):
        # Skip hidden / system directories
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() not in {
                "$recycle.bin", "system volume information", "windows",
                "program files", "program files (x86)", "programdata",
            }
        ]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            stem = Path(fname).stem
            title = _normalize_title(stem)
            wsl_path = os.path.join(dirpath, fname)
            win_path = _wsl_to_win(wsl_path)
            # Keep the highest-quality (largest) file if duplicate title
            if title not in index or os.path.getsize(wsl_path) > os.path.getsize(
                _wsl_to_win(index[title]).replace("\\", "/").replace("C:", "/mnt/c")
                if index[title][1:2] == ":" else index[title]
            ):
                index[title] = win_path
            added += 1
    return added


def _discover_scan_roots() -> List[Path]:
    """
    Staged discovery of likely media directories.

    Stage 1: Well-known named subdirs on all mounted Windows drives.
    Stage 2: Windows user home (via %USERPROFILE%) Videos/Downloads/Desktop.
    Stage 3: Shallow scan (depth=1) of each drive root for media-like dirs.

    Returns deduplicated list of existing, readable directories.
    """
    import subprocess

    MEDIA_SUBDIRS = [
        "Videos", "Movies", "Anime", "Downloads", "Desktop",
        "Entertainment", "Series", "TV Shows", "Video", "Film", "Films",
        "Media", "Torrents", "My Videos",
    ]
    MEDIA_LIKE_RE = re.compile(
        r'\b(?:video|movie|movies|anime|film|films|series|media|'
        r'download|downloads|entertainment|torrent|torrents)\b',
        re.IGNORECASE,
    )

    seen: set[str] = set()
    roots: list[Path] = []

    def _add(p: Path) -> None:
        s = str(p)
        if s not in seen and p.is_dir():
            seen.add(s)
            roots.append(p)
            logger.info("[MEDIA_INDEX_STAGE] root_added=%s", p)

    # Stage 1: scan all /mnt/<letter> drives for known media subdirs
    mnt = Path("/mnt")
    drive_roots: list[Path] = []
    try:
        for entry in sorted(mnt.iterdir()):
            if entry.is_dir() and len(entry.name) == 1 and entry.name.isalpha():
                try:
                    # Quick accessibility test
                    list(entry.iterdir())
                    drive_roots.append(entry)
                    logger.info("[MEDIA_ROOTS_DISCOVERED] drive=%s", entry)
                except (PermissionError, OSError):
                    pass
    except OSError as exc:
        logger.debug("[MEDIA_ROOTS_DISCOVERED] /mnt scan error: %s", exc)

    for drive in drive_roots:
        for subdir in MEDIA_SUBDIRS:
            candidate = drive / subdir
            if candidate.is_dir():
                _add(candidate)

    # Stage 2: Windows user home via USERPROFILE
    try:
        r = subprocess.run(
            ["/mnt/c/Windows/System32/cmd.exe", "/c", "echo", "%USERPROFILE%"],
            capture_output=True, text=True, timeout=3,
        )
        raw = r.stdout.strip()
        if raw and "%" not in raw and len(raw) > 3:
            # "C:\Users\tayyab" → /mnt/c/Users/tayyab
            win_home = "/mnt/" + raw[0].lower() + "/" + raw[3:].replace("\\", "/")
            home = Path(win_home)
            if home.is_dir():
                logger.info("[MEDIA_ROOTS_DISCOVERED] win_home=%s", home)
                for subdir in MEDIA_SUBDIRS:
                    candidate = home / subdir
                    if candidate.is_dir():
                        _add(candidate)
    except Exception as exc:
        logger.debug("[MEDIA_ROOTS_DISCOVERED] win_home detection failed: %s", exc)

    # Stage 3: shallow scan of each drive root for media-like dir names
    for drive in drive_roots:
        try:
            for entry in drive.iterdir():
                if (
                    entry.is_dir()
                    and MEDIA_LIKE_RE.search(entry.name)
                    and str(entry) not in seen
                ):
                    _add(entry)
        except (PermissionError, OSError):
            pass

    # Fallback: Linux home media subdirs
    home = Path.home()
    for subdir in MEDIA_SUBDIRS:
        candidate = home / subdir
        if candidate.is_dir():
            _add(candidate)

    logger.info("[MEDIA_ROOTS_DISCOVERED] total_roots=%d", len(roots))
    return roots


class MediaIndex:
    """
    Thread-safe in-memory media title → Windows path index.
    Background thread rebuilds every REBUILD_INTERVAL_S seconds.
    """

    REBUILD_INTERVAL_S = 6 * 3600   # 6 hours
    STARTUP_DELAY_S    = 15          # wait for app to be stable before first scan

    def __init__(self) -> None:
        self._lock    = threading.RLock()
        self._index: Dict[str, str] = {}
        self._ready   = False
        self._last_build_t = 0.0

    def start(self) -> None:
        """Launch background build thread. Call once at app startup."""
        threading.Thread(
            target=self._build_loop,
            daemon=True,
            name="media-index-build",
        ).start()
        logger.info("[MEDIA_INDEX_BUILD] background builder scheduled startup_delay=%ds",
                    self.STARTUP_DELAY_S)

    def _build_loop(self) -> None:
        # Serve cached index immediately on startup — no 15s wait for existing entries
        self._load_cache()
        # Wait before the first expensive full scan
        time.sleep(self.STARTUP_DELAY_S)
        while True:
            self._rebuild()
            time.sleep(self.REBUILD_INTERVAL_S)

    def _load_cache(self) -> None:
        """Load persisted index from disk immediately — zero scan delay."""
        try:
            if _CACHE_PATH.exists():
                cached = json.loads(_CACHE_PATH.read_text())
                if isinstance(cached, dict) and cached:
                    with self._lock:
                        self._index = cached
                        self._ready = True
                    logger.info("[MEDIA_INDEX_BUILD] cache_loaded entries=%d", len(cached))
        except Exception as exc:
            logger.debug("[MEDIA_INDEX_BUILD] cache load failed: %s", exc)

    def _rebuild(self) -> None:
        t0 = time.monotonic()
        new_index: Dict[str, str] = {}

        # Carry forward existing (possibly cached) entries
        with self._lock:
            new_index.update(self._index)

        scan_roots = _discover_scan_roots()
        logger.info("[MEDIA_INDEX_BUILD] scanning %d roots", len(scan_roots))
        total = 0
        for root in scan_roots:
            try:
                cnt = _scan_root(root, new_index)
                total += cnt
                logger.debug("[MEDIA_INDEX_BUILD] root=%s found=%d", root, cnt)
            except Exception as exc:
                logger.debug("[MEDIA_INDEX_BUILD] scan error root=%s: %s", root, exc)

        elapsed = (time.monotonic() - t0) * 1000
        with self._lock:
            self._index = new_index
            self._ready = True
            self._last_build_t = time.time()

        logger.info("[MEDIA_INDEX_DONE] entries=%d scanned=%d elapsed_ms=%.0f",
                    len(new_index), total, elapsed)

        try:
            _CACHE_PATH.write_text(json.dumps(new_index))
        except Exception as exc:
            logger.debug("[MEDIA_INDEX_BUILD] cache write failed: %s", exc)

    def lookup(self, title: str) -> Optional[str]:
        """
        Look up a normalized title.  Returns Windows path or None.

        Tries:
          1. Exact match on normalized title.
          2. Any index key that starts with the query (prefix match).
          3. Any index key that contains the query (substring match).
        """
        if not self._ready:
            return None

        q = title.strip().lower()
        with self._lock:
            # Exact
            if q in self._index:
                logger.info("[MEDIA_INDEX_HIT] query=%r key=%r", q, q)
                return self._index[q]
            # Prefix
            for key, path in self._index.items():
                if key.startswith(q):
                    logger.info("[MEDIA_INDEX_HIT] query=%r key=%r (prefix)", q, key)
                    return path
            # Substring
            for key, path in self._index.items():
                if q in key:
                    logger.info("[MEDIA_INDEX_HIT] query=%r key=%r (substr)", q, key)
                    return path

        logger.info("[MEDIA_INDEX_MISS] query=%r index_size=%d", q, len(self._index))
        return None

    def size(self) -> int:
        with self._lock:
            return len(self._index)

    def is_ready(self) -> bool:
        return self._ready


# Singleton started by main.py (or lazily on first use)
media_index = MediaIndex()
