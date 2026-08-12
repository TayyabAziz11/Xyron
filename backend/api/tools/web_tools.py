"""Web tools — search, YouTube, URL navigation."""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict

from .registry import ToolResult, registry

logger = logging.getLogger(__name__)

_URL_MAP = {
    "youtube":   "https://youtube.com",
    "gmail":     "https://mail.google.com",
    "google":    "https://google.com",
    "github":    "https://github.com",
    "twitter":   "https://twitter.com",
    "x":         "https://x.com",
    "linkedin":  "https://linkedin.com",
    "netflix":   "https://netflix.com",
    "spotify":   "https://open.spotify.com",
    "reddit":    "https://reddit.com",
    "amazon":    "https://amazon.com",
    "chatgpt":   "https://chat.openai.com",
    "instagram": "https://instagram.com",
    "facebook":  "https://facebook.com",
    "notion":    "https://notion.so",
    "figma":     "https://figma.com",
    "vercel":    "https://vercel.com",
}


def _exec_search_web(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    query = params.get("query", "").strip()
    if not query:
        return ToolResult(success=False, text="Query required.", spoken="What would you like me to search for?")
    url = f"https://google.com/search?q={urllib.parse.quote(query)}"
    # Root-cause fix: this only ever returned action_url for the frontend to
    # open via window.open() — but the live desktop app's actual WebSocket
    # hook (useVoiceWS.ts) has no handler for that message field at all, so
    # this tool silently did nothing visible. Launch natively, same as
    # open_system_settings/open_drive/open_application already do — the
    # proven-working pattern in this app, not a new one. action_url is kept
    # for any caller that does read it (harmless either way).
    from api.tools.system_tools import open_url_native
    open_url_native(url)
    return ToolResult(
        success=True,
        text=f"Searching Google for '{query}'",
        spoken=f"Searching Google for {query}.",
        action_url=url,
        data={"query": query, "url": url},
    )


# ── YouTube: real result extraction + playback ──────────────────────────────
# Root-cause fix: this tool used to fire-and-forget `open_url_native()` — an
# OS-level "cmd.exe start <url>" with zero connection to `browser_workspace`
# (the shared CDP-controlled Chrome every other browser tool in this app uses).
# Every call spawned a brand-new tab and only ever left a text search open —
# it never actually played anything. Live bug: "open youtube" → "play a song
# called believer" opened a SECOND tab and just searched, never played the
# song; the next "now play it" opened a THIRD tab. Fixed by reusing the same
# controlled tab (browser_tools._get_page()/browser_workspace) and extracting
# the real result list so a specific request can autoplay immediately.

_YT_EXTRACT_JS = """
() => {
    const els = Array.from(document.querySelectorAll('ytd-video-renderer')).slice(0, 10);
    return els.map(el => {
        const a = el.querySelector('a#video-title');
        const chan = el.querySelector('ytd-channel-name a, #channel-name a, #channel-name');
        return {
            title: a ? (a.getAttribute('title') || a.textContent.trim()) : '',
            url: a ? a.href : '',
            channel: chan ? chan.textContent.trim() : '',
        };
    }).filter(v => v.title && v.url);
}
"""

# Below this fuzzy-match score (rapidfuzz token_set_ratio, 0-100), a "play X"
# request is treated as ambiguous rather than auto-played.
_YT_AUTOPLAY_THRESHOLD = 55


def _youtube_search_and_extract(search_url: str) -> list[dict]:
    """Navigate the shared controlled tab to a YouTube search URL (reusing
    it, never opening a new one) and extract the real video result list."""
    from api.tools.browser_tools import _get_page
    from api.services.main_loop import run_coro_from_thread

    page = _get_page()

    async def _do():
        await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
        try:
            await page.wait_for_selector("ytd-video-renderer", timeout=8000)
        except Exception:
            pass  # no results, or slow render — evaluate() below just returns []
        return await page.evaluate(_YT_EXTRACT_JS)

    return run_coro_from_thread(_do(), timeout=20.0) or []


def youtube_scroll_more(seen_urls: set) -> list[dict]:
    """Scroll the current (already-open) YouTube results tab down and
    return newly-revealed video candidates not already in seen_urls.
    Powers "scroll down" / "show more" on a pending disambiguation list —
    reuses the same tab/page the search already ran on, no new navigation."""
    from api.tools.browser_tools import _get_page
    from api.services.main_loop import run_coro_from_thread

    page = _get_page()

    async def _do():
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 1600)")
            await page.wait_for_timeout(400)
        return await page.evaluate(_YT_EXTRACT_JS)

    all_results = run_coro_from_thread(_do(), timeout=15.0) or []
    return [c for c in all_results if c.get("url") not in seen_urls][:5]


