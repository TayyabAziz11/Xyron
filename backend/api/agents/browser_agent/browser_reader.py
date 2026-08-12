"""
BrowserReader — structured content extraction from web pages.

All extraction uses page.evaluate() to run JS in the page context,
keeping network traffic to zero and avoiding flaky CSS-selector churn
caused by anti-scraping obfuscation.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from playwright.async_api import Page

logger = logging.getLogger("api.agents.browser_agent.reader")

# JS that extracts clean article body text, preferring <article> / <main>.
_ARTICLE_JS = """
() => {
    const candidates = [
        document.querySelector('article'),
        document.querySelector('main'),
        document.querySelector('[role="main"]'),
        document.querySelector('.article-body'),
        document.querySelector('.post-content'),
        document.querySelector('.entry-content'),
        document.querySelector('#content'),
        document.body,
    ];
    const el = candidates.find(e => e && e.innerText && e.innerText.trim().length > 200);
    if (!el) return '';
    // Strip scripts/styles inside
    el.querySelectorAll('script,style,nav,footer,aside').forEach(n => n.remove());
    return el.innerText || '';
}
"""

_PRICE_JS = """
() => {
    // Try common price patterns in the DOM text
    const priceRe = /[$€£¥₹]\s?[\d,]+\.?\d{0,2}|[\d,]+\.?\d{0,2}\s?[$€£¥₹]/g;
    const candidates = [
        document.querySelector('[class*="price"]'),
        document.querySelector('[id*="price"]'),
        document.querySelector('[data-price]'),
        document.querySelector('[itemprop="price"]'),
        document.querySelector('.a-price'),    // Amazon
        document.querySelector('.priceToPay'), // Amazon
        document.querySelector('[class*="cost"]'),
        document.querySelector('[class*="amount"]'),
        document.body,
    ];
    for (const el of candidates) {
        if (!el) continue;
        const text = el.innerText || el.getAttribute('data-price') || '';
        const matches = text.match(priceRe);
        if (matches && matches.length > 0) return matches[0];
    }
    return null;
}
"""

_SEARCH_RESULT_JS = """
() => {
    // Works for Google; gracefully degrades on other engines.
    const results = [];
    const items = document.querySelectorAll('div#search div.g, li.b_algo, .result');
    items.forEach((item, idx) => {
        const a = item.querySelector('a');
        const h = item.querySelector('h2, h3');
        const snip = item.querySelector('div[data-sncf], div.VwiC3b, span.st, p');
        if (a && h) {
            results.push({
                position: idx + 1,
                title: (h.innerText || '').trim(),
                url: (a.href || '').trim(),
                snippet: snip ? (snip.innerText || '').trim().slice(0, 300) : '',
            });
        }
    });
    return results;
}
"""


class BrowserReader:
    """Extracts structured content from web pages without screenshots."""

    # ── Article extraction ─────────────────────────────────────────────────────

    async def extract_article(self, page: Page) -> dict:
        """
        Extract article content from the current page.

        Returns: {title, content, url, summary}
        """
        url = page.url
        try:
            title = await page.title()
            raw: str = await page.evaluate(_ARTICLE_JS)
            raw = re.sub(r"\n{3,}", "\n\n", raw)
            raw = re.sub(r"[ \t]+", " ", raw).strip()
            summary = raw[:500] + ("…" if len(raw) > 500 else "")
            logger.info(
                "[BROWSER_PAGE_READ] url=%s chars=%d", url, len(raw)
            )
            return {"title": title, "content": raw, "url": url, "summary": summary}
        except Exception as exc:
            logger.warning("[BROWSER_ARTICLE_ERROR] url=%s error=%r", url, str(exc))
            return {"title": "", "content": "", "url": url, "summary": ""}

    # ── Search results ─────────────────────────────────────────────────────────

    async def extract_search_results(self, page: Page) -> list[dict]:
        """
        Parse search result links from the current page (optimised for Google).

        Returns: [{title, url, snippet, position}]
        """
        try:
            results: list[dict] = await page.evaluate(_SEARCH_RESULT_JS)
            logger.info(
                "[BROWSER_SEARCH_RESULTS_EXTRACTED] url=%s count=%d",
                page.url,
                len(results),
            )
            return results
        except Exception as exc:
            logger.warning("[BROWSER_SEARCH_EXTRACT_ERROR] error=%r", str(exc))
            return []

    # ── Page summarisation ────────────────────────────────────────────────────

    async def summarize_page(self, page: Page, max_chars: int = 2000) -> str:
        """
        Return a clean text summary of the page, stripped of boilerplate,
        truncated to *max_chars*.
        """
        try:
            raw: str = await page.evaluate(_ARTICLE_JS)
            if not raw:
                raw = await page.evaluate("() => document.body.innerText || ''")

            # Cap before regex cleanup, not after — innerText on a heavy
            # page (Google search results, ad-laden articles) can run
            # hundreds of KB, and running IGNORECASE alternation regexes
            # over the full text pegs a CPU core for multiple seconds on
            # the event-loop thread. We only need max_chars in the end,
            # so truncate first.
            if len(raw) > max_chars * 4:
                raw = raw[:max_chars * 4]

            # Strip common boilerplate patterns
            raw = re.sub(
                r"(cookie|privacy policy|terms of service|all rights reserved"
                r"|subscribe to our newsletter|sign up for|follow us on"
                r"|share this article|related articles)[^\n]*\n?",
                "",
                raw,
                flags=re.IGNORECASE,
            )
            raw = re.sub(r"\n{3,}", "\n\n", raw)
            raw = re.sub(r"[ \t]+", " ", raw).strip()

            if len(raw) > max_chars:
                raw = raw[:max_chars] + "…"
            return raw
        except Exception as exc:
            logger.warning("[BROWSER_SUMMARIZE_ERROR] error=%r", str(exc))
            return ""

    # ── Product comparison ────────────────────────────────────────────────────

    async def compare_products(self, pages: list[Page], query: str) -> str:
        """
        Compare products / prices across multiple pages.
        Returns a Markdown table with title, price, and key sentences.
        """
        rows: list[dict] = []
        for page in pages:
            try:
                title = await page.title()
                price = await self.extract_price(page)
                summary = await self.summarize_page(page, max_chars=400)
                # Pull out up to 3 key sentences that mention the query keywords
                kw = query.lower().split()
                key_sentences: list[str] = []
                for sentence in re.split(r"[.!?]\s+", summary):
                    if any(k in sentence.lower() for k in kw):
                        key_sentences.append(sentence.strip())
                        if len(key_sentences) >= 3:
                            break
                rows.append(
                    {
                        "url": page.url,
                        "title": title[:60],
                        "price": price or "N/A",
                        "highlights": " | ".join(key_sentences) or summary[:120],
                    }
                )
            except Exception as exc:
                logger.warning(
                    "[BROWSER_COMPARE_PAGE_ERROR] url=%s error=%r",
                    page.url,
                    str(exc),
                )

        if not rows:
            return f"No comparison data found for: {query}"

        lines = [
            f"## Comparison: {query}\n",
            "| # | Title | Price | Highlights |",
            "|---|-------|-------|-----------|",
        ]
        for i, row in enumerate(rows, 1):
            lines.append(
                f"| {i} | [{row['title']}]({row['url']}) "
                f"| {row['price']} | {row['highlights']} |"
            )
        return "\n".join(lines)

    # ── Price extraction ──────────────────────────────────────────────────────

    async def extract_price(self, page: Page) -> Optional[str]:
        """Locate a price string on the current page. Returns None if not found."""
        try:
            price: Optional[str] = await page.evaluate(_PRICE_JS)
            if price:
                price = price.strip()
                logger.debug(
                    "[BROWSER_PRICE_FOUND] url=%s price=%r", page.url, price
                )
            return price
        except Exception as exc:
            logger.debug("[BROWSER_PRICE_ERROR] error=%r", str(exc))
            return None
