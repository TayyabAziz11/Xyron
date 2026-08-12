from __future__ import annotations

"""
SystemAdminAgent — deep, system-administrator-style disk scan, risk
classification, polished report, and safe selective cleanup.

Builds on top of (never duplicates the safety logic of) the existing
TempCleaner / BrowserCacheCleaner / DiskAnalyzer / LargeFileFinder /
DuplicateFinder modules — this module adds the wider category coverage
(Windows Update cache, thumbnail/shader caches, crash dumps, log files,
dev-tool caches, Recycle Bin, Docker) plus risk classification, a
polished Markdown report, and expert-style safety explanations.

Risk levels: "safe" (clean automatically once approved), "review"
(shown, never auto-selected — needs an explicit ask), "risky" (shown,
requires manual confirmation), "protected" (never touched, ever —
personal documents, source code / project folders, photos/videos,
unknown files).

Log tags: [SYSADMIN_NARRATION] [CLEANER_SAFETY_EXPLANATION]
[CLEANER_RECOMMENDATION] [CLEANER_CATEGORY_FOUND] [CLEANER_REPORT_SAVED]
[SAFETY_BLOCKED_ACTION]
"""

import asyncio
import glob
import logging
import shutil as _shutil
import time
from pathlib import Path
from typing import Optional

try:
    import humanize as _humanize
    _HAS_HUMANIZE = True
except ImportError:
    _HAS_HUMANIZE = False

try:
    from send2trash import send2trash as _s2t
    _HAS_SEND2TRASH = True
except ImportError:
    _HAS_SEND2TRASH = False
    _s2t = None  # type: ignore[assignment]

logger = logging.getLogger("api.agents.automation_agent.system_admin")

REPORTS_DIR = Path.home() / ".xyron" / "reports" / "cleanup"


def _naturalsize(n: int) -> str:
    if _HAS_HUMANIZE:
        return _humanize.naturalsize(n, binary=True)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024  # type: ignore[assignment]
    return f"{n:.1f} GiB"


def _dir_size(path: Path) -> tuple[int, int]:
    total = 0
    count = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
                    count += 1
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return total, count


def _user_glob(sub: str) -> str:
    return f"/mnt/c/Users/*/{sub}"


_RISK_RECOMMENDATION = {
    "safe": "Clean now",
    "review": "Ask first",
    "risky": "Manual review required",
    "protected": "Never auto-delete",
}


# ── CacheCategoryScanner — one glob-pattern-based cleanup category ─────────────

class CacheCategoryScanner:
    """Scans a set of glob patterns and reports size/count for one category.
    Never deletes anything itself — pure read-only awareness."""

    def __init__(self, key: str, label: str, patterns: list[str], risk: str, explanation: str):
        self.key = key
        self.label = label
        self.patterns = patterns
        self.risk = risk  # "safe" | "review" | "risky" | "protected"
        self.explanation = explanation

    def scan_sync(self) -> dict:
        total = 0
        count = 0
        found: list[str] = []
        for pattern in self.patterns:
            for p in glob.glob(pattern):
                path = Path(p)
                try:
                    if path.is_dir():
                        sz, ct = _dir_size(path)
                    elif path.is_file():
                        sz, ct = path.stat().st_size, 1
                    else:
                        continue
                except (PermissionError, OSError):
                    continue
                total += sz
                count += ct
                found.append(p)
        return {
            "key": self.key, "label": self.label, "risk": self.risk,
            "size_bytes": total, "count": count, "paths": found,
            "explanation": self.explanation,
        }

    async def scan(self) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.scan_sync)


