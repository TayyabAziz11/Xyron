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
    return ToolResult(
        success=True,
        text=f"Searching Google for '{query}'",
        spoken=f"Searching Google for {query}.",
        action_url=url,
        data={"query": query, "url": url},
    )


def _exec_search_youtube(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    query = params.get("query", "").strip()
    if not query:
        return ToolResult(success=False, text="Query required.", spoken="What should I search on YouTube?")
    url = f"https://youtube.com/search?q={urllib.parse.quote(query)}"
    return ToolResult(
        success=True,
        text=f"Searching YouTube for '{query}'",
        spoken=f"Searching YouTube for {query}.",
        action_url=url,
        data={"query": query, "url": url},
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
                "properties": {"query": {"type": "string", "description": "Video or topic to search"}},
                "required": ["query"],
            },
        },
    },
    executor=_exec_search_youtube,
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
