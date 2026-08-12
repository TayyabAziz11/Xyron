from __future__ import annotations

"""
BackendEngineer — Phase 4.6 internal CodingAgent role.

Determines whether the project needs a backend and generates:
  - mock_data.json (realistic data embedded in the frontend)
  - Optionally a simple Flask/Express backend for projects with forms

For 95% of generated websites (marketing pages, e-commerce UIs, dashboards)
no runtime backend is needed — mock data files suffice.

Log tag: [BACKEND_ENGINEER]
"""

import json
import logging
from pathlib import Path
from typing import Any

from api.services.openai_client import openai_client

logger = logging.getLogger(__name__)

# App types that are purely frontend (no backend needed)
_FRONTEND_ONLY = {
    "clothing-ecommerce", "developer-portfolio", "landing-page",
    "blog", "creative-agency", "fitness", "travel", "music",
    "mobile-app-landing", "generic-website",
}

# App types that benefit from rich mock data
_NEEDS_MOCK_DATA = {
    "clothing-ecommerce": "fashion product catalog",
    "admin-dashboard":    "analytics metrics and table data",
    "ecommerce":          "product catalog",
    "restaurant":         "menu items with descriptions and prices",
    "blog":               "blog post list with authors and dates",
    "real-estate":        "property listings with specs and prices",
    "fitness":            "class schedule and trainer profiles",
    "travel":             "destination cards and tour packages",
    "education":          "course catalog with instructors",
}


class BackendEngineer:
    """Decide if a backend is needed and generate mock data if not."""

    def needs_backend(self, app_type: str) -> bool:
        """Return True if the app type warrants a real backend."""
        return app_type not in _FRONTEND_ONLY

    async def generate_mock_data(
        self, project_path: Path, product_spec: dict, design_spec: dict
    ) -> list[str]:
        """Generate mock data JSON files and write them to the project.

        Returns list of written relative paths.
        """
        app_type = product_spec.get("app_type", "generic-website")
        data_category = _NEEDS_MOCK_DATA.get(app_type)

        if not data_category:
            return []

        logger.info("[BACKEND_ENGINEER] generating mock data for %r", app_type)

        # Generate mock data via LLM
        mock_data = await self._generate_data(app_type, data_category, product_spec)
        if not mock_data:
            return []

        # Write to src/data/
        data_dir = project_path / "src" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        written = []
        for filename, content in mock_data.items():
            file_path = data_dir / filename
            try:
                file_path.write_text(json.dumps(content, indent=2), "utf-8")
                written.append(f"src/data/{filename}")
                logger.debug("[BACKEND_ENGINEER] wrote %s", filename)
            except Exception as exc:
                logger.warning("[BACKEND_ENGINEER] write failed for %s: %s", filename, exc)

        return written

    async def _generate_data(
        self, app_type: str, category: str, product_spec: dict
    ) -> dict[str, Any]:
        """Ask LLM to generate realistic mock data and return as dict of {filename: data}."""
        system = (
            "You are a backend engineer creating realistic mock data for a frontend project.\n"
            "Return ONLY valid JSON (no markdown) with this structure:\n"
            "{ \"filename.json\": [array of 6-8 realistic data objects] }\n"
            "Each object should have 5-8 fields with realistic values.\n"
            "No placeholder text like 'Lorem ipsum' or 'Item 1'."
        )
        user = (
            f"App type: {app_type}\n"
            f"Data category: {category}\n"
            f"App goal: {product_spec.get('goal', '')[:100]}\n\n"
            "Generate realistic mock data. Return JSON only."
        )
        try:
            raw = openai_client.generate(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                model="gpt-4o-mini",
                max_tokens=1600,  # local models need more tokens for full JSON
            )
            if not raw:
                return {}
            import re
            cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
            # Truncated JSON recovery: find the last complete array/object
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                # Try to salvage a truncated response by closing open brackets
                for tail in ["\n}]}", "\n}]", "\n}", "]}"]:
                    try:
                        return json.loads(cleaned + tail)
                    except json.JSONDecodeError:
                        pass
                logger.warning("[BACKEND_ENGINEER] mock data JSON unparseable — skipping")
                return {}
        except Exception as exc:
            logger.warning("[BACKEND_ENGINEER] mock data generation failed: %s", exc)
            return {}
