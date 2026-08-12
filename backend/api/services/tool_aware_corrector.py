"""
Tool-Aware Correction Engine — Phase 2.3

After the entity corrector runs, predicts which tool the top candidate would
invoke and then adjusts the entity matching threshold to prefer entities
relevant to that tool.

  tool=open_application  → prefer installed app names
  tool=open_folder       → prefer folder/directory names
  tool=install_store_app → prefer Microsoft Store app names
  tool=search_youtube    → prefer media titles / song names
  tool=open_url          → prefer URL / website patterns
  tool=search_web        → prefer search query patterns

Log markers:
  [TOOL_CORRECTOR] tool=<predicted> text=<text> entity_type=<type> score=<score>
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Quick pattern-based tool predictor ───────────────────────────────────────
# Lightweight — runs in <1ms. NOT the full intent router.

_TOOL_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # Folder navigation — MUST precede the generic open_application catch-all
    # below. The generic pattern is a bare "open <word>" match, so it used to
    # win on every "open X folder" phrase before this one ever got a chance
    # (confirmed root cause of "open perfume folder" predicting
    # tool=open_application, Phase 3.5 proven issue 1). Order here mirrors
    # intent_router.py, which already puts folder/drive patterns ahead of
    # its own open_application catch-all for the same reason.
    (re.compile(r'\bopen\s+(?:the\s+)?[\w\s]+\s+(?:folder|directory)\b', re.I),   "open_folder",       "folder"),
    (re.compile(r'\b(?:navigate|go)\s+to\s+\w', re.I),                            "open_folder",       "folder"),
    (re.compile(r'\bopen\s+(?:my\s+)?(?:documents?|downloads?|desktop|projects?|hackathon|work)', re.I),
                                                                                   "open_folder",       "folder"),
    # install/download → install_store_app
    (re.compile(r'\b(?:install|download|get)\s+(?:the\s+)?(?!\bfolder\b|file)\w', re.I), "install_store_app", "store_app"),
    # open_application — specific known apps first (precise, low false-positive risk)
    (re.compile(r'\bopen\s+(chrome|firefox|edge|notepad|calculator|discord|spotify|teams|zoom|slack|steam|vs\s*code|whatsapp|telegram)\b', re.I),
                                                                                   "open_application",  "app"),
    # YouTube / media
    (re.compile(r'\b(?:play|watch)\s+(?:(?!on\s+youtube)\S+\s+)*on\s+(?:youtube|yt|spotify)\b', re.I),
                                                                                   "search_youtube",    "media"),
    (re.compile(r'\byoutube\b.*\b(?:chalao|karo|play|watch)\b', re.I),            "search_youtube",    "media"),
    (re.compile(r'\bplay\s+\w', re.I),                                             "search_youtube",    "media"),
    # Web search
    (re.compile(r'\b(?:search\s+(?:for|about)|google|find\s+(?:me\s+)?info)', re.I), "search_web",     "search_query"),
    # URL
    (re.compile(r'\bhttps?://|\b(?:go\s+to|open)\s+\w+\.(?:com|net|org|io)\b', re.I), "open_url",     "url"),
    # Screenshot
    (re.compile(r'\b(?:take|capture)\s+(?:a\s+)?screenshot\b', re.I),             "take_screenshot",   None),
    # Volume
    (re.compile(r'\bvolume\s+(?:up|down|to\s+\d)', re.I),                         "volume_control",    None),
    (re.compile(r'\b(?:mute|unmute)\b', re.I),                                     "volume_control",    None),
    # open_application — generic catch-all, LAST: only reached once every more
    # specific shape above (folder/install/known-app/media/search/url) has
    # failed to match.
    (re.compile(r'\b(?:open|launch|start|run|switch\s+to)\s+\w', re.I),          "open_application",  "app"),
]


def _predict_tool(text: str) -> tuple[str, str | None]:
    """
    Quick pattern-based tool prediction.

    Returns:
        (tool_name, preferred_entity_type | None)
    """
    tl = text.lower()
    for pat, tool, etype in _TOOL_PATTERNS:
        if pat.search(tl):
            return tool, etype
    return "unknown", None


# ── Entity type → sub-database mapping ────────────────────────────────────────

_TYPE_BOOSTS: dict[str, float] = {
    "app":          0.12,
    "installed_app": 0.12,
    "store_app":    0.10,
    "folder":       0.10,
    "file":         0.08,
    "media":        0.10,
    "search_query": 0.05,
    "url":          0.05,
    "window":       0.06,
}


def rescore(
    candidates: list,    # list[TranscriptCandidate]
    session_state: Optional[dict] = None,
) -> list:               # list[TranscriptCandidate]
    """
    Tool-aware candidate rescoring.

    For each candidate:
      1. Predict tool from text
      2. Look up preferred entity type for that tool
      3. Check ContextStack for matching entity of that type
      4. Boost confidence proportionally

    Returns re-sorted list.
    """
    if not candidates:
        return candidates

    import copy
    try:
        from api.services.context_stack import context_stack as _cs
        ctx_recent = _cs.recent(10)
    except Exception:
        ctx_recent = []

    updated = []
    for cand in candidates:
        tool, preferred_etype = _predict_tool(cand.text)
        boost = 0.0

        if preferred_etype:
            # Check if context stack has a recent entity of this type
            for ent in ctx_recent:
                if ent.type == preferred_etype:
                    boost = _TYPE_BOOSTS.get(preferred_etype, 0.05)
                    break

        logger.info(
            "[TOOL_CORRECTOR] text=%r predicted_tool=%s preferred_etype=%s ctx_boost=%.2f",
            cand.text[:50], tool, preferred_etype or "none", boost,
        )

        if boost > 0.0:
            new_cand            = copy.copy(cand)
            new_cand.confidence = min(1.0, cand.confidence + boost)
            updated.append(new_cand)
        else:
            updated.append(cand)

    updated.sort(key=lambda c: c.confidence, reverse=True)
    return updated