CATEGORY_DEFS: list[CacheCategoryScanner] = [
    CacheCategoryScanner("windows_update_cache", "Windows Update cache",
        ["/mnt/c/Windows/SoftwareDistribution/Download"], "safe",
        "Temp files are safe because Windows recreates them automatically."),
    CacheCategoryScanner("delivery_optimization_cache", "Delivery Optimization cache",
        ["/mnt/c/Windows/SoftwareDistribution/DeliveryOptimization",
         "/mnt/c/ProgramData/Microsoft/Network/Downloader"], "safe",
        "Windows rebuilds this cache automatically as needed."),
    CacheCategoryScanner("thumbnail_cache", "Thumbnail cache",
        [_user_glob("AppData/Local/Microsoft/Windows/Explorer")], "safe",
        "Windows regenerates thumbnails automatically."),
    CacheCategoryScanner("directx_shader_cache", "DirectX shader cache",
        [_user_glob("AppData/Local/D3DSCache")], "safe",
        "Shader caches rebuild automatically the next time a game or app runs."),
    CacheCategoryScanner("nvidia_shader_cache", "NVIDIA shader cache",
        [_user_glob("AppData/Local/NVIDIA/DXCache"), _user_glob("AppData/Local/NVIDIA/GLCache")], "safe",
        "NVIDIA regenerates this cache automatically."),
    CacheCategoryScanner("crash_dumps", "Crash dumps",
        ["/mnt/c/Windows/Minidump", _user_glob("AppData/Local/CrashDumps")], "safe",
        "Old crash dumps are safe to clear once any recent crash has been reviewed."),
    CacheCategoryScanner("log_files", "Log files",
        ["/mnt/c/Windows/Logs", _user_glob("AppData/Local/Temp/*.log")], "safe",
        "Old logs are safe to clear — Windows and apps recreate them as needed."),
    CacheCategoryScanner("npm_cache", "npm cache",
        [_user_glob("AppData/Roaming/npm-cache")], "review",
        "I'm skipping project caches because you're actively developing Xyron."),
    CacheCategoryScanner("pnpm_cache", "pnpm cache",
        [_user_glob("AppData/Local/pnpm/store")], "review",
        "I'm skipping project caches because you're actively developing Xyron."),
    CacheCategoryScanner("pip_cache", "pip cache",
        [_user_glob("AppData/Local/pip/Cache")], "review",
        "I'm skipping project caches because you're actively developing Xyron."),
    CacheCategoryScanner("conda_cache", "conda package cache",
        [_user_glob(".conda/pkgs"), _user_glob("Miniconda3/pkgs"), _user_glob("Anaconda3/pkgs")], "review",
        "I'm skipping project caches because you're actively developing Xyron."),
    CacheCategoryScanner("vscode_cache", "VS Code cache",
        [_user_glob("AppData/Roaming/Code/Cache"), _user_glob("AppData/Roaming/Code/CachedData")], "review",
        "I'm treating editor caches as review-only since VS Code is one of your active tools."),
    CacheCategoryScanner("visual_studio_cache", "Visual Studio cache",
        [_user_glob("AppData/Local/Microsoft/VisualStudio/*/ComponentModelCache")], "review",
        "I'm skipping project caches because you're actively developing Xyron."),
]

_DEV_CACHE_KEYS = {"npm_cache", "pnpm_cache", "pip_cache", "conda_cache", "vscode_cache", "visual_studio_cache"}
_WSL_CACHE_PATTERNS = ["/tmp", "/var/cache/apt/archives"]

PROTECTED_ROWS: list[dict] = [
    {"key": "source_code_folders", "label": "Source Code Folders", "risk": "protected",
     "size_bytes": 0, "count": 0, "paths": [],
     "explanation": "Source code and project folders are never touched, no matter what's asked."},
    {"key": "personal_documents", "label": "Personal Documents", "risk": "protected",
     "size_bytes": 0, "count": 0, "paths": [],
     "explanation": "Personal documents are never scanned for deletion."},
    {"key": "photos_videos", "label": "Photos/Videos", "risk": "protected",
     "size_bytes": 0, "count": 0, "paths": [],
     "explanation": "Photos and videos are never touched without you opening and reviewing them yourself."},
]


# ── DeveloperCacheScanner — groups the dev-tool categories ─────────────────────

class DeveloperCacheScanner:
    """Groups npm/pnpm/pip/conda/VS Code/Visual Studio caches. Always
    'review' risk — never auto-cleaned, because this machine is actively
    used for Xyron development."""

    CATEGORIES = [c for c in CATEGORY_DEFS if c.key in _DEV_CACHE_KEYS]

    @classmethod
    async def scan(cls) -> list[dict]:
        return list(await asyncio.gather(*(c.scan() for c in cls.CATEGORIES)))

    @classmethod
    async def scan_docker(cls) -> Optional[dict]:
        """Docker's cache lives inside its WSL2 VM disk, not as ordinary
        files — only report if the CLI is present, and be honest that we
        aren't estimating a real number from the filesystem."""
        if not _shutil.which("docker"):
            return None
        return {
            "key": "docker_cache", "label": "Docker cache", "risk": "review",
            "size_bytes": 0, "count": 0, "paths": [],
            "explanation": (
                "Docker is installed, but its cache lives inside the WSL2 VM disk — "
                "run `docker system df` for an exact figure; I won't estimate it from the filesystem."
            ),
        }


