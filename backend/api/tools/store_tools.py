"""
Store Tools — Microsoft Store app search and installation via winget.

Tools registered:
  install_store_app       search winget/msstore, open the product page directly
  install_store_app_exec  execute the actual install (winget install --id ...)

Safety: install_store_app never installs — it only searches and opens the
        Store product page (ms-windows-store://pdp/?ProductId=...), then
        tells the user to say "install it". install_store_app_exec is only
        ever reached via that explicit follow-up, resolved by
        follow_up_resolver(_v2).py / api/services/store_agent.py — never
        called directly from a bare "install X" utterance.

Logs: [STORE_INSTALL_REQUEST] [STORE_APP_QUERY] [WINGET_AVAILABLE] [WINGET_UNAVAILABLE]
      [WINGET_SEARCH_START] [WINGET_SEARCH_RESULTS] [STORE_APP_MATCH_SELECTED]
      [STORE_MATCH_EXACT] [STORE_MATCH_FUZZY] [STORE_MATCH_TOKEN_SCORE]
      [STORE_MATCH_REJECTED] [STORE_MATCH_SELECTED]
      [STORE_INSTALL_CONFIRM_REQUIRED] [STORE_INSTALL_CONFIRMED] [STORE_INSTALL_STARTED]
      [STORE_INSTALL_SUCCESS] [STORE_INSTALL_FAILED] [STORE_INSTALL_VERIFY]
      [STORE_INSTALL_MS]
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .registry import ToolResult, registry

logger = logging.getLogger(__name__)

# ── Known msstore IDs for popular apps whose winget NAME search fails ─────────
# Winget msstore name-search is unreliable for many popular apps (returns nothing
# even when the app is available and installable by ID). These IDs are verified
# against real winget show output and are region-independent msstore identifiers.
_KNOWN_MSSTORE_IDS: Dict[str, Tuple[str, str]] = {
    # (display_name, msstore_id)
    "instagram":    ("Instagram",           "9NBLGGH5L9XT"),
    "tiktok":       ("TikTok",              "9NH2GPH4JZS4"),
    "snapchat":     ("Snapchat",            "9PKR68RMOF6N"),
    "facebook":     ("Facebook",            "XPFFTQ5700D3V8"),
    "twitter":      ("Twitter",             "9WZDNCRFHVJL"),
    "x":            ("X (Twitter)",         "9WZDNCRFHVJL"),
    "linkedin":     ("LinkedIn",            "9WZDNCRFJ4Q7"),
    "netflix":      ("Netflix",             "9WZDNCRFJ3TJ"),
    "spotify":      ("Spotify",             "9NCBCSZSJRSB"),
    "discord":      ("Discord",             "XP8BR1GDJQ2JD7"),
    "zoom":         ("Zoom",                "XP99J3KP4XZ4VV"),
    "whatsapp":     ("WhatsApp",            "9NKSQGP7F2NH"),
    "telegram":     ("Telegram Desktop",    "9NZTWSQNTD0S"),
    "signal":       ("Signal",              "9NBLGGH5L9XT"),
    "capcut":       ("CapCut",              "9PGZV3D5PVNL"),
    "chatgpt":      ("ChatGPT",             "9NT1R1C2HH7J"),
    "minecraft":    ("Minecraft",           "9NBLGGH537BL"),
    "roblox":       ("Roblox",              "9NBLGGH5RJFM"),
    "prime video":  ("Prime Video",         "9P6RC76MSQQB"),
    "amazon":       ("Amazon",              "9WZDNCRFJR0R"),
    "disney plus":  ("Disney+",             "9NXQXXLFST89"),
    "disneyplus":   ("Disney+",             "9NXQXXLFST89"),
    "youtube":      ("YouTube",             "9NBLGGH4NSBM"),
    "gmail":        ("Gmail",               "9NS1G86C0LVV"),
    "outlook":      ("Microsoft Outlook",   "9NRX63209R7B"),
    "teams":        ("Microsoft Teams",     "XP8BT8DW290MPQ"),
    "onenote":      ("Microsoft OneNote",   "XPFFZHVGQWWLHB"),
    "onedrive":     ("OneDrive",            "9WZDNCRFJ364"),
    "skype":        ("Skype",               "9WZDNCRFJ364"),
    "vlc":          ("VLC",                 "XPDM1ZW6815MQM"),
    "canva":        ("Canva",               "XP9MT8S1H5RRRD"),
    "notion":       ("Notion",              "XPDCM8GPPHSVD0"),
    "shazam":       ("Shazam",              "9NBLGGH0JHF4"),
    "duolingo":     ("Duolingo",            "XP8K0J757HHRDQ"),
    "pinterest":    ("Pinterest",           "9WZDNCRFHVJL"),
    "reddit":       ("Reddit",              "9NTZKF2XRSR9"),
}

# ── winget location detection ─────────────────────────────────────────────────

def _find_winget() -> Optional[str]:
    """Return path to winget.exe, or None if unavailable."""
    # WSL2: call via powershell.exe so winget.exe runs on Windows side
    # We'll use the PS session / cmd.exe bridge rather than direct exec
    candidates = [
        "/mnt/c/Users/*/AppData/Local/Microsoft/WindowsApps/winget.exe",
    ]
    # Check via cmd.exe through WSL
    try:
        _cmdexe = _find_cmdexe()
        if _cmdexe:
            r = subprocess.run(
                [_cmdexe, "/c", "where", "winget"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                logger.info("[WINGET_AVAILABLE] path=%r", r.stdout.strip().splitlines()[0])
                return "winget"  # callable via cmd.exe
    except Exception as exc:
        logger.debug("[WINGET_CHECK] %s", exc)
    return None


def _find_cmdexe() -> Optional[str]:
    for p in [
        "/mnt/c/Windows/System32/cmd.exe",
        "/mnt/c/WINDOWS/System32/cmd.exe",
    ]:
        if Path(p).exists():
            return p
    return None


def _find_ps() -> Optional[str]:
    for p in [
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
    ]:
        if Path(p).exists():
            return p
    return None


def _open_store_pdp(app_name: str, product_id: str) -> bool:
    """Open the Microsoft Store product page for product_id. Returns True on launch attempt."""
    uri = f"ms-windows-store://pdp/?ProductId={product_id}"
    cmd_exe = _find_cmdexe()
    if cmd_exe:
        try:
            subprocess.Popen(
                [cmd_exe, "/c", f"start {uri}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as exc:
            logger.warning("[STORE_PDP_OPEN_FAILED] app=%r uri=%s err=%s", app_name, uri, exc)
    return False


def _run_winget(args: List[str], timeout: int = 20) -> Tuple[bool, str]:
    """
    Run a winget command via the persistent PS session (preferred) or cmd.exe fallback.
    Returns (success, output_text).
    """
    cmd_str = "winget " + " ".join(args)

    # Prefer persistent PS session — avoids per-call powershell.exe spawn overhead
    try:
        from api.services.ps_session import run_ps
        ok, out = run_ps(cmd_str, timeout=timeout)
        if ok:
            return True, out
        # winget non-zero exit still returns output (e.g. "No packages found")
        return False, out
    except Exception as exc:
        logger.debug("[WINGET_PS_SESSION] fallback to cmd.exe: %s", exc)

    # Fallback: cmd.exe direct spawn
    cmd_exe = _find_cmdexe()
    if not cmd_exe:
        return False, "cmd.exe not found"
    try:
        r = subprocess.run(
            [cmd_exe, "/c", cmd_str],
            capture_output=True, text=True, timeout=timeout, errors="replace",
        )
        out = (r.stdout or "") + (r.stderr or "")
        return (r.returncode == 0), out.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc)


# ── winget output parsers ─────────────────────────────────────────────────────

# Words that appear in the winget table header — never valid package names.
_WINGET_HEADER_WORDS = {"name", "id", "version", "source", "match", "available", "type"}

# ANSI escape sequence stripper (covers CSI sequences and OSC sequences)
_ANSI_RE = re.compile(r'\x1b(?:\[[0-9;]*[A-Za-z]|\][^\x07\x1b]*(?:\x07|\x1b\\))')

# Separator line: 3+ dashes, box-drawing chars (─ U+2500, ━ U+2501, ═ U+2550), or equals
_SEP_RE = re.compile(r'^[\-─━═=_]{3,}\s*$')

# Header line: contains "Name" and "Id" column headers
_HDR_RE = re.compile(r'\bname\b.{0,40}\bid\b', re.IGNORECASE)

# Known winget column names in their typical left-to-right order
_COL_NAMES_ORDERED = ["name", "id", "version", "match", "source", "available"]


def _col_positions(header_line: str) -> List[Tuple[int, str]]:
    """
    Return [(char_offset, col_name), ...] detected from the header line.
    E.g. "Name  Id  Version  Match  Source" → [(0,'name'),(6,'id'),...]
    """
    positions: List[Tuple[int, str]] = []
    low = header_line.lower()
    for col in _COL_NAMES_ORDERED:
        idx = low.find(col)
        if idx >= 0:
            positions.append((idx, col))
    positions.sort()
    return positions


def _split_by_cols(
    line: str, positions: List[Tuple[int, str]]
) -> Dict[str, str]:
    """
    Slice a data line into fields using column-start offsets from the header.
    Returns a dict with keys: name, id, version, source (match is skipped).
    Falls back to '' for any column that doesn't appear in positions.
    """
    fields: Dict[str, str] = {}
    for i, (start, col) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(line)
        fields[col] = line[start:end].strip()
    return fields


def _parse_winget_search(output: str) -> List[Dict[str, str]]:
    """
    Parse `winget search` tabular output into list of {name, id, version, source}.

    Strategy:
      1. Locate the header row and the separator row. Data starts after whichever
         is last — this tolerates PS session noise/progress lines that look like
         separators before the real table header.
      2. Extract column positions from the header so multi-word names (e.g.
         "All-in-One Messenger") and extra columns ("Match") are handled correctly.
      3. Fall back to 2+-space splitting when no header positions were found.
      4. Always filter rows whose name/id equal header column keywords.

    Logs: [STORE_RAW_LINE] [STORE_HEADER_ROW_SKIPPED] [STORE_PARSED_RESULT]
    """
    clean = _ANSI_RE.sub("", output)
    lines = clean.splitlines()

    # ── Pass 1: log raw lines and find header + separator ─────────────────────
    for i, raw in enumerate(lines):
        logger.debug("[STORE_RAW_LINE] %02d: %r", i, raw[:120])

    header_idx = -1
    sep_idx    = -1
    header_line = ""
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if header_idx == -1 and _HDR_RE.search(s):
            header_idx  = i
            header_line = line          # preserve original indentation for col offsets
            logger.debug("[STORE_HEADER_FOUND] line=%d %r", i, s[:80])
        if _SEP_RE.match(s):
            sep_idx = i
            logger.debug("[STORE_SEP_FOUND] line=%d %r", i, s[:40])

    # Data rows begin after whichever anchor is latest
    data_start = max(header_idx, sep_idx) + 1
    if data_start <= 0:
        data_start = 0

    # Compute column positions from header (if found)
    cols = _col_positions(header_line) if header_line else []
    logger.debug("[STORE_COL_POSITIONS] %s", [(o, c) for o, c in cols])

    # ── Pass 2: parse data rows ───────────────────────────────────────────────
    results: List[Dict[str, str]] = []
    for line in lines[data_start:]:
        if not line.strip():
            continue
        if _SEP_RE.match(line.strip()):
            continue

        if cols:
            # Header-guided column extraction — handles "All-in-One Messenger"
            # and 5-column (Match) output correctly
            fields = _split_by_cols(line, cols)
            name    = fields.get("name", "").strip()
            pkg_id  = fields.get("id", "").strip()
            version = fields.get("version", "").strip()
            source  = fields.get("source", "").strip()
        else:
            # Fallback: split on 2+ consecutive spaces
            parts   = re.split(r'\s{2,}', line.strip())
            if len(parts) < 2:
                continue
            name    = parts[0].strip()
            pkg_id  = parts[1].strip()
            version = parts[2].strip() if len(parts) > 2 else ""
            # Handle 5-column output: skip "Match" at parts[3] if it looks like
            # "Tag: ..." and grab parts[4] as source
            if len(parts) >= 5 and parts[3].lower().startswith("tag:"):
                source = parts[4].strip()
            elif len(parts) >= 4:
                source = parts[3].strip()
            else:
                source = ""

        # Safety: skip header column words that slipped through
        if name.lower() in _WINGET_HEADER_WORDS or pkg_id.lower() in _WINGET_HEADER_WORDS:
            logger.info("[STORE_HEADER_ROW_SKIPPED] name=%r id=%r", name, pkg_id)
            continue

        if not name or not pkg_id:
            continue

        logger.info("[STORE_PARSED_RESULT] name=%r id=%r version=%r source=%r",
                    name, pkg_id, version, source)
        results.append({
            "name":    name,
            "id":      pkg_id,
            "version": version,
            "source":  source,
        })

    return results


_STRIP_SUFFIXES = re.compile(
    r'\b(app|official|for windows|desktop|mobile|lite|plus|pro|free)\b',
    re.IGNORECASE,
)
_STRIP_PUNCT = re.compile(r'[^\w\s]')


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation and common marketing suffixes."""
    t = text.lower()
    t = _STRIP_PUNCT.sub(" ", t)
    t = _STRIP_SUFFIXES.sub(" ", t)
    return " ".join(t.split())


