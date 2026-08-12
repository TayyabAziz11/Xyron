from __future__ import annotations

"""
DesignReferenceAgent — Phase 4.6 brand/reference-aware design research.

Detects brand or style references in the user's goal ("like Apple",
"Zara-inspired", "minimalist luxury") and generates a detailed design brief
by querying the LLM with design knowledge.

We intentionally avoid scraping brand websites (unreliable, blocked by
anti-bot measures, copyright risk). Instead we use the LLM's knowledge
of well-known brands and design trends.

Saves:
  - <project_path>/design_brief.json   (structured design spec)
  - <project_path>/design_notes.md     (human-readable notes)

Log tags:
  [DESIGN_RESEARCH_START] [DESIGN_REFERENCE_VISITED]
  [DESIGN_PATTERN_EXTRACTED] [DESIGN_BRIEF_CREATED]
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from api.services.openai_client import openai_client

logger = logging.getLogger(__name__)

# ── Brand / style reference patterns ──────────────────────────────────────────

_BRAND_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\blike\s+apple\b|\bapple[\s-]style\b|\bapple[\s-]inspired\b", re.I), "Apple"),
    (re.compile(r"\blike\s+zara\b|\bzara[\s-]style\b|\bzara[\s-]inspired\b", re.I), "Zara"),
    (re.compile(r"\blike\s+nike\b|\bnike[\s-]style\b|\bnike[\s-]inspired\b", re.I), "Nike"),
    (re.compile(r"\blike\s+gucci\b|\bgucci[\s-]style\b", re.I), "Gucci"),
    (re.compile(r"\blike\s+airbnb\b|\bairbnb[\s-]style\b", re.I), "Airbnb"),
    (re.compile(r"\blike\s+stripe\b|\bstripe[\s-]style\b", re.I), "Stripe"),
    (re.compile(r"\blike\s+linear\b|\blinear[\s-]style\b", re.I), "Linear"),
    (re.compile(r"\blike\s+notion\b|\bnotion[\s-]style\b", re.I), "Notion"),
    (re.compile(r"\blike\s+figma\b|\bfigma[\s-]style\b", re.I), "Figma"),
    (re.compile(r"\blike\s+vercel\b|\bvercel[\s-]style\b", re.I), "Vercel"),
    (re.compile(r"\blike\s+shopify\b|\bshopify[\s-]style\b", re.I), "Shopify"),
    (re.compile(r"\blike\s+hermes\b|\bhermes[\s-]style\b|\bluxury\s+fashion\b", re.I), "Hermès"),
    (re.compile(r"\blike\s+tesla\b|\btesla[\s-]style\b", re.I), "Tesla"),
    (re.compile(r"\blike\s+spotify\b|\bspotify[\s-]style\b", re.I), "Spotify"),
    (re.compile(r"\blike\s+netflix\b|\bnetflix[\s-]style\b", re.I), "Netflix"),
    (re.compile(r"\bluxury\b.*\b(minimal|clean|elegant)\b|\bminimal.*luxury\b", re.I), "luxury minimalist"),
    (re.compile(r"\bbrutalist\b|\bbrutalism\b", re.I), "brutalist"),
    (re.compile(r"\bglass\s*morphism\b|\bglassy\b", re.I), "glassmorphism"),
    (re.compile(r"\bbento\s*grid\b", re.I), "bento grid"),
    (re.compile(r"\bdark\s+mode\b|\bdarker\b|\bdark\s+theme\b", re.I), "dark mode"),
]

_SYSTEM_PROMPT = """\
You are a world-class UI/UX design director with encyclopaedic knowledge of
brand design systems and contemporary web aesthetics.

Given a brand or style reference, produce a detailed, actionable design brief
for a developer who will build a React/Tailwind website INSPIRED by (not
copying) that brand's aesthetic.

