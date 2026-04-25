"""
Command Splitter — splits compound voice commands into individual commands.

Examples:
    "open Chrome and set volume to 50"  → ["open Chrome", "set volume to 50"]
    "take a screenshot and play music"  → ["take a screenshot", "play music"]
    "open my IT course folder"          → ["open my IT course folder"]
    "what is my ip"                     → ["what is my ip"]
    "open chrome and firefox"           → ["open chrome", "open firefox"]

Rules:
    - Splits on conjunctions/transitions only when both sides look like commands.
    - A valid command clause must have ≥4 words AND contain a verb-like word.
    - If a part lacks a verb it's merged into the preceding part.
    - At most 4 commands; if more splits found the original text is returned unsplit.
    - No external dependencies — regex only.
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# ── Verb list ────────────────────────────────────────────────────────────────
# Words that make a clause look like an actionable command.
_VERBS: frozenset[str] = frozenset({
    "open", "play", "set", "take", "increase", "decrease", "show", "close",
    "search", "go", "find", "create", "delete", "send", "check", "read",
    "get", "mute", "unmute", "launch", "start", "stop", "run", "pause",
    "resume", "restart", "turn", "switch", "change", "move", "copy", "paste",
    "download", "upload", "save", "print", "rename", "refresh", "reload",
    "scroll", "click", "type", "write", "enter", "press", "maximize",
    "minimize", "lock", "unlock", "shutdown", "reboot", "sleep", "wake",
    "install", "uninstall", "update", "upgrade", "connect", "disconnect",
    "enable", "disable", "toggle", "adjust", "raise", "lower", "mute",
    "record", "capture", "screenshot", "list", "show", "display", "hide",
})

# Minimum words required for a clause to be treated as a standalone command.
_MIN_WORDS: int = 4

# Maximum number of commands we'll return; more implies a mis-split.
_MAX_COMMANDS: int = 4

# ── Splitter tokens (ordered longest-first so "and then" wins over "and") ────
_SPLIT_TOKENS: list[str] = [
    "and then", "and also", "after that", "and", "then", "also", "plus",
]

# Pre-compiled pattern: one of the split tokens surrounded by word boundaries,
# allowing optional punctuation/whitespace around it.
_SPLIT_RE: re.Pattern[str] = re.compile(
    r"\s*\b(?:" + "|".join(re.escape(t) for t in _SPLIT_TOKENS) + r")\b\s*",
    re.IGNORECASE,
)

# Quoted / parenthesised regions that should never be split inside.
_QUOTED_RE: re.Pattern[str] = re.compile(r'"[^"]*"|\'[^\']*\'|\([^)]*\)')

# ── App-list expansion ────────────────────────────────────────────────────────
# Detect "open X and Y and Z" patterns and expand to ["open X", "open Y", …].
# The heuristic: the leading verb is followed by a list of short (1-3 word)
# noun-only tokens joined by "and".
_APP_LIST_RE: re.Pattern[str] = re.compile(
    r"^(?P<verb>open|launch|start|close|play)\s+(?P<apps>.+)$",
    re.IGNORECASE,
)
# A single app-name token: 1-3 words, none of which are known verbs.
_APP_TOKEN_RE: re.Pattern[str] = re.compile(
    r"^(?:\w+(?:\s\w+){0,2})$"
)


def _has_verb(text: str) -> bool:
    """Return True if *text* contains at least one recognised action verb."""
    words = re.findall(r"\b\w+\b", text.lower())
    return any(w in _VERBS for w in words)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _protect_quotes(text: str) -> tuple[str, list[str]]:
    """Replace quoted/parenthesised substrings with placeholders."""
    placeholders: list[str] = []

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        placeholders.append(m.group(0))
        return f"\x00{len(placeholders) - 1}\x00"

    protected = _QUOTED_RE.sub(_replace, text)
    return protected, placeholders


def _restore_quotes(text: str, placeholders: list[str]) -> str:
    for i, ph in enumerate(placeholders):
        text = text.replace(f"\x00{i}\x00", ph)
    return text


def _try_app_list_expansion(text: str) -> list[str] | None:
    """
    If the text looks like "<verb> app1 and app2 and app3", return
    ["<verb> app1", "<verb> app2", "<verb> app3"].  Returns None otherwise.
    """
    m = _APP_LIST_RE.match(text.strip())
    if not m:
        return None

    verb = m.group("verb")
    apps_raw = m.group("apps")

    # Split candidate app list on "and" only (no other conjunctions).
    candidates = re.split(r"\band\b", apps_raw, flags=re.IGNORECASE)
    candidates = [c.strip() for c in candidates if c.strip()]

    if len(candidates) < 2:
        return None

    # Every candidate must:
    #   1. be 1-3 words
    #   2. not contain a verb (otherwise it's a full sub-command, not an app name)
    for cand in candidates:
        if not _APP_TOKEN_RE.match(cand):
            return None
        if _has_verb(cand):
            # A candidate contains a verb → this isn't a simple app list.
            return None

    # Check we won't exceed the max command limit.
    if len(candidates) > _MAX_COMMANDS:
        return None

    return [f"{verb} {app}" for app in candidates]


def _raw_split(text: str) -> list[str]:
    """
    Split *text* on conjunction tokens, skipping anything inside quotes.
    Returns the raw list of segments (may include fragments).
    """
    protected, placeholders = _protect_quotes(text)
    parts = _SPLIT_RE.split(protected)
    parts = [_restore_quotes(p.strip(), placeholders) for p in parts if p.strip()]
    return parts


def _is_fragment(part: str) -> bool:
    """
    Return True if *part* should be merged into a neighbour rather than treated
    as a standalone command.

    A part is a fragment when it either:
      - contains no recognised action verb  (strongest signal), OR
      - is very short (< _MIN_WORDS words) AND has no verb.

    Importantly, a short clause that *does* contain a verb (e.g. "open Chrome",
    "play music") is NOT a fragment — it is a valid terse command.
    """
    has_v = _has_verb(part)
    wc = _word_count(part)

    if not has_v:
        # No verb → definitely a noun-phrase tail, not a command.
        return True
    if wc < _MIN_WORDS and not has_v:
        # Redundant given the above, but kept for clarity.
        return True
    return False


def _merge_fragments(parts: list[str]) -> list[str]:
    """
    Merge any fragment part into the preceding part.
    If the very first part is a fragment, merge it into the following one.
    """
    if not parts:
        return parts

    merged: list[str] = []
    for part in parts:
        if merged and _is_fragment(part):
            merged[-1] = merged[-1] + " " + part
        else:
            merged.append(part)

    # If the first item is still a fragment, push it forward.
    if len(merged) >= 2 and _is_fragment(merged[0]):
        merged[1] = merged[0] + " " + merged[1]
        merged = merged[1:]

    return merged


def split(text: str) -> list[str]:
    """
    Split a compound voice command into individual command strings.

    Parameters
    ----------
    text:
        Raw user utterance.

    Returns
    -------
    list[str]
        A list of 1-to-4 individual command strings.  Returns ``[text]`` when
        the input cannot be cleanly split into 2 or more valid commands.
    """
    text = text.strip()
    if not text:
        return [text]

    # ── 1. Quick exit: no conjunction token present at all ───────────────────
    if not _SPLIT_RE.search(text):
        return [text]

    # ── 2. Try app-list expansion first ─────────────────────────────────────
    expanded = _try_app_list_expansion(text)
    if expanded and 2 <= len(expanded) <= _MAX_COMMANDS:
        logger.debug("command_splitter: app-list expansion → %s", expanded)
        return expanded

    # ── 3. General split on conjunction tokens ───────────────────────────────
    raw_parts = _raw_split(text)

    if len(raw_parts) < 2:
        # No real split happened.
        return [text]

    # ── 4. Merge fragments into neighbours ──────────────────────────────────
    merged = _merge_fragments(raw_parts)

    # ── 5. Validate: every part must have a verb (word minimum is secondary) ──
    valid = all(_has_verb(p) for p in merged)

    if not valid or len(merged) < 2:
        return [text]

    # ── 6. Guard against over-splitting ─────────────────────────────────────
    if len(merged) > _MAX_COMMANDS:
        logger.debug(
            "command_splitter: %d parts exceeds max %d, returning original",
            len(merged), _MAX_COMMANDS,
        )
        return [text]

    logger.debug("command_splitter: %r → %s", text, merged)
    return merged
