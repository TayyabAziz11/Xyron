"""
BrowserResearchAgent — autonomous multi-source web research.

Flow:
  1. Extract search query from the goal string
  2. Google search → top 3-5 organic results
  3. Open each result, read article text
  4. Compile findings into a structured Markdown summary with citations
  5. Return the summary string

All network activity is read-only. No forms are filled, no buttons clicked
beyond search box / "Accept cookies".
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from playwright.async_api import BrowserContext

from api.agents.agent_types import AgentTask
from api.agents.browser_agent.browser_navigator import BrowserNavigator
from api.agents.browser_agent.browser_reader import BrowserReader

logger = logging.getLogger("api.agents.browser_agent.research_agent")

# Maximum number of sources to read in full
_MAX_SOURCES = 5
# Maximum characters of raw article text to keep per source before summarising
_MAX_CHARS_PER_SOURCE = 3000
# Minimum chars to consider a page "readable" (filters out empty / JS-only pages)
_MIN_READABLE_CHARS = 200


class BrowserResearchAgent:
    """Orchestrates multi-source research from a natural-language goal."""

    async def research(
        self,
        goal: str,
        context: BrowserContext,
        navigator: BrowserNavigator,
        reader: BrowserReader,
        task: AgentTask,
    ) -> str:
        """
        Research *goal* by searching Google and reading top results.

        Parameters
        ----------
        goal:
            Natural-language research goal.
        context:
            Playwright BrowserContext — new pages are opened on this context.
        navigator:
            Shared BrowserNavigator (already has a search page open, or idle).
        reader:
            Shared BrowserReader for extraction.
        task:
            AgentTask for progress reporting via ws_send_fn.

        Returns
        -------
        str
            Markdown summary with inline citations.
        """
        await self._send_progress(task, f"Researching: {goal}", 5)

        # 1. Extract search query
        query = self._extract_query(goal)
        logger.info("[BROWSER_RESEARCH_START] goal=%r query=%r", goal, query)

        # 2. Google search
        await self._send_progress(task, f"Searching Google for: {query}", 15)
        search_results = await navigator.search_google(query)
        if not search_results:
            return f"I searched for **{query}** but found no usable results."

        # 3. Open top results and read each
        sources: list[dict] = []
        urls_tried = 0
        for result in search_results:
            if len(sources) >= _MAX_SOURCES:
                break
            url = result.get("url", "")
            if not url or not url.startswith("http"):
                continue

            pct = 20 + (urls_tried * 12)
            await self._send_progress(
                task, f"Reading source {urls_tried + 1}: {result.get('title', url)}", pct
            )
            urls_tried += 1

            article = await self._read_source(context, url, reader)
            if article:
                sources.append(article)
            await asyncio.sleep(0.3)

        if not sources:
            # Fall back to snippets from search results
            logger.warning(
                "[BROWSER_RESEARCH_NO_SOURCES] using search snippets as fallback"
            )
            return self._compile_snippet_summary(goal, query, search_results)

        # 4. Compile summary
        await self._send_progress(task, "Compiling findings…", 90)
        summary = self._compile_summary(goal, query, sources)

        logger.info(
            "[BROWSER_RESEARCH_DONE] sources_read=%d summary_chars=%d",
            len(sources),
            len(summary),
        )
        await self._send_progress(task, "Research complete", 100)
        return summary

    # ── Source reading ─────────────────────────────────────────────────────────

    async def _read_source(
        self,
        context: BrowserContext,
        url: str,
        reader: BrowserReader,
    ) -> Optional[dict]:
        """
        Open *url* in a fresh page, extract article text, close the page.
        Returns {title, url, text} or None if the page was unreadable.
        """
        page = None
        try:
            from api.agents.browser_agent.browser_navigator import _open_in_real_chrome
            _open_in_real_chrome(url)  # mirror to the visible Windows Chrome too
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            await asyncio.sleep(0.5)
            logger.info("[BROWSER_PAGE_OPENED] url=%s", url)

            article = await reader.extract_article(page)
            text = article.get("content", "")

            if len(text.strip()) < _MIN_READABLE_CHARS:
                logger.debug(
                    "[BROWSER_SOURCE_SKIP] url=%s reason=too_short chars=%d",
                    url,
                    len(text),
                )
                return None

            # Truncate to limit
            if len(text) > _MAX_CHARS_PER_SOURCE:
                text = text[:_MAX_CHARS_PER_SOURCE] + "…"

            return {
                "title": article.get("title") or url,
                "url": url,
                "text": text,
            }
        except Exception as exc:
            logger.warning(
                "[BROWSER_SOURCE_ERROR] url=%s error=%r", url, str(exc)
            )
            return None
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    # ── Summary compilation ────────────────────────────────────────────────────

    def _compile_summary(self, goal: str, query: str, sources: list[dict]) -> str:
        """Build a Markdown summary from the collected source texts."""
        lines: list[str] = [
            f"## Research: {goal}\n",
            f"*Query used:* `{query}`  \n",
            f"*Sources read:* {len(sources)}\n",
            "---\n",
            "### Key Findings\n",
        ]

        all_text = "\n\n".join(s["text"] for s in sources)

        # Extract the first coherent paragraph from each source as a finding
        for i, source in enumerate(sources, 1):
            paras = [
                p.strip()
                for p in re.split(r"\n\n+", source["text"])
                if len(p.strip()) > 80
            ]
            excerpt = paras[0][:600] if paras else source["text"][:300]
            lines.append(f"**{i}. {source['title'][:70]}**")
            lines.append(f"> {excerpt}\n")

        lines += [
            "---\n",
            "### Sources\n",
        ]
        for i, source in enumerate(sources, 1):
            lines.append(f"{i}. [{source['title'][:60]}]({source['url']})")

        return "\n".join(lines)

    def _compile_snippet_summary(
        self, goal: str, query: str, search_results: list[dict]
    ) -> str:
        """Fallback summary using only Google search snippets."""
        lines = [
            f"## Research: {goal}\n",
            f"*Note: Could not open source pages — using search snippets.*\n",
            "### Snippets\n",
        ]
        for i, r in enumerate(search_results[:5], 1):
            snippet = r.get("snippet", "")
            lines.append(f"**{i}. [{r.get('title', '')}]({r.get('url', '')})**")
            if snippet:
                lines.append(f"> {snippet}\n")
        return "\n".join(lines)

    # ── Query extraction ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_query(goal: str) -> str:
        """
        Strip common research-intent prefixes and extract the core search query.

        Examples
        --------
        "research the best Python async frameworks" → "best Python async frameworks"
        "find information about climate change 2025" → "climate change 2025"
        "summarize what is quantum computing" → "quantum computing"
        """
        prefixes = re.compile(
            r"^(?:research|find information about|summarize|look up|"
            r"tell me about|what is|search for|find out about)\s+",
            re.IGNORECASE,
        )
        return prefixes.sub("", goal.strip()).strip()

    # ── Progress helper ────────────────────────────────────────────────────────

    async def _send_progress(
        self, task: AgentTask, message: str, pct: int
    ) -> None:
        task.progress_pct = pct
        if task.ws_send_fn is not None:
            try:
                await task.ws_send_fn(
                    {
                        "type": "progress",
                        "task_id": task.task_id,
                        "message": message,
                        "progress_pct": pct,
                    }
                )
            except Exception:
                pass