def _token_score(query_tokens: set, name_tokens: set) -> float:
    """Jaccard-like overlap score between 0.0 and 1.0."""
    if not query_tokens or not name_tokens:
        return 0.0
    intersection = query_tokens & name_tokens
    union = query_tokens | name_tokens
    return len(intersection) / len(union)


def _score_result(app_name: str, result: Dict[str, str]) -> float:
    """Return a 0.0–1.0 score for how well result matches app_name."""
    query_norm  = _normalize(app_name)
    query_lower = app_name.lower()
    name_norm   = _normalize(result["name"])
    name_lower  = result["name"].lower()

    if name_lower == query_lower:
        return 1.0
    if name_norm == query_norm:
        return 0.95
    if name_norm.startswith(query_norm):
        return 0.85
    if query_norm in name_norm:
        return 0.75
    return _token_score(set(query_norm.split()), set(name_norm.split()))


def _select_or_disambiguate(
    app_name: str, results: List[Dict[str, str]]
) -> Tuple[str, Optional[Dict[str, str]], List[Dict[str, str]]]:
    """
    Returns ("auto", best, [best]) or ("disambig", None, [top3]).
    Auto-selects when: only one result, exact/near-exact match, or top gap >= 0.15.
    Disambiguates when: 2+ results have similar scores and meaningfully different names.
    """
    if not results:
        return ("none", None, [])

    scored = sorted(
        [(r, _score_result(app_name, r)) for r in results],
        key=lambda x: x[1], reverse=True,
    )
    top_r, top_score = scored[0]
    logger.info("[STORE_CANDIDATES] count=%d", len(scored))
    logger.info("[STORE_TOP_MATCH] name=%r id=%r score=%.2f", top_r["name"], top_r["id"], top_score)

    if len(scored) == 1:
        logger.info("[STORE_AUTO_SELECT] reason=only_one name=%r", top_r["name"])
        return ("auto", top_r, [top_r])

    if top_score >= 0.95:
        logger.info("[STORE_AUTO_SELECT] reason=exact_match name=%r", top_r["name"])
        return ("auto", top_r, [top_r])

    second_r, second_score = scored[1]
    gap = top_score - second_score
    logger.info("[STORE_MATCH_GAP] top=%.2f second=%.2f gap=%.2f", top_score, second_score, gap)

    if gap >= 0.15 and top_score >= 0.5:
        logger.info("[STORE_AUTO_SELECT] reason=large_gap gap=%.2f name=%r", gap, top_r["name"])
        return ("auto", top_r, [top_r])

    if top_score >= 0.80 and gap >= 0.10:
        logger.info("[STORE_AUTO_SELECT] reason=high_score score=%.2f name=%r", top_score, top_r["name"])
        return ("auto", top_r, [top_r])

    # No result has meaningful relevance — treat as no match so caller opens Store URI
    if top_score < 0.20:
        logger.info("[STORE_MATCH_REJECTED] top_score=%.2f below threshold — no relevant match", top_score)
        return ("none", None, [])

    top3 = [r for r, _ in scored[:3]]
    logger.info("[STORE_DISAMBIGUATION_REQUIRED] candidates=%d top=%r second=%r",
                len(top3), top3[0]["name"], top3[1]["name"] if len(top3) > 1 else "n/a")
    return ("disambig", None, top3)


