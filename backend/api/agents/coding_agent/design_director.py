from __future__ import annotations

"""
DesignDirector — internal CodingAgent role.

Converts a raw design brief string into a concrete DesignSpec dict
with exact hex colors, font names, layout system, and CSS custom
property declarations.

Log tag: [DESIGN_DIRECTOR]
"""

import json
import logging
import re
from typing import Any

from api.services.openai_client import openai_client

logger = logging.getLogger(__name__)

# ── Built-in palettes per app type (fallback when LLM unavailable) ─────────────

_DEFAULT_PALETTES: dict[str, dict] = {
    "clothing-ecommerce": {
        "primary":    "#0F0F0F",
        "secondary":  "#F5F5F5",
        "accent":     "#C9A96E",
        "background": "#FFFFFF",
        "surface":    "#FAFAFA",
        "text":       "#111111",
        "text_muted": "#666666",
    },
    "saas-product": {
        "primary":    "#6366F1",
        "secondary":  "#EEF2FF",
        "accent":     "#EC4899",
        "background": "#0F172A",
        "surface":    "#1E293B",
        "text":       "#F8FAFC",
        "text_muted": "#94A3B8",
    },
    "admin-dashboard": {
        "primary":    "#3B82F6",
        "secondary":  "#EFF6FF",
        "accent":     "#10B981",
        "background": "#F1F5F9",
        "surface":    "#FFFFFF",
        "text":       "#1E293B",
        "text_muted": "#64748B",
    },
    "developer-portfolio": {
        "primary":    "#14B8A6",
        "secondary":  "#0F172A",
        "accent":     "#F59E0B",
        "background": "#0A0A0A",
        "surface":    "#111827",
        "text":       "#F9FAFB",
        "text_muted": "#9CA3AF",
    },
    "landing-page": {
        "primary":    "#7C3AED",
        "secondary":  "#F5F3FF",
        "accent":     "#06B6D4",
        "background": "#FFFFFF",
        "surface":    "#F8FAFC",
        "text":       "#111827",
        "text_muted": "#6B7280",
    },
    "restaurant": {
        "primary":    "#92400E",
        "secondary":  "#FEF3C7",
        "accent":     "#D97706",
        "background": "#1C1917",
        "surface":    "#292524",
        "text":       "#FAFAF9",
        "text_muted": "#A8A29E",
    },
    "blog": {
        "primary":    "#1F2937",
        "secondary":  "#F9FAFB",
        "accent":     "#EF4444",
        "background": "#FFFFFF",
        "surface":    "#F3F4F6",
        "text":       "#111827",
        "text_muted": "#6B7280",
    },
    "creative-agency": {
        "primary":    "#000000",
        "secondary":  "#FFFFFF",
        "accent":     "#FF3B00",
        "background": "#0A0A0A",
        "surface":    "#141414",
        "text":       "#FFFFFF",
        "text_muted": "#999999",
    },
    "fitness": {
        "primary":    "#DC2626",
        "secondary":  "#FEF2F2",
        "accent":     "#F59E0B",
        "background": "#111111",
        "surface":    "#1F1F1F",
        "text":       "#FFFFFF",
        "text_muted": "#9CA3AF",
    },
    "generic-website": {
        "primary":    "#3B82F6",
        "secondary":  "#EFF6FF",
        "accent":     "#F59E0B",
        "background": "#FFFFFF",
        "surface":    "#F9FAFB",
        "text":       "#111827",
        "text_muted": "#6B7280",
    },
}

_DEFAULT_TYPOGRAPHY: dict[str, dict] = {
    "clothing-ecommerce": {"heading": "Playfair Display", "body": "Inter",   "mono": "JetBrains Mono"},
    "saas-product":        {"heading": "Plus Jakarta Sans", "body": "Inter",  "mono": "Fira Code"},
    "admin-dashboard":     {"heading": "Inter",             "body": "Inter",  "mono": "JetBrains Mono"},
    "developer-portfolio": {"heading": "Space Grotesk",     "body": "Inter",  "mono": "Fira Code"},
    "landing-page":        {"heading": "Plus Jakarta Sans", "body": "Inter",  "mono": "JetBrains Mono"},
    "restaurant":          {"heading": "Cormorant Garamond","body": "Lato",   "mono": "Courier New"},
    "blog":                {"heading": "Merriweather",      "body": "Source Sans Pro", "mono": "Fira Code"},
    "creative-agency":     {"heading": "Bebas Neue",        "body": "Inter",  "mono": "JetBrains Mono"},
    "fitness":             {"heading": "Montserrat",        "body": "Inter",  "mono": "JetBrains Mono"},
    "generic-website":     {"heading": "Inter",             "body": "Inter",  "mono": "JetBrains Mono"},
}

_DEFAULT_LAYOUT: dict[str, str] = {
    "clothing-ecommerce": "editorial-grid",
    "saas-product":       "asymmetric-hero",
    "admin-dashboard":    "sidebar-main",
    "developer-portfolio":"full-bleed-sections",
    "landing-page":       "centered-sections",
    "restaurant":         "magazine",
    "blog":               "reading-focused",
    "creative-agency":    "bold-fullscreen",
    "fitness":            "bold-dynamic",
    "generic-website":    "classic-sections",
}


