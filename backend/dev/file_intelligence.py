"""
Active File Intelligence — reads, analyzes, and caches context for the active file.

[ACTIVE_FILE] log prefix. Cache keyed by content hash. Max 300 KB read.
Never blocks the event loop — call via asyncio.to_thread from async code.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_BYTES = 300_000
_CACHE: dict[str, dict] = {}

# ── Language detection ────────────────────────────────────────────────────────

_EXT_LANG: dict[str, str] = {
    "py": "Python", "pyi": "Python",
    "ts": "TypeScript", "tsx": "TypeScript",
    "js": "JavaScript", "jsx": "JavaScript",
    "rs": "Rust", "go": "Go",
    "java": "Java", "kt": "Kotlin",
    "cpp": "C++", "cc": "C++", "cxx": "C++", "c": "C", "h": "C",
    "cs": "C#", "rb": "Ruby", "php": "PHP",
    "swift": "Swift", "dart": "Dart",
    "html": "HTML", "css": "CSS", "scss": "SCSS", "sass": "SASS",
    "json": "JSON", "yaml": "YAML", "yml": "YAML", "toml": "TOML",
    "sh": "Shell", "bash": "Shell", "zsh": "Shell",
    "sql": "SQL", "md": "Markdown",
}

_FRAMEWORK_SIGNATURES: list[tuple[list[str], str]] = [
    (["from fastapi", "APIRouter", "FastAPI("],      "FastAPI"),
    (["from flask", "Flask("],                       "Flask"),
    (["from django", "django."],                     "Django"),
    (["express()", "require('express')", "from 'express'"], "Express"),
    (["import React", "from 'react'", 'from "react"'], "React"),
    (["from 'next/", 'from "next/', "getServerSideProps", "getStaticProps"], "Next.js"),
    (["from 'vue'", "defineComponent("],            "Vue"),
    (["from '@angular/"],                           "Angular"),
    (["import svelte", "from 'svelte'"],            "Svelte"),
    (["import pytest", "pytest.mark"],              "pytest"),
    (["import unittest"],                           "unittest"),
    (["import torch"],                              "PyTorch"),
    (["import tensorflow", "import tf"],            "TensorFlow"),
]


def _detect_language(path: Path) -> str:
    ext = path.suffix.lstrip(".").lower()
    return _EXT_LANG.get(ext, ext.upper() or "unknown")


def _detect_framework(content: str) -> str:
    for signatures, name in _FRAMEWORK_SIGNATURES:
        if any(sig in content for sig in signatures):
            return name
    return "unknown"


# ── Symbol extraction ─────────────────────────────────────────────────────────

_PY_SYM  = re.compile(r"^(?:(?:async\s+)?def|class)\s+(\w+)", re.MULTILINE)
_TS_CLS  = re.compile(r"(?:class|interface|type)\s+(\w+)", re.MULTILINE)
_TS_FN   = re.compile(r"(?:function|const|let|var)\s+(\w+)\s*[=:(]", re.MULTILINE)
_TS_EXP  = re.compile(r"export\s+(?:default\s+)?(?:function|class|const)\s+(\w+)", re.MULTILINE)


def _extract_symbols(content: str, lang: str) -> list[str]:
    if lang == "Python":
        return _PY_SYM.findall(content)[:30]
    if lang in ("TypeScript", "JavaScript"):
        seen: set[str] = set()
        out: list[str] = []
        for name in _TS_CLS.findall(content) + _TS_EXP.findall(content) + _TS_FN.findall(content):
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out[:30]
    return []


# ── Import extraction ─────────────────────────────────────────────────────────

_PY_IMP = re.compile(r"^(?:import|from)\s+(\S+)", re.MULTILINE)
_TS_IMP = re.compile(r"""from\s+['"]([^'"]+)['"]""", re.MULTILINE)


def _extract_imports(content: str, lang: str) -> list[str]:
    if lang == "Python":
        return list(dict.fromkeys(_PY_IMP.findall(content)))[:20]
    if lang in ("TypeScript", "JavaScript"):
        return list(dict.fromkeys(_TS_IMP.findall(content)))[:20]
    return []


# ── TODO / FIXME ──────────────────────────────────────────────────────────────

_TODO_PY = re.compile(r"#\s*(TODO|FIXME|HACK|XXX)[\s:]+(.*)", re.IGNORECASE)
_TODO_TS = re.compile(r"//\s*(TODO|FIXME|HACK|XXX)[\s:]+(.*)", re.IGNORECASE)


def _extract_todos(content: str, lang: str) -> list[str]:
    pat = _TODO_PY if lang == "Python" else _TODO_TS
    return [f"{m.group(1)}: {m.group(2).strip()}" for m in pat.finditer(content)][:15]


# ── Issue detection ───────────────────────────────────────────────────────────

def _detect_issues(content: str, lang: str) -> list[str]:
    issues: list[str] = []
    if lang == "Python":
        if "import *" in content:
            issues.append("Wildcard import (import *) — namespace pollution risk.")
        if re.search(r"except\s*:", content):
            issues.append("Bare except: — catches all exceptions including KeyboardInterrupt.")
        if re.search(r"\bprint\(", content) and "def " in content:
            issues.append("print() found — consider using logging.")
    if lang in ("TypeScript", "JavaScript"):
        if re.search(r"console\.log\(", content):
            issues.append("console.log() found — remove before production.")
        if lang == "TypeScript" and re.search(r":\s*any\b", content):
            issues.append("`any` type annotation — weakens type safety.")
        if "@ts-ignore" in content:
            issues.append("@ts-ignore found — type checking suppressed.")
    return issues


# ── Purpose summary ───────────────────────────────────────────────────────────

def _summarize(content: str, lang: str, path: Path) -> str:
    if lang == "Python":
        m = re.search(r'"""(.*?)"""', content[:800], re.DOTALL)
        if m:
            return m.group(1).strip().splitlines()[0][:120]
    if lang in ("TypeScript", "JavaScript"):
        m = re.search(r"/\*\*(.*?)\*/", content[:800], re.DOTALL)
        if m:
            return re.sub(r"\s*\*\s?", " ", m.group(1)).strip()[:120]
        m = re.search(r"//\s*(.+)", content[:400])
        if m:
            return m.group(1).strip()[:120]
    return f"{lang} file — {path.stem}"


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_file(file_path: str | None, project: str | None = None) -> dict[str, Any]:
    """
    Analyze the given file and return structured context.
    Thread-safe. Returns empty context gracefully if file unavailable.
    """
    t0 = time.monotonic()
    empty: dict[str, Any] = {
        "project": project, "file": file_path, "language": "unknown",
        "framework": "unknown", "summary": "", "imports": [],
        "symbols": [], "todos": [], "issues": [], "hash": None,
        "latency_ms": 0, "cache_hit": False,
    }

    if not file_path:
        return empty

    path = Path(file_path)
    if not path.exists() or not path.is_file():
        logger.warning("[ACTIVE_FILE] not found: %s project=%s", file_path, project)
        return {**empty, "latency_ms": int((time.monotonic() - t0) * 1000)}

    try:
        size = path.stat().st_size
        if size > _MAX_BYTES:
            logger.warning("[ACTIVE_FILE] too large (%d KB): %s", size // 1024, file_path)
            return {**empty, "file": file_path,
                    "summary": f"File too large ({size // 1024} KB) — skipped.",
                    "latency_ms": int((time.monotonic() - t0) * 1000)}

        content = path.read_text(encoding="utf-8", errors="replace")
        h = hashlib.md5(content.encode()).hexdigest()

        if h in _CACHE:
            logger.debug("[ACTIVE_FILE] cache_hit=true hash=%s file=%s", h[:8], path.name)
            return {**_CACHE[h], "cache_hit": True,
                    "latency_ms": int((time.monotonic() - t0) * 1000)}

        lang = _detect_language(path)
        result: dict[str, Any] = {
            "project":   project,
            "file":      file_path,
            "language":  lang,
            "framework": _detect_framework(content),
            "summary":   _summarize(content, lang, path),
            "imports":   _extract_imports(content, lang),
            "symbols":   _extract_symbols(content, lang),
            "todos":     _extract_todos(content, lang),
            "issues":    _detect_issues(content, lang),
            "hash":      h,
            "cache_hit": False,
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }
        _CACHE[h] = result
        logger.info("[ACTIVE_FILE] analyzed file=%s lang=%s symbols=%d latency_ms=%d cache_hit=false action=analyze project=%s",
                    path.name, lang, len(result["symbols"]), result["latency_ms"], project)
        return result

    except Exception as exc:
        logger.error("[ACTIVE_FILE] error file=%s: %s", file_path, exc)
        return {**empty, "latency_ms": int((time.monotonic() - t0) * 1000)}