Return ONLY valid JSON with this exact structure:
{
  "brand_reference": "brand name or style",
  "hero_style": "description of hero section layout and feeling",
  "navigation_style": "description of nav bar design",
  "layout_pattern": "one of: editorial-grid | asymmetric-hero | centered-sections | full-bleed | sidebar-main | bento-grid",
  "product_card_style": "how cards / content blocks look",
  "spacing": "tight | comfortable | spacious | ultra-spacious",
  "typography": {
    "heading_font": "font family name",
    "body_font": "font family name",
    "heading_weight": "300|400|500|600|700|800",
    "style": "serif | sans-serif | display | geometric"
  },
  "color_palette": {
    "primary": "#hexcode",
    "secondary": "#hexcode",
    "accent": "#hexcode",
    "background": "#hexcode",
    "surface": "#hexcode",
    "text": "#hexcode",
    "text_muted": "#hexcode"
  },
  "cta_style": "description of call-to-action button style",
  "imagery_style": "photography direction | illustration | minimal icons | none",
  "distinctive_elements": ["element 1", "element 2", "element 3"],
  "design_brief": "2-3 paragraph prose description of the complete visual direction",
  "css_class_hints": ["tailwind classes or CSS patterns to achieve the look"]
}
"""


class DesignReferenceAgent:
    """Detect brand/style references and generate design briefs."""

    def detect_reference(self, goal: str) -> Optional[str]:
        """Return detected brand/style name, or None."""
        for pattern, brand in _BRAND_PATTERNS:
            if pattern.search(goal):
                return brand
        return None

    async def research(
        self,
        goal: str,
        project_path: Optional[Path] = None,
    ) -> Optional[dict]:
        """Research design brief for brand/style reference in *goal*.

        Saves design_brief.json and design_notes.md to *project_path* if given.
        Returns the brief dict, or None if no brand reference detected.
        """
        brand = self.detect_reference(goal)
        if not brand:
            return None

        logger.info("[DESIGN_RESEARCH_START] brand=%r goal=%r", brand, goal[:60])

        brief = await self._query_llm(brand, goal)
        if not brief:
            return None

        logger.info("[DESIGN_PATTERN_EXTRACTED] brand=%r palette=%r",
                    brand, brief.get("color_palette", {}).get("primary"))

        # Save to project folder
        if project_path and project_path.exists():
            await self._save_brief(brief, project_path)

        return brief

    async def _query_llm(self, brand: str, goal: str) -> Optional[dict]:
        user = (
            f"Brand/style reference: {brand}\n"
            f"Project goal: {goal}\n\n"
            "Generate a design brief for a website inspired by this aesthetic."
        )
        try:
            import re as _re
            raw = openai_client.generate(
                [{"role": "system", "content": _SYSTEM_PROMPT},
                 {"role": "user",   "content": user}],
                model="gpt-4o-mini",
                max_tokens=900,
            )
            if not raw:
                return None
            cleaned = _re.sub(r"```(?:json)?|```", "", raw).strip()
            return json.loads(cleaned)
        except Exception as exc:
            logger.warning("[DESIGN_REFERENCE_VISITED] LLM call failed: %s", exc)
            return None

    async def _save_brief(self, brief: dict, project_path: Path) -> None:
        """Persist brief as JSON and a human-readable Markdown file."""
        import asyncio
        try:
            # JSON
            json_path = project_path / "design_brief.json"
            json_path.write_text(json.dumps(brief, indent=2), "utf-8")
            logger.info("[DESIGN_BRIEF_CREATED] json=%s", json_path)

            # Markdown
            md_lines = [
                f"# Design Brief: {brief.get('brand_reference', 'Unknown')}",
                "",
                brief.get("design_brief", ""),
                "",
                "## Color Palette",
            ]
            palette = brief.get("color_palette", {})
            for key, val in palette.items():
                md_lines.append(f"- **{key}**: `{val}`")
            md_lines += [
                "",
                "## Typography",
                f"- Heading: {brief.get('typography', {}).get('heading_font', 'Inter')}",
                f"- Body: {brief.get('typography', {}).get('body_font', 'Inter')}",
                "",
                "## Distinctive Elements",
            ]
            for el in brief.get("distinctive_elements", []):
                md_lines.append(f"- {el}")

            md_path = project_path / "design_notes.md"
            md_path.write_text("\n".join(md_lines), "utf-8")
            logger.info("[DESIGN_BRIEF_CREATED] md=%s", md_path)
        except Exception as exc:
            logger.warning("[DESIGN_REFERENCE_VISITED] save failed: %s", exc)