def _youtube_play_url(video_url: str) -> None:
    """Navigate the shared controlled tab directly to a video — reuses the
    same tab the search happened in, so the user never sees a new window."""
    from api.tools.browser_tools import _get_page
    from api.services.main_loop import run_coro_from_thread

    page = _get_page()

    async def _do():
        await page.goto(video_url, wait_until="domcontentloaded", timeout=15000)

    run_coro_from_thread(_do(), timeout=20.0)


# Signals YouTube's own auto-generated official channels use ("Artist -
# Topic" for label-uploaded audio, "...VEVO" for official music videos), and
# phrases official uploads themselves commonly carry — used to break ties in
# favor of the real song over covers/reactions that happen to fuzzy-match
# the title just as well when the query has no artist name in it (e.g. a
# generic "believer" query can't distinguish "Imagine Dragons - Believer" from
# a cover by title-similarity alone).
_OFFICIAL_TITLE_SIGNALS = (
    "official video", "official audio", "official music video", "official lyric video",
)
_OFFICIAL_CHANNEL_SUFFIXES = (" - topic", "vevo")
# Phrases that strongly suggest a derivative/non-official upload — down-rank
# these relative to a same-scoring plain title so "play X" defaults to the
# real song, not someone's cover/reaction/karaoke version of it.
_NON_OFFICIAL_TITLE_SIGNALS = (
    "cover", "tribute", "karaoke", "reaction", "the voice", "originally by",
    "instrumental", "8d audio", "sped up", "slowed", "nightcore", "type beat",
    "acoustic version", "piano version",
)

_OFFICIAL_BONUS = 15.0
_CHANNEL_BONUS = 8.0
_NON_OFFICIAL_PENALTY = 20.0


def _official_adjustment(candidate: dict) -> float:
    title = (candidate.get("title") or "").lower()
    channel = (candidate.get("channel") or "").lower()
    adj = 0.0
    if any(sig in title for sig in _OFFICIAL_TITLE_SIGNALS):
        adj += _OFFICIAL_BONUS
    if any(channel.endswith(sig) for sig in _OFFICIAL_CHANNEL_SUFFIXES):
        adj += _CHANNEL_BONUS
    if any(sig in title for sig in _NON_OFFICIAL_TITLE_SIGNALS):
        adj -= _NON_OFFICIAL_PENALTY
    return adj


def _score_candidates(query: str, candidates: list[dict]) -> tuple[int, float]:
    """Fuzzy-match query against each candidate's title, adjusted for
    official-upload signals (see _official_adjustment) so a cover/reaction
    video doesn't outrank the real song just because its title happens to
    string-match the query slightly better. Returns (best_index, best_score)."""
    from rapidfuzz import fuzz
    best_idx, best_score = 0, -1.0
    for i, c in enumerate(candidates):
        base = fuzz.token_set_ratio(query.lower(), (c.get("title") or "").lower())
        score = base + _official_adjustment(c)
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx, best_score


