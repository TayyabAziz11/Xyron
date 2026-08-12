from __future__ import annotations

"""
FrontendEngineer — Phase 4.6 internal CodingAgent role.

Takes a ProductSpec + DesignSpec and generates the complete UI file set
beyond what ProjectPlanner produces.  Specifically adds:
  - Tailwind config with brand theme colours
  - Google Fonts import in index.html
  - CSS custom properties in index.css
  - Additional component files (Navbar, Footer, HeroSection, etc.)

This module ENRICHES what ProjectPlanner already generated — it does NOT
replace or regenerate the existing files unless they are missing.

Log tag: [FRONTEND_ENGINEER]
"""

import asyncio
import json
import logging
from pathlib import Path

from api.services.openai_client import openai_client

logger = logging.getLogger(__name__)

# ── Component prompts ──────────────────────────────────────────────────────────

_COMPONENT_PROMPTS: dict[str, str] = {
    "Navbar": (
        "Generate a responsive React TypeScript Navbar component.\n"
        "- Logo on the left (text or simple SVG icon)\n"
        "- Navigation links in the middle\n"
        "- CTA button (e.g. 'Shop Now', 'Get Started', 'Contact') on the right\n"
        "- Mobile hamburger menu (show/hide toggle with useState)\n"
        "- Use brand CSS variables (var(--color-primary), var(--color-text), etc.) or Tailwind brand-* classes\n"
        "- Sticky, glass-effect background on scroll (use useEffect)\n"
        "- export default Navbar"
    ),
    "Footer": (
        "Generate a responsive React TypeScript Footer component.\n"
        "- Brand logo / name on the left column\n"
        "- 3-4 link columns (Company, Products/Services, Legal, Social)\n"
        "- Newsletter email signup\n"
        "- Copyright notice at the bottom\n"
        "- Use brand CSS variables or Tailwind brand-* classes\n"
        "- export default Footer"
    ),
    "HeroSection": (
        "Generate a visually stunning React TypeScript HeroSection component.\n"
        "- Full-viewport height hero with an attention-grabbing headline\n"
        "- Sub-headline and two CTA buttons (primary + outline)\n"
        "- Background: gradient or full-bleed image placeholder using CSS\n"
        "- Animated entrance (CSS transitions or simple Tailwind animate classes)\n"
        "- export default HeroSection"
    ),
}


