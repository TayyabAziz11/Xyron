"""
send_security.py — outbound-file security policy for WhatsApp sends.

A file leaving the machine via WhatsApp is a data-exfiltration surface, so
this module is deliberately stricter than tools/safety.py (which governs
local open/read/write). It blocks, BEFORE any network call:

  • anything under a `.secrets` directory (repo credentials live there)
  • credential / auth / session / token / environment files by name pattern
  • private-key material by extension (pem, key, ppk, p12, pfx, kdbx, …)
  • hidden application state (.git, __pycache__, node_modules, .cache, …)
  • anything under AppData / ProgramData (application state, not user docs)
  • files over the send size limit, directories, missing/unreadable files

The Node sidecar independently blocks `.secrets` at /sendFile and /sendImage
(see server.js) — this is the Python-side defense-in-depth layer. Both must
agree; if they drift, the sidecar remains the last line of defense.

This module never raises — every check returns a structured verdict.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Must match the sidecar's MAX_FILE_SIZE (server.js).
MAX_SEND_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB

# Directory segments that indicate credentials or hidden application state.
# Matched against every path segment, case-insensitively.
_BLOCKED_SEGMENTS = {
    ".secrets",          # Xyron credential vault
    ".git", ".hg", ".svn",
    "__pycache__", ".cache", ".pytest_cache",
    "node_modules", ".venv", "venv",
    ".vscode", ".idea",
    "appdata",           # Windows application state (via segment match below)
    "programdata",
}

# Filename substrings that look like credentials / secrets / session state.
# Substring (not word-boundary) matching on purpose: over-blocking a benign
# file named "author-photo.jpg" is an acceptable cost; leaking a credential
# is not. The caller can always rename a genuinely innocent file.
_BLOCKED_NAME_SUBSTRINGS = (
    "credential", "password", "passwd", "secret", "token",
    "session", "cookie", "auth", "apikey", "api_key",
)

# Extensions that are private-key / keystore material.
_BLOCKED_EXTENSIONS = {
    ".pem", ".key", ".ppk", ".p12", ".pfx", ".kdbx", ".keystore", ".crt",
    ".env",
}

# OpenSSH/age private-key filename prefixes.
_BLOCKED_KEY_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "age.key")


@dataclass
class SendPathVerdict:
    ok: bool
    reason: Optional[str] = None           # machine-readable block reason
    detail: Optional[str] = None           # human-readable explanation
    size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    media_kind: Optional[str] = None       # "image" | "document" (send routing)


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic", ".avif"}


def _looks_like_env_file(name: str) -> bool:
    n = name.lower()
    return n == ".env" or n.startswith(".env.") or n.endswith(".env")


def detect_media_kind(path: Path) -> Optional[str]:
    """
    Classify a file for send routing using MIME type (mimetypes), falling
    back to extension. Returns "image" (→ send_image) or "document"
    (→ send_file). Never raises.
    """
    try:
        import mimetypes
        mime, _ = mimetypes.guess_type(str(path))
    except Exception:
        mime = None
    if mime and mime.startswith("image/"):
        return "image"
    if mime:
        return "document"
    # MIME unknown — fall back to extension
    if path.suffix.lower() in _IMAGE_EXTENSIONS:
        return "image"
    return "document"


def _mime_of(path: Path) -> Optional[str]:
    try:
        import mimetypes
        mime, _ = mimetypes.guess_type(str(path))
        return mime
    except Exception:
        return None


def validate_sendable_path(path_str: str) -> SendPathVerdict:
    """
    Full outbound-send validation. Returns ok=False with a reason for
    anything that must never be sent. Never raises.
    """
    try:
        if not path_str or not path_str.strip():
            return SendPathVerdict(False, reason="invalid", detail="empty path")

        path = Path(path_str.strip().strip('"').strip("'"))

        if not path.exists():
            return SendPathVerdict(False, reason="file_not_found",
                                   detail=f"file does not exist: {path.name}")

        if not path.is_file():
            return SendPathVerdict(False, reason="not_a_file",
                                   detail="path is a directory, not a file")

        # Segment-based blocks (state dirs, .secrets, AppData)
        segments = [s.lower() for s in path.parts]
        for seg in segments:
            if seg in _BLOCKED_SEGMENTS:
                return SendPathVerdict(
                    False, reason="blocked_directory",
                    detail=f"path is inside a blocked directory ({seg}) — "
                           "secrets and application state cannot be sent",
                )

        name_l = path.name.lower()
        if _looks_like_env_file(name_l):
            return SendPathVerdict(False, reason="credential_like_filename",
                                   detail="environment file — refusing to send")

        for sub in _BLOCKED_NAME_SUBSTRINGS:
            if sub in name_l:
                return SendPathVerdict(
                    False, reason="credential_like_filename",
                    detail=f"filename contains '{sub}' — looks like a credential/auth/session file; "
                           "rename it if this is genuinely safe to share",
                )

        ext = path.suffix.lower()
        if ext in _BLOCKED_EXTENSIONS:
            return SendPathVerdict(False, reason="key_material",
                                   detail=f"'{ext}' files are private-key/credential material")

        for prefix in _BLOCKED_KEY_PREFIXES:
            if name_l.startswith(prefix):
                return SendPathVerdict(False, reason="key_material",
                                       detail=f"'{path.name}' looks like a private key file")

        # Readability
        try:
            if not os.access(path, os.R_OK):
                return SendPathVerdict(False, reason="unreadable",
                                       detail="file is not readable by this process")
        except OSError as e:
            return SendPathVerdict(False, reason="unreadable", detail=str(e))

        # Size
        try:
            size = path.stat().st_size
        except OSError as e:
            return SendPathVerdict(False, reason="stat_failed", detail=str(e))
        if size <= 0:
            return SendPathVerdict(False, reason="empty_file",
                                   detail="file is empty (0 bytes)")
        if size > MAX_SEND_SIZE_BYTES:
            return SendPathVerdict(
                False, reason="file_too_large",
                detail=f"file is {size / 1024 / 1024:.1f} MB — limit is "
                       f"{MAX_SEND_SIZE_BYTES // 1024 // 1024} MB",
            )

        mime = _mime_of(path)
        kind = detect_media_kind(path)
        return SendPathVerdict(True, size_bytes=size, mime_type=mime, media_kind=kind)

    except Exception as e:
        # Never raise out of a security check — fail closed.
        return SendPathVerdict(False, reason="validation_error", detail=str(e))
