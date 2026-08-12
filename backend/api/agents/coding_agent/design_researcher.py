from __future__ import annotations

"""
DesignResearcher — query the LLM for current design trends before code generation.

Returns a plain-English design brief that the ProjectPlanner embeds into its
system prompt so the generated UI follows contemporary patterns for the
requested project type.

This is a fast LLM call (no browser, no I/O beyond the API) — typically
completes in 1-3 seconds.
"""

import logging
import re
from typing import Optional

from api.services.openai_client import openai_client

logger = logging.getLogger(__name__)

# Categories whose design trends we research proactively.
_DESIGN_CATEGORIES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(clothing|fashion|apparel|outfit|wear|boutique|store)\b", re.I), "clothing e-commerce"),
    (re.compile(r"\b(portfolio|personal\s+site|my\s+site|personal\s+portfolio)\b", re.I), "developer portfolio"),
    (re.compile(r"\b(dashboard|admin\s+panel|admin|analytics\s+dashboard)\b", re.I), "admin dashboard"),
    (re.compile(r"\b(landing\s+page|coming\s+soon|product\s+launch)\b", re.I), "SaaS landing page"),
    (re.compile(r"\b(saas|software\s+as\s+a\s+service|subscription|platform)\b", re.I), "SaaS product"),
    (re.compile(r"\b(blog|news|magazine|article|editorial)\b", re.I), "editorial blog"),
    (re.compile(r"\b(restaurant|food|cafe|menu|delivery)\b", re.I), "restaurant / food delivery"),
    (re.compile(r"\b(ecommerce|e-commerce|shop|marketplace|product\s+listing)\b", re.I), "e-commerce"),
    (re.compile(r"\b(agency|creative\s+agency|design\s+agency|marketing\s+agency)\b", re.I), "creative agency"),
    (re.compile(r"\b(fitness|gym|workout|health|wellness)\b", re.I), "fitness / wellness"),
]

_SYSTEM_PROMPT = """\
You are a senior UI/UX designer with deep knowledge of 2024-2025 design trends.
Given a project type, return a concise design brief (150-200 words) covering:
- Color palette (primary, secondary, accent with hex codes)
- Typography (heading font, body font — specific names)
- Layout style (grid, asymmetric, minimal, bento, etc.)
- Key visual elements (glassmorphism, gradients, illustrations, photos, icons)
- Mood / tone
- 2-3 specific modern patterns used by leading sites in this category

Return ONLY the design brief. No headers, no markdown, no explanation.
"""


class DesignResearcher:
    """Query the LLM for design trends appropriate to the project type."""

    def detect_category(self, goal: str) -> Optional[str]:
        """Detect the design category from the goal string, or None."""
        for pattern, category in _DESIGN_CATEGORIES:
            if pattern.search(goal):
                return category
        return None

    async def research(self, goal: str) -> Optional[str]:
        """Return a design brief string, or None if LLM is unavailable or the
        goal doesn't match a known design category."""
        category = self.detect_category(goal)
        if not category:
            logger.debug("[DESIGN_RESEARCHER] no design category matched for goal=%r", goal[:60])
            return None

        logger.info("[DESIGN_RESEARCHER] researching design trends for category=%r", category)

        user_msg = f"Project type: {category}\n\nProvide a modern design brief for this project."

        try:
            brief = openai_client.generate(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                model="gpt-4o-mini",
                max_tokens=300,
            )
            if brief:
                logger.info("[DESIGN_RESEARCHER] design brief received (%d chars)", len(brief))
                return brief.strip()
        except Exception as exc:
            logger.warning("[DESIGN_RESEARCHER] LLM call failed: %s", exc)

        return None