# ── DeepDiskAnalyzer — everything beyond the original 4 categories ─────────────

class DeepDiskAnalyzer:
    """Scans every system-administrator-relevant category this module adds
    on top of temp/browser-cache/downloads/desktop. Read-only — never
    deletes anything itself."""

    SAFE_SYSTEM_CATEGORIES = [c for c in CATEGORY_DEFS if c.risk == "safe"]

    async def scan_system_categories(self) -> list[dict]:
        results = list(await asyncio.gather(*(c.scan() for c in self.SAFE_SYSTEM_CATEGORIES)))
        for r in results:
            if r["size_bytes"] or r["count"]:
                logger.info(
                    "[CLEANER_CATEGORY_FOUND] category=%s size_bytes=%d desc=%r risk=%s",
                    r["key"], r["size_bytes"], f"{r['count']:,} files", r["risk"],
                )
        return results

    async def scan_recycle_bin(self) -> dict:
        loop = asyncio.get_event_loop()

        def _scan() -> dict:
            total, count = 0, 0
            for drive_bin in glob.glob("/mnt/*/$Recycle.Bin"):
                sz, ct = _dir_size(Path(drive_bin))
                total += sz
                count += ct
            return {
                "key": "recycle_bin", "label": "Recycle Bin", "risk": "review",
                "size_bytes": total, "count": count, "paths": [],
                "explanation": "Emptying the Recycle Bin is permanent — I'll only do it if you explicitly ask.",
            }

        return await loop.run_in_executor(None, _scan)

    async def scan_wsl_categories(self) -> dict:
        """WSL temp + apt cache — awareness only. Never auto-cleaned even
        though it would otherwise qualify as 'safe', since this WSL
        instance is the active Xyron dev environment."""
        loop = asyncio.get_event_loop()

        def _scan() -> dict:
            total, count = 0, 0
            for pattern in _WSL_CACHE_PATTERNS:
                p = Path(pattern)
                if p.exists():
                    sz, ct = _dir_size(p)
                    total += sz
                    count += ct
            return {
                "key": "wsl_cache", "label": "WSL temp/cache", "risk": "review",
                "size_bytes": total, "count": count, "paths": _WSL_CACHE_PATTERNS,
                "explanation": "Left alone by default — this WSL environment is where Xyron itself runs.",
            }

        return await loop.run_in_executor(None, _scan)


# ── HealthRecommendationEngine ──────────────────────────────────────────────────

class HealthRecommendationEngine:
    """Turns a categorized report into a first-action recommendation and a
    short spoken recommendation line."""

    @staticmethod
    def recommend(rows: list[dict]) -> tuple[str, str]:
        safe_total = sum(r["size_bytes"] for r in rows if r["risk"] == "safe")
        review_total = sum(r["size_bytes"] for r in rows if r["risk"] == "review")

        if safe_total > 0:
            first_action = f"Clean the safe categories now ({_naturalsize(safe_total)})."
            recommendation = f"I can safely clean {_naturalsize(safe_total)} right now."
        elif review_total > 0:
            first_action = "Review the flagged categories before cleaning anything."
            recommendation = "Nothing is safe to auto-clean yet — take a look at the review items first."
        else:
            first_action = "Nothing significant to clean right now."
            recommendation = "Your system looks clean — nothing significant to recover right now."

        logger.info("[CLEANER_RECOMMENDATION] text=%r", recommendation)
        return recommendation, first_action


# ── CleanupSafetyExplainer ──────────────────────────────────────────────────────

