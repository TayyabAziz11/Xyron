from __future__ import annotations

"""
Stack selector for the Coding Builder Agent.

Inspects the natural-language goal and feature list, then returns the most
appropriate technology-stack key.  All stack metadata (dev command, port, …)
is centralised here so every other module can depend on this single source
of truth.
"""

import logging
import re

logger = logging.getLogger(__name__)


class StackSelector:
    """Choose a tech-stack based on goal keywords and explicit feature flags."""

    DEFAULT_FRONTEND_STACK = "vite-react-tailwind"

    STACKS: dict[str, dict] = {
        "vite-react-tailwind": {
            "name": "Vite + React + Tailwind CSS",
            "runtime": "node",
            "dev_command": "npm run dev",
            "port": 5173,
            "build_command": "npm run build",
            "install_command": "npm install",
        },
        "nextjs": {
            "name": "Next.js",
            "runtime": "node",
            "dev_command": "npm run dev",
            "port": 3000,
            "build_command": "npm run build",
            "install_command": "npm install",
        },
        "python-flask": {
            "name": "Python Flask",
            "runtime": "python",
            "dev_command": "python app.py",
            "port": 5000,
            "build_command": None,
            "install_command": "pip install -r requirements.txt",
        },
        "html-vanilla": {
            "name": "Vanilla HTML/CSS/JS",
            "runtime": "node",
            "dev_command": "npx serve .",
            "port": 3000,
            "build_command": None,
            "install_command": "npm install -g serve",
        },
    }

    # Keyword → stack key.  First match wins (order matters).
    _RULES: list[tuple[list[str], str]] = [
        # Explicit framework mentions
        (["next.js", "nextjs", "next js"], "nextjs"),
        (["flask", "django", "fastapi", "python api", "python backend", "python server"], "python-flask"),
        (["vanilla", "plain html", "static html", "html only", "no framework"], "html-vanilla"),
        # Catch-all frontend categories → vite-react-tailwind
        (
            [
                "clothing", "fashion", "ecommerce", "e-commerce", "shop", "store",
                "portfolio", "personal site", "landing page", "landing",
                "dashboard", "admin panel", "admin", "saas", "startup",
                "website", "web app", "web application", "react", "tailwind",
            ],
            "vite-react-tailwind",
        ),
        # Explicit backend / API projects
        (["api", "backend", "rest api", "graphql api", "microservice"], "python-flask"),
    ]

    def select(self, goal: str, features: list[str]) -> str:
        """Return a stack key from ``STACKS``.

        Args:
            goal:     Raw user goal string.
            features: Parsed feature list (lower-case strings).

        Returns:
            Stack key string such as ``"vite-react-tailwind"``.
        """
        combined = (goal + " " + " ".join(features)).lower()

        for keywords, stack_key in self._RULES:
            for kw in keywords:
                if re.search(r"\b" + re.escape(kw) + r"\b", combined):
                    logger.info("[STACK_SELECTOR] matched keyword=%r → stack=%s", kw, stack_key)
                    return stack_key

        logger.info("[STACK_SELECTOR] no keyword match — using default=%s", self.DEFAULT_FRONTEND_STACK)
        return self.DEFAULT_FRONTEND_STACK

    def get_stack_info(self, key: str) -> dict:
        """Return full stack metadata dict.  Falls back to default stack."""
        return self.STACKS.get(key, self.STACKS[self.DEFAULT_FRONTEND_STACK])
