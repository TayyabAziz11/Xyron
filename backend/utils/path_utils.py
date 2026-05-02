"""WSL2-aware path resolution.

All public functions operate in WSL2 space — they produce /mnt/... paths.
Never produce or consume Windows paths internally; use wsl_to_win() only for
display or PowerShell calls.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional


# ── Windows username discovery ─────────────────────────────────────────────

@lru_cache(maxsize=1)
def _win_username() -> str:
    """Find the real Windows username by scanning /mnt/c/Users/*/Desktop."""
    skip = {"public", "default", "default user", "all users"}
    try:
        for desktop in sorted(Path("/mnt/c/Users").glob("*/Desktop")):
            if desktop.parent.name.lower() not in skip:
                return desktop.parent.name
    except Exception:
        pass
    return "User"


def _win_home() -> str:
    return f"/mnt/c/Users/{_win_username()}"


# ── Named special directories ──────────────────────────────────────────────

def _specials() -> dict[str, str]:
    home = _win_home()
    return {
        "desktop":   f"{home}/Desktop",
        "documents": f"{home}/Documents",
        "document":  f"{home}/Documents",
        "downloads": f"{home}/Downloads",
        "download":  f"{home}/Downloads",
        "pictures":  f"{home}/Pictures",
        "picture":   f"{home}/Pictures",
        "music":     f"{home}/Music",
        "videos":    f"{home}/Videos",
        "video":     f"{home}/Videos",
        "home":      home,
        "user":      home,
    }


# ── Core resolver ──────────────────────────────────────────────────────────

def resolve_wsl_path(raw: str) -> Optional[str]:
    """Convert any path expression to an absolute WSL2 path.

    Returns None when raw is empty or unrecognisable — caller should ask user.

    Handles:
      "D drive" / "d drive" / "my d drive" / "myd drive"  →  /mnt/d
      "D:" / "D:\\"                                         →  /mnt/d
      "/mnt/d" / "/mnt/d/Games"                             →  /mnt/d / /mnt/d/Games
      "C:\\Users\\muham\\Desktop"                           →  /mnt/c/Users/muham/Desktop
      "desktop" / "downloads" / "documents"                 →  /mnt/c/Users/{user}/...
      Any A–Z drive letter                                   →  /mnt/{letter}
      ""  / None                                             →  None
    """
    if not raw or not raw.strip():
        return None

    p = raw.strip()

    # Normalize backslashes early so "d\\workspace" → "d/workspace" can be matched below.
    # Double-backslash first to avoid turning "\\\\" into "//".
    p = p.replace("\\\\", "/").replace("\\", "/")

    # Already a WSL path: /mnt/d or /mnt/d/...
    m = re.match(r'^/mnt/([a-z])(/.*)?$', p, re.I)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2) or ""
        return f"/mnt/{drive}{rest}"

    # Windows absolute path: C:\... or D:/... or C:/Users/... (backslashes already normalized)
    m = re.match(r'^([a-z]):[/](.*)', p, re.I)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).strip("/")
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"

    # Drive letter + subpath after backslash normalization: "d/workspace" → /mnt/d/workspace
    # Only matches single-letter drive (not "desktop/games" since that's 7 chars before /).
    m = re.match(r'^([a-z])/(.+)$', p, re.I)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).strip("/")
        return f"/mnt/{drive}/{rest}"

    # "my d drive" / "myd drive" (Whisper merges "my"+"d")
    m = re.match(r'^my\s*([a-z])\s+(?:drive|disk)\s*$', p, re.I)
    if m:
        return f"/mnt/{m.group(1).lower()}"

    # Single drive letter with optional "drive" / "disk" / ":"
    # Matches: "D drive", "d disk", "D:", "d"
    m = re.match(r'^([a-z])\s*(?:drive|disk|:)?\s*$', p, re.I)
    if m:
        return f"/mnt/{m.group(1).lower()}"

    # Named special directories (exact match)
    specials = _specials()
    key = p.lower().strip("/\\")
    if key in specials:
        return specials[key]

    # Special dir + sub-path: "desktop/Games" or "desktop\\Games"
    for sep in ("/", "\\"):
        if sep in p:
            head, tail = p.split(sep, 1)
            head_key = head.lower().strip()
            if head_key in specials:
                return specials[head_key].rstrip("/") + "/" + tail.replace("\\", "/")
            break

    return None


# ── Text-based extraction ──────────────────────────────────────────────────

# Patterns ordered most-specific → least-specific
_LOC_PATTERNS = [
    # "in my d drive" / "on myd drive"
    re.compile(r'\b(?:in|on|at|inside|under)\s+(?:the\s+)?my\s*([a-z])\s+(?:drive|disk)\b', re.I),
    # "in d drive" / "on f disk"
    re.compile(r'\b(?:in|on|at|inside|under)\s+(?:the\s+)?([a-z])\s+(?:drive|disk)\b', re.I),
    # "in d:" (trailing colon)
    re.compile(r'\b(?:in|on|at|inside|under)\s+(?:the\s+)?([a-z]):\s*\b', re.I),
    # "on the desktop" / "in documents" / "in downloads"
    re.compile(
        r'\b(?:in|on|at|inside|under)\s+(?:the\s+)?(desktop|documents?|downloads?|pictures?|music|videos?)\b',
        re.I,
    ),
]


def resolve_wsl_path_from_text(text: str) -> Optional[str]:
    """Extract and resolve a location from a natural-language sentence.

    "create folder Games in D drive"    →  /mnt/d
    "make a folder on the desktop"      →  /mnt/c/Users/{user}/Desktop
    "create subfolder in my D drive"    →  /mnt/d
    "create folder X"   (no location)   →  None
    """
    for pat in _LOC_PATTERNS:
        m = pat.search(text)
        if m:
            return resolve_wsl_path(m.group(1).strip())
    return None


# ── Backward-compat helpers ────────────────────────────────────────────────

def win_to_wsl(path: str) -> str:
    """Convert Windows path → WSL2 path (kept for backward compat)."""
    result = resolve_wsl_path(path)
    return result if result else path


def wsl_to_win(path: str) -> str:
    """Convert a WSL2 path to a Windows display path (for Explorer / PowerShell)."""
    if not path:
        return path
    m = re.match(r'^/mnt/([a-z])(/.+)$', path, re.I)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2).replace("/", "\\")
        return f"{drive}:{rest}"
    m2 = re.match(r'^/mnt/([a-z])/?$', path, re.I)
    if m2:
        return f"{m2.group(1).upper()}:\\"
    return path


def safe_path(path: str) -> str:
    """Always return a WSL2 path regardless of input (backward compat)."""
    result = resolve_wsl_path(path)
    return result if result else path