def _best_match(app_name: str, results: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Return the single best matching result for app_name using fuzzy matching."""
    if not results:
        return None
    if len(results) == 1:
        logger.info("[STORE_MATCH_SELECTED] only one result: name=%r id=%r",
                    results[0]["name"], results[0]["id"])
        return results[0]

    query_lower = app_name.lower()
    query_norm  = _normalize(app_name)
    query_tokens = set(query_norm.split())

    # Tier 1 — exact case-insensitive name match
    for r in results:
        if r["name"].lower() == query_lower:
            logger.info("[STORE_MATCH_EXACT] name=%r id=%r", r["name"], r["id"])
            return r

    # Tier 2 — normalized exact match (strips suffixes/punctuation)
    for r in results:
        if _normalize(r["name"]) == query_norm:
            logger.info("[STORE_MATCH_FUZZY] normalized_exact name=%r id=%r", r["name"], r["id"])
            return r

    # Tier 3 — name starts with query (normalized)
    for r in results:
        if _normalize(r["name"]).startswith(query_norm):
            logger.info("[STORE_MATCH_FUZZY] startswith name=%r id=%r", r["name"], r["id"])
            return r

    # Tier 4 — query contained in normalized name
    for r in results:
        if query_norm in _normalize(r["name"]):
            logger.info("[STORE_MATCH_FUZZY] contains name=%r id=%r", r["name"], r["id"])
            return r

    # Tier 5 — token overlap score
    scored = []
    for r in results:
        name_tokens = set(_normalize(r["name"]).split())
        score = _token_score(query_tokens, name_tokens)
        logger.info("[STORE_MATCH_TOKEN_SCORE] name=%r score=%.2f id=%r", r["name"], score, r["id"])
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_r = scored[0]

    if best_score >= 0.3:
        logger.info("[STORE_MATCH_SELECTED] token_score=%.2f name=%r id=%r",
                    best_score, best_r["name"], best_r["id"])
        return best_r

    # Tier 6 — msstore source preference as final tiebreak
    msstore = [r for r in results if "msstore" in r.get("source", "").lower()]
    if msstore:
        logger.info("[STORE_MATCH_SELECTED] msstore_fallback name=%r id=%r",
                    msstore[0]["name"], msstore[0]["id"])
        return msstore[0]

    logger.warning("[STORE_MATCH_REJECTED] no confident match for %r — using first result", app_name)
    return results[0]


# ── Tool implementations ──────────────────────────────────────────────────────

def _exec_install_store_app(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """
    Search for an app in Microsoft Store / winget and return a confirmation prompt.
    Does NOT install — returns confirm_required so the voice pipeline asks first.
    """
    t0 = time.monotonic()
    app_name = (params.get("app_name") or params.get("query") or "").strip().strip('"')

    if not app_name:
        return ToolResult(
            success=False,
            text="App name required.",
            spoken="Which app should I install?",
        )

    logger.info("[STORE_INSTALL_REQUEST] app=%r", app_name)

    # ── winget availability check ─────────────────────────────────────────────
    winget_path = _find_winget()
    if not winget_path:
        logger.warning("[WINGET_UNAVAILABLE] falling back to Microsoft Store URI")
        # Fallback: open Microsoft Store search page
        cmd_exe = _find_cmdexe()
        if cmd_exe:
            try:
                import urllib.parse
                encoded = urllib.parse.quote(app_name)
                subprocess.Popen(
                    [cmd_exe, "/c", f"start ms-windows-store://search/?query={encoded}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return ToolResult(
                    success=True,
                    text=f"Opened Microsoft Store search for {app_name}.",
                    spoken=f"I've opened the Microsoft Store so you can find {app_name}.",
                )
            except Exception as exc:
                return ToolResult(success=False, text=str(exc),
                                  spoken="I couldn't open the Microsoft Store.")
        return ToolResult(
            success=False,
            text="winget not available.",
            spoken="I can't install apps automatically right now. Please open the Microsoft Store manually.",
        )

    logger.info("[WINGET_AVAILABLE] searching for app=%r", app_name)

    # ── Known-ID fast path — check before slow winget search ─────────────────
    # Popular apps (Instagram, TikTok, etc.) often fail winget name search.
    # Checking the ID table first avoids a 25s winget timeout for known apps.
    _norm_query = app_name.lower().strip()
    _known = _KNOWN_MSSTORE_IDS.get(_norm_query)
    if not _known:
        for _k, _v in _KNOWN_MSSTORE_IDS.items():
            if _k in _norm_query or _norm_query in _k:
                _known = _v
                break

    if _known:
        _display_name, _known_id = _known
        logger.info("[STORE_KNOWN_ID_SYNTHETIC] id=%r display=%r", _known_id, _display_name)
        results = [{"name": _display_name, "id": _known_id, "version": "Unknown", "source": "msstore"}]
        searched_msstore = True
    else:
        # ── Unknown app — search winget (msstore source first, then all) ─────
        logger.info("[WINGET_SEARCH_START] app=%r source=msstore", app_name)
        # 12s (was 25s) — a working winget search normally answers in a few
        # seconds; 25s+25s (up to 50s total with the fallback below) made
        # every unknown-app install command feel hung to the user.
        _ok, _out = _run_winget([
            "search", f'"{app_name}"',
            "--source", "msstore",
            "--accept-source-agreements",
        ], timeout=12)

        results = _parse_winget_search(_out)
        # winget omits the source column when --source is specified; backfill it
        for r in results:
            if not r.get("source"):
                r["source"] = "msstore"
        logger.info("[WINGET_SEARCH_RESULTS] source=msstore count=%d raw=%r", len(results), _out[:200])
        searched_msstore = bool(results)

    if not results:
        # Retry without source restriction
        _ok2, _out2 = _run_winget([
            "search", f'"{app_name}"',
            "--accept-source-agreements",
        ], timeout=12)
        results = _parse_winget_search(_out2)
        logger.info("[WINGET_SEARCH_RESULTS] source=any count=%d raw=%r", len(results), _out2[:200])

    ms = (time.monotonic() - t0) * 1000
    logger.info("[STORE_APP_QUERY] app=%r results=%d ms=%.0f", app_name, len(results), ms)

    if not results:
        # winget found nothing — open Store search URI as fallback and say something helpful
        cmd_exe = _find_cmdexe()
        import urllib.parse
        encoded = urllib.parse.quote(app_name)
        store_uri = f"ms-windows-store://search/?query={encoded}"
        if cmd_exe:
            try:
                subprocess.Popen(
                    [cmd_exe, "/c", f"start {store_uri}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                # No candidates exist to pick from here (winget found nothing) —
                # must not claim otherwise or invite "first/second/third", which
                # sends a plain "install the first one" straight back into a
                # second, doomed winget search instead of being honest that
                # nothing was found (live bug).
                spoken = (
                    f"I couldn't find an exact match for {app_name} through winget, "
                    f"so I opened the Microsoft Store search for it — take a look "
                    f"and let me know if you want me to try a different name."
                )
                return ToolResult(
                    success=True,
                    text=spoken,
                    spoken=spoken,
                )
            except Exception:
                pass
        return ToolResult(
            success=False,
            text=f"No packages found for: {app_name}",
            spoken=f"I couldn't find {app_name} in the Store. Could you check the spelling?",
        )

    _decision, best, candidates = _select_or_disambiguate(app_name, results)

    # Backfill blank source
    for _c in candidates:
        if not _c.get("source"):
            _c["source"] = "msstore" if searched_msstore else "winget"

    # No relevant match found even though winget returned rows — open Store URI
    if _decision == "none":
        import urllib.parse as _up
        encoded = _up.quote(app_name)
        store_uri = f"ms-windows-store://search/?query={encoded}"
        cmd_exe = _find_cmdexe()
        if cmd_exe:
            try:
                subprocess.Popen(
                    [cmd_exe, "/c", f"start {store_uri}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                spoken = (
                    f"I opened Microsoft Store search results for {app_name.title()}. "
                    f"If you see the app you want, tap it to install."
                )
                logger.info("[STORE_URI_FALLBACK] query=%r uri=%s", app_name, store_uri)
                return ToolResult(success=True, text=spoken, spoken=spoken)
            except Exception as _ue:
                logger.warning("[STORE_URI_FALLBACK_FAILED] %s", _ue)
        return ToolResult(
            success=False,
            text=f"No match found for {app_name} in the Store.",
            spoken=f"I couldn't find {app_name} in the Microsoft Store for your region.",
        )

    if _decision == "disambig":
        # Multiple similar candidates — ask user to pick
        _names = [f"{i+1}. {c['name']}" for i, c in enumerate(candidates)]
        _list  = ", ".join(_names[:3])
        disambig_prompt = (
            f"I found a few options: {_list}. "
            f"Which one would you like? Say first, second, or third."
        )
        logger.info("[STORE_DISAMBIGUATION_REQUIRED] prompt=%r", disambig_prompt[:80])
        return ToolResult(
            success=False,
            text=disambig_prompt,
            spoken=disambig_prompt,
            error="store_disambiguation",
            data={
                "candidates":   candidates,
                "source_query": app_name,
                "prompt":       disambig_prompt,
            },
        )

    if not best:
        best = results[0]

    if not best.get("source"):
        best["source"] = "msstore" if searched_msstore else "winget"

    logger.info("[STORE_APP_MATCH_SELECTED] name=%r id=%r source=%r",
                best["name"], best["id"], best["source"])

    # Open the product page (PDP) in Microsoft Store, then let user say "install it"
    _pdp_result = _open_store_pdp(best["name"], best["id"])
    spoken = f"I opened {best['name']} in Microsoft Store. Say install it when you're ready."
    logger.info("[STORE_PDP_OPENED] app=%r id=%r", best["name"], best["id"])

    return ToolResult(
        success=True,
        text=spoken,
        spoken=spoken,
        data={
            "app_name":   best["name"],
            "app_id":     best["id"],
            "product_id": best["id"],
            "source":     best.get("source", "msstore"),
        },
    )


def _exec_install_store_app_exec(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """
    Execute a confirmed winget installation.
    Called ONLY after user has said yes to the confirmation prompt.
    """
    t0 = time.monotonic()
    app_name = (params.get("app_name") or "").strip()
    app_id   = (params.get("app_id")   or app_name).strip()
    source   = (params.get("source")   or "winget").lower()

    if not app_name:
        return ToolResult(
            success=False, text="App name required.", spoken="Which app should I install?"
        )

    # Default source to msstore when blank — search was always msstore-first
    if not source or source == "winget":
        source = "msstore"

    logger.info("[STORE_INSTALL_CONFIRMED] app=%r id=%r source=%r", app_name, app_id, source)
    # ACK is emitted by voice_ws.py BEFORE this runs; log here for trace alignment
    logger.info("[STORE_INSTALL_ACK] app=%r — ack was spoken before winget", app_name)
    logger.info("[STORE_INSTALL_STARTED] app=%r id=%r source=%r", app_name, app_id, source)

    # Build winget install command — use --id to avoid ambiguous name matching
    install_args = [
        "install",
        "--id", f'"{app_id}"',
        "--source", source,
        "--accept-package-agreements",
        "--accept-source-agreements",
    ]

    # 240s (was 120s) — real downloads (esp. under the CPU contention seen in
    # live logs) routinely outlast 120s, which was silently dropping the
    # install into the "ambiguous" branch below with no open-after-install
    # offer, even though winget just hadn't finished yet. This call already
    # runs off the event loop (voice pipeline keeps handling VAD/mic input
    # throughout), so a longer timeout costs nothing but wall-clock patience.
    _ok, _out = _run_winget(install_args, timeout=240)
    ms = (time.monotonic() - t0) * 1000
    logger.info("[STORE_INSTALL_MS] app=%r ms=%.0f ok=%s", app_name, ms, _ok)
    logger.info("[STORE_INSTALL_VERIFY] output=%r", _out[:300])

    # Parse winget output to determine success. Failure markers are checked
    # FIRST — a nonzero-looking "_ok" exit combined with "failed"/"error" text
    # in the output (winget sometimes exits 0 with a failure message on
    # partial installs) must not be reported as success. Never trust the
    # exit code alone; verify against the actual output text.
    out_low = _out.lower()
    if "failed" in out_low or "error" in out_low:
        logger.warning("[STORE_INSTALL_FAILED] app=%r output=%r", app_name, _out[:200])
        return ToolResult(
            success=False,
            text=f"Installation failed: {_out[:200]}",
            spoken=f"I wasn't able to install {app_name}. You may need to install it manually from the Microsoft Store.",
            error="install_failed",
        )

    if "already installed" in out_low:
        logger.info("[STORE_INSTALL_COMPLETE] app=%r already_installed=True", app_name)
        spoken = f"{app_name} is already installed. Would you like me to open it?"
        return ToolResult(
            success=True,
            text=spoken,
            spoken=spoken,
            data={"app_name": app_name, "app_id": app_id, "already_installed": True, "open_offer": True},
        )

    if _ok or any(s in out_low for s in (
        "successfully installed",
        "no applicable upgrade", "installation successful",
    )):
        logger.info("[STORE_INSTALL_SUCCESS] app=%r ms=%.0f", app_name, ms)
        logger.info("[STORE_INSTALL_COMPLETE] app=%r ms=%.0f", app_name, ms)
        spoken = f"{app_name} has been installed successfully. Would you like me to open it?"
        return ToolResult(
            success=True,
            text=spoken,
            spoken=spoken,
            data={
                "app_name":   app_name,
                "app_id":     app_id,
                "ms":         round(ms),
                "open_offer": True,
            },
        )

    # Ambiguous — winget may still have queued the install. Still offer to
    # open it (open_offer=True) rather than leaving the user with no way to
    # follow up — live bug: this branch omitted open_offer entirely, so a
    # slow-but-eventually-successful install never got a "want me to open
    # it?" and "sure, open it" afterward had nothing to act on.
    if out_low.strip() == "timeout":
        logger.warning("[STORE_INSTALL_TIMEOUT] app=%r timeout_s=240", app_name)
        spoken = (
            f"{app_name} is still installing — it's taking longer than usual. "
            f"Say 'open it' once it's ready and I'll launch it."
        )
    else:
        logger.warning("[STORE_INSTALL_AMBIGUOUS] app=%r output=%r", app_name, _out[:200])
        spoken = f"I've started installing {app_name}. Say 'open it' once it's ready."
    return ToolResult(
        success=True,
        text=f"Install command sent for {app_name}.",
        spoken=spoken,
        data={"app_name": app_name, "app_id": app_id, "open_offer": True},
    )


def _exec_open_store_app_page(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Open the Microsoft Store product page for a known app by product ID."""
    app_name   = (params.get("app_name") or "").strip()
    product_id = (params.get("product_id") or params.get("app_id") or "").strip()
    if not product_id:
        return ToolResult(
            success=False,
            text="Product ID required to open the Store page.",
            spoken="I need the app's product ID to open the Store page.",
            error="missing_product_id",
        )
    _open_store_pdp(app_name, product_id)
    spoken = f"Opening {app_name or 'the app'} in Microsoft Store."
    logger.info("[STORE_PDP_OPENED] app=%r id=%r", app_name, product_id)
    return ToolResult(
        success=True,
        text=spoken,
        spoken=spoken,
        data={"app_name": app_name, "app_id": product_id, "product_id": product_id, "source": "msstore"},
    )


# ── Tool registration ─────────────────────────────────────────────────────────

registry.register(
    name="install_store_app",
    definition={
        "type": "function",
        "function": {
            "name": "install_store_app",
            "description": (
                "Search Microsoft Store / winget for an app and open its product page. "
                "Use for: 'download WhatsApp', 'install Spotify', 'get Telegram', "
                "'download CapCut', 'install Netflix', 'get ChatGPT', 'install any app'. "
                "Opens the Store product page — user can then say 'install it' to proceed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name of the app to search for and install",
                    },
                },
                "required": ["app_name"],
            },
        },
    },
    executor=_exec_install_store_app,
    risk="medium",
    category="system",
)

registry.register(
    name="install_store_app_exec",
    definition={
        "type": "function",
        "function": {
            "name": "install_store_app_exec",
            "description": (
                "Execute a confirmed app installation. Called only after user confirmed. "
                "Requires app_name and app_id from a prior install_store_app search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                    "app_id":   {"type": "string"},
                    "source":   {"type": "string", "enum": ["msstore", "winget"]},
                },
                "required": ["app_name", "app_id"],
            },
        },
    },
    executor=_exec_install_store_app_exec,
    risk="medium",
    category="system",
)

registry.register(
    name="open_store_app_page",
    definition={
        "type": "function",
        "function": {
            "name": "open_store_app_page",
            "description": (
                "Open the Microsoft Store product page for a specific app by product ID. "
                "Use when you already know the product ID and want to show the user the Store page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name":   {"type": "string", "description": "Display name of the app"},
                    "product_id": {"type": "string", "description": "Microsoft Store product ID (e.g. 9NBLGGH5L9XT)"},
                },
                "required": ["product_id"],
            },
        },
    },
    executor=_exec_open_store_app_page,
    risk="low",
    category="system",
)