def _youtube_disambiguation_result(candidates: list[dict], query: str, search_url: str, prefix: str) -> ToolResult:
    top = candidates[:5]
    names = ", ".join(f"{i+1}. {c['title'][:50]}" for i, c in enumerate(top))
    prompt = f"{prefix} {names}. Say first, second, third — or scroll down for more."
    return ToolResult(
        success=False,
        text=prompt,
        spoken=prompt,
        error="youtube_disambiguation",
        data={"candidates": top, "source_query": query, "prompt": prompt, "search_url": search_url},
    )


def _exec_search_youtube(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    query = params.get("query", "").strip()
    if not query:
        return ToolResult(success=False, text="Query required.", spoken="What should I search on YouTube?")
    # "play X" (default) autoplays a confident top match; "search for X" is a
    # literal browse request and always shows results instead.
    intent = (params.get("intent") or "play").strip().lower()
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"

    try:
        candidates = _youtube_search_and_extract(search_url)
    except Exception as exc:
        logger.warning("[SEARCH_YOUTUBE_BROWSER_FAILED] query=%r error=%s", query, exc)
        # Browser control unavailable — fall back to at least opening the
        # search natively rather than failing the command outright.
        from api.tools.system_tools import open_url_native
        open_url_native(search_url)
        return ToolResult(
            success=True,
            text=f"Searching YouTube for '{query}'",
            spoken=f"Searching YouTube for {query}.",
            action_url=search_url,
            data={"query": query, "url": search_url},
        )

    if not candidates:
        return ToolResult(
            success=False,
            text=f"No YouTube results for '{query}'.",
            spoken=f"I couldn't find anything on YouTube for {query}.",
        )

    if intent == "search":
        return _youtube_disambiguation_result(
            candidates, query, search_url, f"Here's what I found for {query}:",
        )

    best_idx, best_score = _score_candidates(query, candidates)
    if best_score < _YT_AUTOPLAY_THRESHOLD:
        return _youtube_disambiguation_result(
            candidates, query, search_url, f"I found a few videos for {query}:",
        )

    chosen = candidates[best_idx]
    try:
        _youtube_play_url(chosen["url"])
    except Exception as exc:
        logger.warning("[YOUTUBE_AUTOPLAY_FAILED] url=%s error=%s", chosen["url"], exc)
        return ToolResult(
            success=False, text=str(exc),
            spoken=f"I found {chosen['title']} but couldn't start playback.",
        )
    logger.info("[YOUTUBE_AUTOPLAY] query=%r chosen=%r score=%.1f", query, chosen["title"][:60], best_score)
    return ToolResult(
        success=True,
        text=f"Playing '{chosen['title']}'",
        spoken=f"Playing {chosen['title']}.",
        action_url=chosen["url"],
        data={"query": query, "url": chosen["url"], "title": chosen["title"], "autoplayed": True},
    )


def _exec_play_youtube_video(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Play a specific, already-known YouTube video — used for ordinal picks
    ("play the 2nd one") and pronoun replays ("play it again") once a video
    or a candidate list is already known, so no new search/tab is needed."""
    url = (params.get("url") or "").strip()
    title = (params.get("title") or "").strip()
    if not url:
        return ToolResult(success=False, text="No video URL.", spoken="I don't have a video to play.")
    try:
        _youtube_play_url(url)
    except Exception as exc:
        logger.warning("[PLAY_YOUTUBE_VIDEO_FAILED] url=%s error=%s", url, exc)
        return ToolResult(success=False, text=str(exc), spoken="I had trouble playing that video.")
    label = title or "the video"
    return ToolResult(
        success=True,
        text=f"Playing '{label}'",
        spoken=f"Playing {label}.",
        action_url=url,
        data={"url": url, "title": label},
    )


def _exec_wiki_summary(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Fetch a Wikipedia summary for a topic — zero hallucination, sub-1s."""
    import urllib.request as _req
    import json as _json
    topic = params.get("topic", "").strip()
    if not topic:
        return ToolResult(success=False, text="Topic required.", spoken="What topic should I look up?")

    encoded = urllib.parse.quote(topic.replace(" ", "_"))
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
    try:
        r = _req.Request(url, headers={"User-Agent": "AIOperator/1.0"})
        with _req.urlopen(r, timeout=5) as resp:
            data = _json.loads(resp.read())
        extract = (data.get("extract") or "").strip()
        if not extract:
            return ToolResult(success=False, text=f"No Wikipedia article found for '{topic}'.",
                              spoken=f"I couldn't find a Wikipedia article for {topic}.")
        # Trim to first 2 sentences for a spoken answer
        sentences = [s.strip() for s in extract.split(". ") if s.strip()]
        spoken_text = ". ".join(sentences[:2]) + "."
        page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
        return ToolResult(
            success=True,
            text=extract[:500],
            spoken=spoken_text,
            action_url=page_url or None,
            data={"topic": topic, "extract": extract, "url": page_url},
        )
    except Exception as exc:
        logger.warning("Wikipedia lookup failed for %s: %s", topic, exc)
        return ToolResult(success=False, text=str(exc),
                          spoken=f"I had trouble looking up {topic} on Wikipedia right now.")


def _exec_open_url(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    site = params.get("site", "").strip().lower()
    url  = _URL_MAP.get(site, site if site.startswith("http") else f"https://{site}")
    label = site.replace("https://", "").replace("http://", "").split("/")[0]
    from api.tools.system_tools import open_url_native
    open_url_native(url)
    return ToolResult(
        success=True,
        text=f"Opening {label}",
        spoken=f"Opening {label}.",
        action_url=url,
        data={"site": site, "url": url},
    )


# ── Register ──────────────────────────────────────────────────────────────────

registry.register(
    name="search_web",
    definition={
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search Google for information on any topic. Use for: 'search for...', 'look up...', 'google...'.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"],
            },
        },
    },
    executor=_exec_search_web,
    risk="low",
    category="web",
)

registry.register(
    name="search_youtube",
    definition={
        "type": "function",
        "function": {
            "name": "search_youtube",
            "description": "Search for or play videos on YouTube. Use for: 'play...', 'search YouTube for...', 'find video of...'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":  {"type": "string", "description": "Video or topic to search"},
                    "intent": {
                        "type": "string",
                        "enum": ["play", "search"],
                        "description": "'play' (default) autoplays a confident top match; 'search' always shows results to choose from.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    executor=_exec_search_youtube,
    risk="low",
    category="web",
)

registry.register(
    name="play_youtube_video",
    definition={
        "type": "function",
        "function": {
            "name": "play_youtube_video",
            "description": "Play a specific, already-known YouTube video by URL. Used for follow-ups like 'play the 2nd one' or 'play it again' once a video or candidate list is already known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url":   {"type": "string", "description": "Full YouTube video URL"},
                    "title": {"type": "string", "description": "Video title, for the spoken confirmation"},
                },
                "required": ["url"],
            },
        },
    },
    executor=_exec_play_youtube_video,
    risk="low",
    category="web",
)

registry.register(
    name="wiki_summary",
    definition={
        "type": "function",
        "function": {
            "name": "wiki_summary",
            "description": "Look up a quick factual summary from Wikipedia. Use for: 'what is X', 'who is X', 'tell me about X', 'define X'. Zero hallucination — real encyclopedia data.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "The topic, person, place, or concept to look up"}},
                "required": ["topic"],
            },
        },
    },
    executor=_exec_wiki_summary,
    risk="low",
    category="web",
)

registry.register(
    name="open_url",
    definition={
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a website in the browser. Use for: 'open YouTube', 'go to GitHub', 'open Gmail'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site name (youtube, github, gmail, twitter, linkedin, netflix, spotify, reddit, amazon, chatgpt) or full URL."}
                },
                "required": ["site"],
            },
        },
    },
    executor=_exec_open_url,
    risk="low",
    category="web",
)