class CleanupSafetyExplainer:
    """Generates the plain-English safety reasoning per category, and logs
    each line with [CLEANER_SAFETY_EXPLANATION] as required."""

    @staticmethod
    def explain(rows: list[dict]) -> list[str]:
        lines: list[str] = []
        for r in rows:
            if r["risk"] == "protected":
                continue
            if not r.get("size_bytes") and not r.get("count"):
                continue
            explanation = r.get("explanation") or ""
            if not explanation:
                continue
            line = f"{r['label']}: {explanation}"
            lines.append(line)
            logger.info("[CLEANER_SAFETY_EXPLANATION] category=%s text=%r", r["key"], explanation)
        return lines


# ── CleanupPlanBuilder ──────────────────────────────────────────────────────────

class CleanupPlanBuilder:
    """Builds the actual set of categories to clean from risk + selection.
    Refuses to include anything not marked 'safe', even if a voice command
    explicitly asks for it — logs [SAFETY_BLOCKED_ACTION] instead of
    silently complying."""

    @staticmethod
    def build(rows_by_key: dict[str, dict], include: Optional[set], exclude: Optional[set]) -> dict:
        exclude = exclude or set()
        safe_keys = {k for k, r in rows_by_key.items() if r["risk"] == "safe"}

        blocked: set[str] = set()
        if include:
            blocked = {k for k in include if k not in safe_keys}
            for k in blocked:
                risk = rows_by_key.get(k, {}).get("risk", "unknown")
                logger.info(
                    "[SAFETY_BLOCKED_ACTION] action=clean category=%s reason=risk_level_%s", k, risk,
                )
            selected = include & safe_keys
        else:
            selected = safe_keys - exclude

        return {"selected": selected, "blocked": blocked}


# ── CleanupFollowupPlanner ──────────────────────────────────────────────────────

class CleanupFollowupPlanner:
    """Suggests optimization follow-ups after a cleanup completes. Purely
    narrative — routing an affirmative reply reuses the existing
    'check startup apps' / 'find duplicates' voice commands."""

    @staticmethod
    def suggestions(rows_by_key: dict[str, dict]) -> list[str]:
        out = [
            "Would you like me to also check startup apps?",
        ]
        if rows_by_key.get("duplicates", {}).get("count"):
            out.append("Would you like me to find duplicate videos too?")
        out.append("Would you like me to generate a full system health report?")
        return out


# ── Report builder ──────────────────────────────────────────────────────────────

def build_report_table(rows: list[dict]) -> str:
    """Renders the Category | Size | Risk | Recommendation table. Protected
    rows always show (as a visible "never touched" declaration); every
    other category is only listed if something was actually found, so an
    empty NVIDIA/conda/Visual Studio cache on a machine that doesn't have
    them doesn't clutter the report."""
    lines = ["| Category | Size | Risk | Recommendation |", "|---|---|---|---|"]
    for r in rows:
        if r["risk"] != "protected" and not r["size_bytes"] and not r["count"]:
            continue
        size_str = _naturalsize(r["size_bytes"]) if r["size_bytes"] else (f"{r['count']} item(s)" if r["count"] else "0 MB")
        recommendation = _RISK_RECOMMENDATION.get(r["risk"], "Review")
        lines.append(f"| {r['label']} | {size_str} | {r['risk'].title()} | {recommendation} |")
    return "\n".join(lines)


async def recycle_category(row: dict) -> dict:
    """Move every path in row['paths'] to the Recycle Bin via send2trash.
    Callers must only invoke this for rows CleanupPlanBuilder actually
    selected (i.e. already verified risk == 'safe')."""
    if not _HAS_SEND2TRASH:
        return {"freed_bytes": 0, "recycled": 0, "failed": ["send2trash not installed"]}

    loop = asyncio.get_event_loop()

    def _do() -> dict:
        freed = 0
        recycled = 0
        failed: list[str] = []
        for p in row.get("paths", []):
            path = Path(p)
            if not path.exists():
                continue
            try:
                sz, _ct = _dir_size(path) if path.is_dir() else (path.stat().st_size, 1)
                _s2t(str(path))
                freed += sz
                recycled += 1
                logger.info("[CLEANER_DELETE_TO_RECYCLE] path=%s category=%s", path, row["key"])
            except Exception as exc:
                failed.append(str(p))
                logger.warning(
                    "[CLEANER_DELETE_TO_RECYCLE] FAILED path=%s category=%s err=%r", p, row["key"], exc,
                )
        return {"freed_bytes": freed, "recycled": recycled, "failed": failed}

    return await loop.run_in_executor(None, _do)


