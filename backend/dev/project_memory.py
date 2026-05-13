"""
Project Memory — persistent JSON memory per project.

[PROJECT_MEMORY] log prefix.
Storage: backend/data/project_memory/{12-char-hash}.json
Atomic writes (write tmp → rename).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "project_memory"


def _phash(project_path: str) -> str:
    return hashlib.md5(project_path.encode()).hexdigest()[:12]


def _pfile(project_path: str) -> Path:
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return _DATA_ROOT / f"{_phash(project_path)}.json"


def _blank(project_path: str) -> dict[str, Any]:
    return {
        "project_path":      project_path,
        "project_name":      Path(project_path).name,
        "detected_stack":    [],
        "package_manager":   "unknown",
        "git_branch":        "unknown",
        "common_commands":   [],
        "architecture_notes": [],
        "recurring_errors":  [],
        "user_preferences":  {},
        "recent_files":      [],
        "recent_tasks":      [],
        "previous_fixes":    [],
        "session_summaries": [],
        "created_at":        time.time(),
        "updated_at":        time.time(),
    }


def _load(project_path: str) -> dict[str, Any]:
    f = _pfile(project_path)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception as exc:
            logger.warning("[PROJECT_MEMORY] corrupt json %s: %s", f.name, exc)
    return _blank(project_path)


def _save(project_path: str, data: dict) -> None:
    f = _pfile(project_path)
    data["updated_at"] = time.time()
    tmp = f.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(f)
    except Exception as exc:
        logger.error("[PROJECT_MEMORY] save failed: %s", exc)


# ── Stack auto-detection ──────────────────────────────────────────────────────

def _detect_stack(p: Path) -> list[str]:
    checks = [
        ("package.json",       "Node.js"),
        ("requirements.txt",   "Python"),
        ("pyproject.toml",     "Python"),
        ("Cargo.toml",         "Rust"),
        ("go.mod",             "Go"),
        ("pom.xml",            "Java/Maven"),
        ("build.gradle",       "Java/Gradle"),
        ("Gemfile",            "Ruby"),
        ("composer.json",      "PHP"),
        ("pubspec.yaml",       "Dart/Flutter"),
        ("docker-compose.yml", "Docker"),
        ("Dockerfile",         "Docker"),
        (".github",            "GitHub Actions"),
    ]
    glob_checks = [
        ("next.config.*",      "Next.js"),
        ("vite.config.*",      "Vite"),
        ("tailwind.config.*",  "Tailwind CSS"),
    ]
    stack: list[str] = []
    for fname, label in checks:
        if (p / fname).exists():
            stack.append(label)
    for pattern, label in glob_checks:
        if list(p.glob(pattern)):
            stack.append(label)
    return stack


def _detect_pkg_manager(p: Path) -> str:
    if (p / "pnpm-lock.yaml").exists():    return "pnpm"
    if (p / "yarn.lock").exists():         return "yarn"
    if (p / "package-lock.json").exists(): return "npm"
    if (p / "poetry.lock").exists():       return "poetry"
    if (p / "Pipfile.lock").exists():      return "pipenv"
    if (p / "requirements.txt").exists():  return "pip"
    return "unknown"


def _git_branch(p: Path) -> str:
    head = p / ".git" / "HEAD"
    if head.exists():
        try:
            text = head.read_text().strip()
            if text.startswith("ref: refs/heads/"):
                return text.replace("ref: refs/heads/", "")
        except Exception:
            pass
    return "unknown"


# ── Public API ────────────────────────────────────────────────────────────────

def get_memory(project_path: str) -> dict[str, Any]:
    t0 = time.monotonic()
    data = _load(project_path)
    p = Path(project_path)

    # Auto-populate on first load
    if not data.get("detected_stack") and p.exists():
        data["detected_stack"]  = _detect_stack(p)
        data["package_manager"] = _detect_pkg_manager(p)
        data["git_branch"]      = _git_branch(p)
        _save(project_path, data)

    logger.info("[PROJECT_MEMORY] loaded project=%s stack=%s latency_ms=%d cache_hit=false action=load",
                data["project_name"], data["detected_stack"],
                int((time.monotonic() - t0) * 1000))
    return data


def update_memory(project_path: str, updates: dict[str, Any]) -> dict[str, Any]:
    t0 = time.monotonic()
    data = _load(project_path)
    _UPDATABLE = {"architecture_notes", "user_preferences", "common_commands",
                  "recent_tasks", "git_branch"}
    for k, v in updates.items():
        if k not in _UPDATABLE:
            continue
        if isinstance(v, list) and isinstance(data.get(k), list):
            data[k] = list(dict.fromkeys(data[k] + v))[:30]
        else:
            data[k] = v
    _save(project_path, data)
    logger.info("[PROJECT_MEMORY] updated project=%s keys=%s latency_ms=%d action=update",
                data["project_name"], list(updates.keys()),
                int((time.monotonic() - t0) * 1000))
    return data


def record_file_visit(project_path: str, file_path: str) -> None:
    data = _load(project_path)
    recent: list[str] = data.get("recent_files", [])
    if file_path in recent:
        recent.remove(file_path)
    recent.insert(0, file_path)
    data["recent_files"] = recent[:20]
    _save(project_path, data)


def record_error(project_path: str, summary: str, error_type: str) -> None:
    data = _load(project_path)
    errors: list[dict] = data.get("recurring_errors", [])
    for e in errors:
        if e.get("type") == error_type:
            e["count"] = e.get("count", 1) + 1
            e["last_seen"] = time.time()
            _save(project_path, data)
            return
    errors.insert(0, {"type": error_type, "summary": summary[:200],
                      "count": 1, "last_seen": time.time()})
    data["recurring_errors"] = errors[:20]
    _save(project_path, data)


def record_fix(project_path: str, problem: str, fix: str) -> None:
    data = _load(project_path)
    fixes: list[dict] = data.get("previous_fixes", [])
    fixes.insert(0, {"problem": problem[:200], "fix": fix[:500], "timestamp": time.time()})
    data["previous_fixes"] = fixes[:20]
    _save(project_path, data)


def add_session_summary(project_path: str, summary: str) -> None:
    data = _load(project_path)
    summaries: list[dict] = data.get("session_summaries", [])
    summaries.insert(0, {"text": summary[:500], "timestamp": time.time()})
    data["session_summaries"] = summaries[:10]
    _save(project_path, data)
    logger.info("[PROJECT_MEMORY] session_summary added project=%s action=summary", data["project_name"])
