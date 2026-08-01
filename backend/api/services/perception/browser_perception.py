"""
browser_perception.py — Perception Engine: Chrome DevTools Protocol observation.

The most important perception system per the Phase 2 brief — extracts
structured page state via CDP/Playwright instead of screenshots.

SAFETY-CRITICAL DESIGN CONSTRAINT: this module must never trigger a Chrome
connection or launch as a side effect of passive observation.
browser_workspace.get_or_create_page() *launches Chrome if it isn't already
running* (see browser_workspace.py's public API docstring) — that eager-launch
behavior was deliberately removed from the voice-command critical path in an
earlier fix (commit b487803, "remove eager Chrome warmup"). A perception loop
ticking every few seconds must not reintroduce it. Every function here checks
`browser_workspace.is_healthy` first and does nothing at all if Chrome isn't
already connected — this module only ever *reads* an existing session.

Reuses browser_reader.py's page.evaluate() extraction pattern (already used
for article/price/search-result reading) rather than duplicating it, and
adds what didn't exist yet: page-type classification and product extraction.

Logs: [BROWSER_PERCEPTION_SKIP] [BROWSER_PERCEPTION_REFRESH] [BROWSER_PERCEPTION_PRODUCT]
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Page type classification — signals only, no LLM/reasoning ───────────────

_PAGE_TYPE_HOST_PATTERNS: list[tuple[str, str]] = [
    (r"github\.com", "github"),
    (r"(youtube\.com|youtu\.be)", "youtube"),
    (r"(google\.[a-z.]+/search|bing\.com/search|duckduckgo\.com)", "google_search"),
    (r"chat\.openai\.com|chatgpt\.com", "chatgpt"),
    (r"(mail\.google\.com|outlook\.(live|office)\.com|mail\.yahoo\.com)", "email"),
    (r"(chase\.com|bankofamerica\.com|wellsfargo\.com|.*bank.*\.com/.*(login|account))", "banking"),
    (r"(amazon\.[a-z.]+|ebay\.[a-z.]+|etsy\.com|walmart\.com|aliexpress\.com|shopify)", "shopping"),
    (r"(readthedocs\.io|docs\.[a-z0-9-]+\.[a-z]+|developer\.[a-z0-9-]+\.[a-z]+)", "documentation"),
    (r"(news\.|cnn\.com|bbc\.co|nytimes\.com|reuters\.com)", "news"),
    (r"localhost|127\.0\.0\.1", "developer_tools"),
]

_SHOPPING_PATH_HINTS = re.compile(r"/(dp|product|item|p)/", re.IGNORECASE)

# ── GitHub structured extraction (Part 8) — pure URL parsing, no DOM/LLM ────
# GitHub's URL structure already encodes owner/repo/branch/path/page-type
# unambiguously, so this is observation (structural signal), same spirit as
# classify_page_type() above — it never needs to look at rendered content to
# know "this is a pull request" or "this is a file view".
_GITHUB_RESERVED_OWNERS = frozenset({
    "settings", "notifications", "issues", "pulls", "marketplace",
    "explore", "topics", "trending", "collections", "sponsors", "orgs",
    "codespaces", "search", "new",
})

_GITHUB_PAGE_TYPE_BY_SEGMENT: dict[str, str] = {
    "tree": "file_view", "blob": "file_view", "commit": "commit",
    "commits": "commit", "pull": "pull_request", "pulls": "pull_request",
    "issues": "issue", "actions": "actions", "settings": "settings",
}


def classify_github_page(url: str) -> Optional[dict]:
    """
    Parse a github.com URL into {owner, name, branch, current_path,
    page_type}. Returns None for non-repository GitHub pages (the GitHub
    homepage, a user's profile, global search, etc.) — those aren't "a
    repository" and shouldn't be reported as one.
    """
    parsed = urlparse(url)
    if "github.com" not in parsed.netloc.lower():
        return None

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    if owner.lower() in _GITHUB_RESERVED_OWNERS:
        return None

    page_type = "repository_home"
    branch: Optional[str] = None
    current_path: Optional[str] = None

    if len(parts) >= 3:
        segment = parts[2].lower()
        page_type = _GITHUB_PAGE_TYPE_BY_SEGMENT.get(segment, "other")
        if segment in ("tree", "blob") and len(parts) >= 4:
            branch = parts[3]
            current_path = "/".join(parts[4:]) or None
        elif segment == "commits" and len(parts) >= 4:
            branch = parts[3]

    return {
        "owner": owner,
        "name": name,
        "branch": branch,
        "current_path": current_path,
        "page_type": page_type,
    }


def classify_page_type(url: str, title: str = "", has_product_schema: bool = False) -> str:
    """Rule-based page-type classification from structural signals — observation, not reasoning."""
    if has_product_schema:
        return "shopping"
    host = urlparse(url).netloc.lower()
    full = f"{url} {title}".lower()
    for pattern, label in _PAGE_TYPE_HOST_PATTERNS:
        if re.search(pattern, host) or re.search(pattern, full):
            return label
    if _SHOPPING_PATH_HINTS.search(url):
        return "shopping"
    return "unknown"


# ── JS extraction — page.evaluate(), no screenshots ──────────────────────────

_PRODUCT_SCHEMA_JS = """
() => {
    // JSON-LD schema.org/Product — the most reliable universal signal
    // across e-commerce sites, far more stable than CSS selectors.
    const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
    for (const s of scripts) {
        try {
            let data = JSON.parse(s.textContent);
            const items = Array.isArray(data) ? data : (data['@graph'] || [data]);
            for (const item of items) {
                const type = item['@type'];
                const isProduct = type === 'Product' || (Array.isArray(type) && type.includes('Product'));
                if (isProduct) return item;
            }
        } catch (e) { /* malformed JSON-LD — skip */ }
    }
    return null;
}
"""

_SELECTION_JS = "() => (window.getSelection ? window.getSelection().toString() : '')"

_VISIBLE_ERROR_JS = """
() => {
    const errSelectors = ['[class*="error"]', '[role="alert"]', '.alert-danger', '[class*="exception"]'];
    for (const sel of errSelectors) {
        const el = document.querySelector(sel);
        if (el && el.innerText && el.innerText.trim().length > 5 && el.offsetParent !== null) {
            return el.innerText.trim().slice(0, 300);
        }
    }
    return null;
}
"""

# GitHub embeds the repo description in the standard Open Graph meta tag —
# far more reliable than scraping a CSS class that changes with every GitHub
# redesign. Falls back to the generic <meta name="description">.
_GITHUB_DESCRIPTION_JS = """
() => {
    const og = document.querySelector('meta[property="og:description"]');
    if (og && og.content) return og.content.trim().slice(0, 300);
    const md = document.querySelector('meta[name="description"]');
    if (md && md.content) return md.content.trim().slice(0, 300);
    return null;
}
"""


def _extract_product_from_schema(schema: dict) -> dict:
    """Normalize a schema.org Product node into Xyron's product shape."""
    offers = schema.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    aggregate_rating = schema.get("aggregateRating") or {}
    brand = schema.get("brand")
    brand_name = brand.get("name") if isinstance(brand, dict) else brand

    images = schema.get("image")
    if isinstance(images, str):
        images = [images]
    elif not isinstance(images, list):
        images = []

    return {
        "name": schema.get("name"),
        "brand": brand_name,
        "price": offers.get("price"),
        "currency": offers.get("priceCurrency"),
        "availability": (offers.get("availability") or "").split("/")[-1] or None,
        "rating": aggregate_rating.get("ratingValue"),
        "review_count": aggregate_rating.get("reviewCount") or aggregate_rating.get("ratingCount"),
        "category": schema.get("category"),
        "seller": (offers.get("seller") or {}).get("name") if isinstance(offers.get("seller"), dict) else None,
        "sku": schema.get("sku"),
        "images": images[:5],
        "description": (schema.get("description") or "")[:300],
    }


async def _is_workspace_healthy() -> bool:
    """The safety guard — never import/call anything that could launch Chrome."""
    try:
        from api.agents.browser_agent.browser_workspace import browser_workspace
        return browser_workspace.is_healthy
    except Exception:
        return False


async def refresh() -> Optional[dict]:
    """
    Observe the current browser state. Returns None (not an error — just
    "nothing to observe") if Chrome isn't already connected; never attempts
    to connect or launch it.
    """
    if not await _is_workspace_healthy():
        logger.debug("[BROWSER_PERCEPTION_SKIP] reason=no_healthy_connection")
        return None

    try:
        from api.agents.browser_agent.browser_workspace import browser_workspace
        page = await browser_workspace.get_or_create_page()  # reuses cached page — no launch, is_healthy already true

        url = page.url
        title = await page.title()
        tab_count = len(browser_workspace._context.pages) if browser_workspace._context else 1

        schema = None
        try:
            schema = await page.evaluate(_PRODUCT_SCHEMA_JS)
        except Exception:
            pass

        page_type = classify_page_type(url, title, has_product_schema=bool(schema))

        product = None
        if page_type == "shopping":
            if schema:
                product = _extract_product_from_schema(schema)
            else:
                from api.agents.browser_agent.browser_reader import BrowserReader
                price = await BrowserReader().extract_price(page)
                if price:
                    product = {"name": title, "price": price}
            if product:
                logger.info("[BROWSER_PERCEPTION_PRODUCT] name=%s price=%s", product.get("name"), product.get("price"))

        selected_text = ""
        try:
            selected_text = (await page.evaluate(_SELECTION_JS)) or ""
        except Exception:
            pass

        visible_error = None
        try:
            visible_error = await page.evaluate(_VISIBLE_ERROR_JS)
        except Exception:
            pass

        repository = None
        if page_type == "github":
            repository = classify_github_page(url)
            if repository:
                try:
                    desc = await page.evaluate(_GITHUB_DESCRIPTION_JS)
                    if desc:
                        repository["description"] = desc
                except Exception:
                    pass
                logger.info("[BROWSER_PERCEPTION_GITHUB] owner=%s name=%s page_type=%s branch=%s path=%s",
                            repository.get("owner"), repository.get("name"), repository.get("page_type"),
                            repository.get("branch"), repository.get("current_path"))

        logger.debug("[BROWSER_PERCEPTION_REFRESH] url=%s page_type=%s tabs=%d", url, page_type, tab_count)

        return {
            "url": url,
            "title": title,
            "tab_count": tab_count,
            "page_type": page_type,
            "product": product,
            "repository": repository,
            "selected_text": selected_text,
            "visible_error": visible_error,
        }
    except Exception:
        logger.debug("[BROWSER_PERCEPTION] refresh failed", exc_info=True)
        return None