class FrontendEngineer:
    """Enrich a generated project with additional UI components."""

    async def enhance(
        self,
        project_path: Path,
        product_spec: dict,
        design_spec: dict,
    ) -> list[str]:
        """Write supplementary component and config files to *project_path*.

        Returns list of written relative paths.
        """
        written: list[str] = []

        # 1. Inject brand theme into tailwind.config.js
        tw_written = await self._patch_tailwind_config(project_path, design_spec)
        if tw_written:
            written.append("tailwind.config.js (patched)")

        # 2. Inject CSS variables into index.css
        css_written = await self._patch_index_css(project_path, design_spec)
        if css_written:
            written.append("src/index.css (patched)")

        # 3. Inject Google Fonts into index.html
        html_written = await self._patch_index_html(project_path, design_spec)
        if html_written:
            written.append("index.html (patched)")

        # 4. Generate extra components (parallel)
        components_dir = project_path / "src" / "components"
        components_dir.mkdir(parents=True, exist_ok=True)

        coros = [
            self._generate_component(name, prompt, components_dir, design_spec, product_spec)
            for name, prompt in _COMPONENT_PROMPTS.items()
            if not (components_dir / f"{name}.tsx").exists()
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        for name, result in zip(_COMPONENT_PROMPTS, results):
            if isinstance(result, str) and result:
                written.append(f"src/components/{name}.tsx")

        logger.info("[FRONTEND_ENGINEER] enhanced=%d files", len(written))
        return written

    # ── Tailwind config patch ──────────────────────────────────────────────────

    async def _patch_tailwind_config(
        self, project_path: Path, design_spec: dict
    ) -> bool:
        tw_config = project_path / "tailwind.config.js"
        if not tw_config.exists():
            return False
        try:
            extend = design_spec.get("tailwind_config", {})
            colors_json = json.dumps(extend.get("colors", {}), indent=4)
            fonts_json  = json.dumps(extend.get("fontFamily", {}), indent=4)

            content = tw_config.read_text("utf-8")
            # Inject before the closing `}` of `theme.extend`
            inject = (
                f"\n        colors: {colors_json},\n"
                f"        fontFamily: {fonts_json},"
            )
            if "extend: {}" in content:
                content = content.replace(
                    "extend: {}",
                    f"extend: {{{inject}\n      }}",
                )
                tw_config.write_text(content, "utf-8")
                logger.debug("[FRONTEND_ENGINEER] tailwind.config.js patched")
                return True
            # Don't double-patch
            if "brand" in content:
                return False
            # Generic injection before last closing brace of extend
            if "extend: {" in content:
                idx = content.rfind("extend: {")
                block_end = content.find("}", idx) + 1
                new_block = content[idx:block_end].rstrip("}")  + inject + "\n    }"
                content = content[:idx] + new_block + content[block_end:]
                tw_config.write_text(content, "utf-8")
                return True
        except Exception as exc:
            logger.debug("[FRONTEND_ENGINEER] tailwind patch skipped: %s", exc)
        return False

    # ── CSS variables patch ────────────────────────────────────────────────────

    async def _patch_index_css(
        self, project_path: Path, design_spec: dict
    ) -> bool:
        css_file = project_path / "src" / "index.css"
        if not css_file.exists():
            return False
        try:
            existing = css_file.read_text("utf-8")
            css_vars = design_spec.get("css_vars", "")
            if not css_vars or ":root" in existing:
                return False
            css_file.write_text(css_vars + "\n\n" + existing, "utf-8")
            logger.debug("[FRONTEND_ENGINEER] CSS variables injected")
            return True
        except Exception as exc:
            logger.debug("[FRONTEND_ENGINEER] CSS patch skipped: %s", exc)
        return False

    # ── Google Fonts injection ─────────────────────────────────────────────────

    async def _patch_index_html(
        self, project_path: Path, design_spec: dict
    ) -> bool:
        html_file = project_path / "index.html"
        if not html_file.exists():
            return False
        fonts_url = design_spec.get("google_fonts_url", "")
        if not fonts_url:
            return False
        try:
            content = html_file.read_text("utf-8")
            if "fonts.googleapis.com" in content:
                return False
            link_tag = f'    <link rel="preconnect" href="https://fonts.googleapis.com">\n    <link href="{fonts_url}" rel="stylesheet">\n'
            content = content.replace("<head>", "<head>\n" + link_tag, 1)
            html_file.write_text(content, "utf-8")
            logger.debug("[FRONTEND_ENGINEER] Google Fonts injected")
            return True
        except Exception as exc:
            logger.debug("[FRONTEND_ENGINEER] HTML patch skipped: %s", exc)
        return False

    # ── Component generation ───────────────────────────────────────────────────

    async def _generate_component(
        self,
        name: str,
        base_prompt: str,
        components_dir: Path,
        design_spec: dict,
        product_spec: dict,
    ) -> str:
        """Generate a single component file and write it to disk."""
        app_type = product_spec.get("app_type", "generic-website")
        palette  = design_spec.get("palette", {})

        system = (
            "You are an expert React/TypeScript UI engineer for a VITE project.\n"
            "Write a complete, self-contained TypeScript React functional component.\n"
            "STRICT RULES — any violation makes the file fail to compile:\n"
            "1. Return ONLY raw TypeScript code. The first character must be 'import' or '//'.\n"
            "2. NO markdown fences (no ```tsx, no ```).\n"
            "3. NO Next.js imports (no next/head, next/image, next/link, next/router).\n"
            "4. Use only: react, react-dom, and Tailwind CSS classes.\n"
            "5. export default ComponentName as the very last line.\n"
            f"- App type: {app_type}\n"
            f"- Primary color: {palette.get('primary', '#3B82F6')}\n"
            f"- Background: {palette.get('background', '#FFFFFF')}\n"
            f"- Text color: {palette.get('text', '#111827')}\n"
            "- Include realistic, domain-appropriate content (no lorem ipsum).\n"
        )
        user = f"{base_prompt}\n\nMake it specific for a {app_type} project."

        try:
            code = openai_client.generate(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                model="gpt-4o-mini",
                max_tokens=1200,
            )
            if not code:
                return ""
            # Robust fence stripping
            import re as _re
            code = code.strip()
            code = _re.sub(r"^```[a-zA-Z]*\n?", "", code)
            code = _re.sub(r"\n?```\s*$", "", code).strip()
            # Reject any Next.js imports (this is a Vite project)
            if "from 'next/" in code or 'from "next/' in code:
                return ""

            out_path = components_dir / f"{name}.tsx"
            out_path.write_text(code, "utf-8")
            logger.debug("[FRONTEND_ENGINEER] wrote component %s", name)
            return code
        except Exception as exc:
            logger.warning("[FRONTEND_ENGINEER] component %s failed: %s", name, exc)
            return ""
