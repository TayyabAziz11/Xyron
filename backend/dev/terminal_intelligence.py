"""
Terminal Intelligence — classifies shell errors and suggests fixes.

[TERMINAL_INTEL] log prefix.
Strategy: regex-first (fast, high confidence) → Ollama mistral:7b (low confidence only).
Cache keyed on hash of command + first 500 chars of output.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

_OLLAMA_BASE = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
_CACHE: dict[str, dict] = {}

# Stored reference to latest terminal event (ring buffer of 1 per project)
_LATEST: dict[str, dict] = {}  # project → latest terminal analysis


# ── Pattern catalogue ─────────────────────────────────────────────────────────

@dataclass
class _P:
    name:           str
    patterns:       list[str]
    severity:       str
    summary:        str
    likely_cause:   str
    fix:            str
    commands:       list[str] = field(default_factory=list)
    files_to_check: list[str] = field(default_factory=list)
    _compiled:      list[re.Pattern] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in self.patterns]

    def match(self, text: str) -> bool:
        return any(r.search(text) for r in self._compiled)


_PATTERNS: list[_P] = [
    _P("npm_missing_module",
       [r"Cannot find module '(.+)'", r"Module not found: Error: Can't resolve"],
       "high",
       "Missing npm/Node module.",
       "Package not installed or import path is wrong.",
       "Run npm install. If the module exists, check the import path.",
       ["npm install", "npm install <package-name>"],
       ["package.json"]),

    _P("npm_vulnerability",
       [r"found \d+ vulnerabilit"],
       "medium",
       "npm audit found vulnerabilities.",
       "Outdated packages with known security issues.",
       "Run npm audit fix or upgrade affected packages.",
       ["npm audit fix", "npm update"]),

    _P("python_import_error",
       [r"ModuleNotFoundError: No module named", r"ImportError: cannot import name"],
       "high",
       "Python import failed.",
       "Package not installed or wrong import path.",
       "Install the missing package: pip install <name>",
       ["pip install <package-name>", "pip list"]),

    _P("python_traceback",
       [r"Traceback \(most recent call last\)"],
       "high",
       "Python exception raised.",
       "Runtime error — see last line of traceback for type and message.",
       "Read the last line of the traceback. Fix the root cause, not the symptom.",
       []),

    _P("python_syntax_error",
       [r"SyntaxError:", r"IndentationError:", r"TabError:"],
       "high",
       "Python syntax error.",
       "Invalid Python — missing colon, bracket, or bad indentation.",
       "Open the file at the indicated line and fix the syntax.",
       ["python3 -m py_compile <file>"]),

    _P("typescript_error",
       [r"TS\d+:", r"error TS\d+", r"Type '(.+)' is not assignable to type"],
       "medium",
       "TypeScript compilation error.",
       "Type mismatch or missing annotation.",
       "Run tsc --noEmit to see all errors. Check the type definitions.",
       ["tsc --noEmit", "npx tsc --noEmit"]),

    _P("nextjs_build_error",
       [r"Next\.js build failed", r"Failed to compile\.", r"Build error occurred"],
       "high",
       "Next.js build failed.",
       "Compile error: type error, missing env var, or bad import.",
       "Check the build output. Common causes: missing env vars, type errors.",
       ["npm run build 2>&1 | head -100"]),

    _P("vite_error",
       [r"\[vite\] error", r"Could not resolve import"],
       "medium",
       "Vite build error.",
       "Import resolution failure or plugin misconfiguration.",
       "Check the import path and vite.config.ts.",
       ["npx vite --debug"],
       ["vite.config.ts", "vite.config.js"]),

    _P("docker_error",
       [r"docker: Error", r"Error response from daemon", r"container.*not running"],
       "medium",
       "Docker error.",
       "Container startup failure or daemon issue.",
       "Check docker logs. Ensure Docker Desktop is running.",
       ["docker ps -a", "docker logs <container-id>"]),

    _P("git_conflict",
       [r"CONFLICT \(", r"Merge conflict in", r"Automatic merge failed"],
       "medium",
       "Git merge conflict.",
       "Conflicting changes between branches.",
       "Resolve conflict markers (<<<, ===, >>>) in each file, then git add + commit.",
       ["git status", "git diff --name-only --diff-filter=U"]),

    _P("permission_denied",
       [r"Permission denied", r"EACCES:", r"Access is denied"],
       "medium",
       "Permission denied.",
       "Insufficient file/directory access rights.",
       "Check ownership with ls -la. Fix with chmod or sudo as appropriate.",
       ["ls -la <path>", "chmod +x <file>"]),

    _P("port_in_use",
       [r"EADDRINUSE", r"address already in use", r"port \d+ is already in use"],
       "medium",
       "Port already in use.",
       "Another process is bound to this port.",
       "Find and kill the process, or change the application port.",
       ["lsof -i :<port>", "kill -9 $(lsof -t -i:<port>)"]),

    _P("pip_error",
       [r"ERROR: Could not install packages", r"pkg_resources\.DistributionNotFound"],
       "medium",
       "pip install failed.",
       "Dependency conflict or network issue.",
       "Try pip install --upgrade pip, then retry. Check for version conflicts.",
       ["pip install --upgrade pip", "pip install -r requirements.txt"]),

    _P("yarn_error",
       [r"yarn error", r"error Command failed with exit code"],
       "medium",
       "Yarn command failed.",
       "Dependency install or script execution failure.",
       "Delete node_modules + yarn.lock, then run yarn install.",
       ["rm -rf node_modules yarn.lock && yarn install"]),

    _P("pnpm_error",
       [r"pnpm ERR!", r"ERR_PNPM_"],
       "medium",
       "pnpm error.",
       "Package resolution or execution failure.",
       "Run pnpm install or clear the store.",
       ["pnpm install", "pnpm store prune"]),

    _P("out_of_memory",
       [r"JavaScript heap out of memory", r"MemoryError", r"out of memory"],
       "high",
       "Out of memory.",
       "Process exhausted available RAM.",
       "Increase Node.js heap: NODE_OPTIONS=--max-old-space-size=4096",
       ["NODE_OPTIONS=--max-old-space-size=4096 npm run build"]),
]

_ERROR_SIGNAL = re.compile(
    r"error|fail|exception|crash|abort|fatal|denied|not found|traceback",
    re.IGNORECASE,
)


# ── Analysis ──────────────────────────────────────────────────────────────────

def _cache_key(output: str, command: str) -> str:
    return hashlib.md5(f"{command}||{output[:500]}".encode()).hexdigest()


def _regex_analyze(output: str) -> dict[str, Any] | None:
    for p in _PATTERNS:
        if p.match(output):
            return {
                "detected": True, "error_type": p.name, "severity": p.severity,
                "summary": p.summary, "likely_cause": p.likely_cause,
                "suggested_fix": p.fix, "commands": p.commands,
                "files_to_check": p.files_to_check,
                "confidence": "high", "source": "regex",
            }
    return None


def _ollama_analyze(output: str, command: str) -> dict[str, Any]:
    prompt = (
        "Analyze this terminal error. Respond with ONLY valid JSON, no prose.\n\n"
        f"Command: {command}\nOutput (last 1000 chars):\n{output[-1000:]}\n\n"
        'JSON format: {"error_type":"...","severity":"low|medium|high",'
        '"summary":"...","likely_cause":"...","suggested_fix":"...","commands":[],"files_to_check":[]}'
    )
    try:
        resp = requests.post(
            f"{_OLLAMA_BASE}/api/generate",
            json={"model": "mistral:7b", "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.1, "num_predict": 256}},
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        start, end = text.find("{"), text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            return {
                "detected": True,
                "error_type":    data.get("error_type", "unknown"),
                "severity":      data.get("severity", "medium"),
                "summary":       data.get("summary", ""),
                "likely_cause":  data.get("likely_cause", ""),
                "suggested_fix": data.get("suggested_fix", ""),
                "commands":      data.get("commands", []),
                "files_to_check": data.get("files_to_check", []),
                "confidence": "medium", "source": "ollama",
            }
    except Exception as exc:
        logger.error("[TERMINAL_INTEL] Ollama fallback failed: %s", exc)
    return {
        "detected": True, "error_type": "unknown", "severity": "medium",
        "summary": "Error detected but could not be classified.",
        "likely_cause": "Check the full terminal output manually.",
        "suggested_fix": "Review the error message carefully.",
        "commands": [], "files_to_check": [],
        "confidence": "low", "source": "fallback",
    }


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_terminal(output: str, command: str = "",
                     project: str | None = None, cwd: str | None = None) -> dict[str, Any]:
    """
    Analyze terminal output and return structured error analysis.
    Regex-first, Ollama only when regex confidence is low.
    """
    t0 = time.monotonic()
    key = _cache_key(output, command)

    if key in _CACHE:
        cached = _CACHE[key]
        logger.debug("[TERMINAL_INTEL] cache_hit=true error_type=%s", cached.get("error_type"))
        return {**cached, "cache_hit": True, "latency_ms": int((time.monotonic() - t0) * 1000)}

    result = _regex_analyze(output)

    if result is None:
        if _ERROR_SIGNAL.search(output):
            result = _ollama_analyze(output, command)
        else:
            result = {
                "detected": False, "error_type": None, "severity": "low",
                "summary": "No error detected.", "likely_cause": "",
                "suggested_fix": "", "commands": [], "files_to_check": [],
                "confidence": "high", "source": "regex",
            }

    result["latency_ms"] = int((time.monotonic() - t0) * 1000)
    result["cache_hit"] = False
    _CACHE[key] = result

    logger.info("[TERMINAL_INTEL] command=%r error_type=%s severity=%s "
                "confidence=%s latency_ms=%d cache_hit=false action=analyze project=%s",
                command[:60], result.get("error_type"), result.get("severity"),
                result.get("confidence"), result["latency_ms"], project)

    # Track in project memory + latest store
    if result.get("detected") and project:
        try:
            from .project_memory import record_error
            record_error(project, result.get("summary", ""), result.get("error_type", "unknown"))
        except Exception as exc:
            logger.debug("[TERMINAL_INTEL] project memory update skipped: %s", exc)

    if project:
        _LATEST[project] = {**result, "command": command, "cwd": cwd, "timestamp": time.time()}

    return result


def get_latest_error(project: str) -> dict[str, Any] | None:
    """Return the most recent terminal analysis for this project."""
    return _LATEST.get(project)