class DesignDirector:
    """Turn a design brief into a concrete DesignSpec."""

    async def direct(self, app_type: str, brief: str | None = None) -> dict[str, Any]:
        """Return a DesignSpec dict.

        Uses the brief for LLM color/font extraction; falls back to
        built-in palettes if LLM is unavailable.
        """
        # Built-in defaults (always available)
        palette    = _DEFAULT_PALETTES.get(app_type, _DEFAULT_PALETTES["generic-website"])
        typography = _DEFAULT_TYPOGRAPHY.get(app_type, _DEFAULT_TYPOGRAPHY["generic-website"])
        layout     = _DEFAULT_LAYOUT.get(app_type, "classic-sections")

        # LLM enrichment when a brief was provided
        if brief:
            enriched = await self._enrich_from_brief(brief, app_type, palette, typography)
            if enriched:
                palette    = enriched.get("palette", palette)
                typography = enriched.get("typography", typography)
                layout     = enriched.get("layout", layout)

        # Build CSS variable block
        css_vars = self._build_css_vars(palette, typography)

        spec: dict[str, Any] = {
            "app_type":   app_type,
            "layout":     layout,
            "palette":    palette,
            "typography": typography,
            "css_vars":   css_vars,
            "google_fonts_url": self._google_fonts_url(typography),
            "tailwind_config":  self._tailwind_extend(palette, typography),
        }

        logger.info(
            "[DESIGN_DIRECTOR] app_type=%r layout=%r primary=%r heading_font=%r",
            app_type, layout, palette.get("primary"), typography.get("heading"),
        )
        return spec

    # ── LLM enrichment ────────────────────────────────────────────────────────

    async def _enrich_from_brief(
        self,
        brief: str,
        app_type: str,
        fallback_palette: dict,
        fallback_typo: dict,
    ) -> dict | None:
        """Extract concrete design values from the brief using the LLM."""
        system = (
            "You are a senior UI designer. Extract concrete design values from the brief.\n"
            "Return ONLY valid JSON with keys: palette (object with primary/secondary/accent/"
            "background/surface/text/text_muted as hex strings), "
            "typography (object with heading/body/mono as font family strings), "
            "layout (one of: editorial-grid | asymmetric-hero | sidebar-main | "
            "full-bleed-sections | centered-sections | magazine | bold-fullscreen | classic-sections).\n"
            "If the brief doesn't mention a value, keep the fallback."
        )
        user = (
            f"App type: {app_type}\n\n"
            f"Design brief:\n{brief[:600]}\n\n"
            f"Fallback palette: {json.dumps(fallback_palette)}\n"
            f"Fallback typography: {json.dumps(fallback_typo)}\n\n"
            "Return JSON only."
        )
        try:
            raw = openai_client.generate(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                model="gpt-4o-mini",
                max_tokens=400,
            )
            if not raw:
                return None
            # Strip markdown fences if present
            cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
            return json.loads(cleaned)
        except Exception as exc:
            logger.debug("[DESIGN_DIRECTOR] LLM enrichment failed: %s", exc)
            return None

    # ── CSS generation ────────────────────────────────────────────────────────

    def _build_css_vars(self, palette: dict, typography: dict) -> str:
        lines = [":root {"]
        for key, val in palette.items():
            lines.append(f"  --color-{key.replace('_', '-')}: {val};")
        lines.append(f"  --font-heading: '{typography.get('heading', 'Inter')}', sans-serif;")
        lines.append(f"  --font-body: '{typography.get('body', 'Inter')}', sans-serif;")
        lines.append("}")
        return "\n".join(lines)

    def _google_fonts_url(self, typography: dict) -> str:
        fonts = set()
        for key in ("heading", "body"):
            f = typography.get(key, "Inter")
            if f and f not in ("system-ui", "sans-serif", "serif", "monospace"):
                fonts.add(f.replace(" ", "+"))
        if not fonts:
            return ""
        families = "&family=".join(sorted(fonts))
        return f"https://fonts.googleapis.com/css2?family={families}:wght@300;400;500;600;700;800&display=swap"

    def _tailwind_extend(self, palette: dict, typography: dict) -> dict:
        """Return a Tailwind theme.extend block as a dict."""
        return {
            "colors": {
                "brand": {
                    "primary":    palette.get("primary",    "#3B82F6"),
                    "secondary":  palette.get("secondary",  "#EFF6FF"),
                    "accent":     palette.get("accent",     "#F59E0B"),
                    "bg":         palette.get("background", "#FFFFFF"),
                    "surface":    palette.get("surface",    "#F9FAFB"),
                    "text":       palette.get("text",       "#111827"),
                    "muted":      palette.get("text_muted", "#6B7280"),
                }
            },
            "fontFamily": {
                "heading": [f"'{typography.get('heading', 'Inter')}'", "sans-serif"],
                "body":    [f"'{typography.get('body', 'Inter')}'", "sans-serif"],
            },
        }