def save_report(report_markdown: str, task_id: str) -> Optional[Path]:
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = time.strftime("%Y-%m-%d", time.localtime())
        path = REPORTS_DIR / f"{date_str}-cleanup-report.md"
        path.write_text(report_markdown, encoding="utf-8")
        logger.info("[CLEANER_REPORT_SAVED] path=%s task=%s", path, task_id)
        return path
    except Exception as exc:
        logger.warning("[CLEANER_REPORT_SAVED] error=%r", str(exc))
        return None


def save_json_report(rows: list[dict], totals: dict, task_id: str) -> Optional[Path]:
    """Machine-readable counterpart to save_report() — same filename stem,
    .json instead of .md, same directory."""
    import json as _json
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = time.strftime("%Y-%m-%d", time.localtime())
        path = REPORTS_DIR / f"{date_str}-cleanup-report.json"
        payload = {
            "task_id": task_id,
            "generated_at": time.time(),
            "categories": [
                {k: v for k, v in r.items() if k != "paths"} for r in rows
            ],
            "totals": totals,
        }
        path.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("[CLEANER_JSON_REPORT_SAVED] path=%s task=%s", path, task_id)
        return path
    except Exception as exc:
        logger.warning("[CLEANER_JSON_REPORT_SAVED] error=%r", str(exc))
        return None


def reconcile_totals(rows: list[dict]) -> dict:
    """Canonical, double-counting-safe totals.

    Dedupes rows by key (a duplicate key in the raw list — e.g. if a
    future edit accidentally appends the same category twice — would
    silently double the reported total if summed directly) and reports
    both the raw and deduped sums so a real mismatch is visible instead
    of silently trusted. The deduped values are what the report and the
    actual cleanup plan must use.
    """
    rows_by_key = {r["key"]: r for r in rows}
    raw_safe = sum(r["size_bytes"] for r in rows if r["risk"] == "safe")
    dedup_safe = sum(r["size_bytes"] for r in rows_by_key.values() if r["risk"] == "safe")
    raw_review = sum(r["size_bytes"] for r in rows if r["risk"] == "review")
    dedup_review = sum(r["size_bytes"] for r in rows_by_key.values() if r["risk"] == "review")
    raw_protected = sum(r["size_bytes"] for r in rows if r["risk"] in ("risky", "protected"))
    dedup_protected = sum(r["size_bytes"] for r in rows_by_key.values() if r["risk"] in ("risky", "protected"))

    reconciled = (raw_safe == dedup_safe) and (raw_review == dedup_review) and (raw_protected == dedup_protected)
    if reconciled:
        logger.info(
            "[CLEANER_TOTAL_RECONCILED] status=OK safe=%d review=%d risky_protected=%d",
            dedup_safe, dedup_review, dedup_protected,
        )
    else:
        logger.warning(
            "[CLEANER_TOTAL_RECONCILED] status=MISMATCH raw_safe=%d dedup_safe=%d "
            "raw_review=%d dedup_review=%d — duplicate category keys detected",
            raw_safe, dedup_safe, raw_review, dedup_review,
        )

    return {
        "safe_bytes": dedup_safe, "review_bytes": dedup_review, "risky_protected_bytes": dedup_protected,
        "reconciled": reconciled, "category_count": len(rows_by_key),
    }


async def check_active_project() -> bool:
    """Best-effort check for whether VS Code (or another dev tool) is
    currently running on the Windows side — used to protect developer
    caches more strongly than a static risk label. Returns False (not an
    exception) if the check can't be performed; never blocks the scan."""
    candidates = [
        "/mnt/c/Windows/System32/tasklist.exe",
        "/mnt/c/WINDOWS/System32/tasklist.exe",
    ]
    exe = next((p for p in candidates if Path(p).exists()), None)
    if not exe:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "/init", exe, "/FI", "IMAGENAME eq Code.exe",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        active = b"Code.exe" in out
        if active:
            logger.info("[CLEANER_ACTIVE_PROJECT_PROTECTED] reason=vscode_running")
        return active
    except Exception as exc:
        logger.debug("[CLEANER_ACTIVE_PROJECT_CHECK] skipped: %r", exc)
        return False
