from __future__ import annotations

"""
ProductPlanner — internal CodingAgent role.

Understands the requested product:
  - app type classification
  - page list
  - feature list
  - user flow summary

Returns a ProductSpec dict consumed by other role modules.

Log tag: [PRODUCT_PLANNER]
"""

import json
import logging
import re
from typing import Any, Optional

from api.services.openai_client import openai_client

logger = logging.getLogger(__name__)

# ── App type detection (fast, no LLM needed) ──────────────────────────────────

_APP_TYPES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(clothing|fashion|apparel|boutique|wear)\b", re.I), "clothing-ecommerce"),
    (re.compile(r"\b(saas|software\s+as\s+a\s+service|subscription\s+app)\b", re.I), "saas-product"),
    (re.compile(r"\b(dashboard|admin\s*panel|analytics|metrics)\b", re.I), "admin-dashboard"),
    (re.compile(r"\b(portfolio|personal\s+site|my\s+site)\b", re.I), "developer-portfolio"),
    (re.compile(r"\b(landing\s+page|product\s+launch|coming\s+soon)\b", re.I), "landing-page"),
    (re.compile(r"\b(ecommerce|e-commerce|online\s+store|shop|marketplace)\b", re.I), "ecommerce"),
    (re.compile(r"\b(restaurant|food|cafe|menu)\b", re.I), "restaurant"),
    (re.compile(r"\b(blog|news|magazine|editorial)\b", re.I), "blog"),
    (re.compile(r"\b(agency|creative\s+agency|design\s+agency|marketing)\b", re.I), "creative-agency"),
    (re.compile(r"\b(fitness|gym|workout|wellness|health)\b", re.I), "fitness"),
    (re.compile(r"\b(travel|hotel|tourism|booking)\b", re.I), "travel"),
    (re.compile(r"\b(real\s+estate|property|listing)\b", re.I), "real-estate"),
    (re.compile(r"\b(music|band|concert|streaming)\b", re.I), "music"),
    (re.compile(r"\b(educational|learning|course|school)\b", re.I), "education"),
    (re.compile(r"\b(app|mobile\s+app|ios|android)\b", re.I), "mobile-app-landing"),
]

# ── Page definitions per app type ─────────────────────────────────────────────

_PAGES: dict[str, list[str]] = {
    "clothing-ecommerce": ["Home", "Shop", "Product Detail", "Cart", "About", "Contact"],
    "saas-product":       ["Home", "Features", "Pricing", "Dashboard Preview", "Login", "Contact"],
    "admin-dashboard":    ["Overview", "Analytics", "Users", "Reports", "Settings"],
    "developer-portfolio": ["Home", "About", "Projects", "Skills", "Contact"],
    "landing-page":       ["Hero", "Features", "Testimonials", "Pricing", "CTA"],
    "ecommerce":          ["Home", "Catalog", "Product", "Cart", "Checkout", "Account"],
    "restaurant":         ["Home", "Menu", "Reservations", "Gallery", "Contact"],
    "blog":               ["Home", "Post List", "Post Detail", "About", "Contact"],
    "creative-agency":    ["Home", "Work", "Services", "Team", "Contact"],
    "fitness":            ["Home", "Classes", "Trainers", "Pricing", "Contact"],
    "travel":             ["Home", "Destinations", "Tours", "Booking", "Contact"],
    "real-estate":        ["Home", "Listings", "Property Detail", "Agents", "Contact"],
    "music":              ["Home", "Albums", "Events", "Store", "Contact"],
    "education":          ["Home", "Courses", "Instructors", "Pricing", "Contact"],
    "mobile-app-landing": ["Hero", "Features", "Screenshots", "Download", "FAQ"],
}

# ── Feature definitions per app type ─────────────────────────────────────────

_FEATURES: dict[str, list[str]] = {
    "clothing-ecommerce":  ["hero-banner", "product-grid", "product-card", "category-filter", "newsletter-signup", "size-guide"],
    "saas-product":        ["hero-with-cta", "feature-cards", "pricing-table", "testimonials", "demo-preview", "faq"],
    "admin-dashboard":     ["stats-cards", "charts", "data-table", "sidebar-nav", "top-bar", "notifications", "quick-actions"],
    "developer-portfolio": ["hero-introduction", "project-cards", "skills-grid", "experience-timeline", "contact-form"],
    "landing-page":        ["hero-cta", "value-props", "social-proof", "feature-list", "final-cta"],
    "ecommerce":           ["hero-banner", "product-grid", "product-card", "search-bar", "category-nav", "cart-icon"],
    "restaurant":          ["hero-ambiance", "menu-sections", "reservation-form", "chef-section", "gallery-grid"],
    "blog":                ["featured-posts", "post-grid", "post-card", "category-tags", "author-bio", "subscribe"],
    "creative-agency":     ["hero-showreel", "portfolio-grid", "service-cards", "team-profiles", "case-studies"],
    "fitness":             ["hero-motivation", "class-schedule", "trainer-cards", "pricing-tiers", "testimonials"],
    "travel":              ["hero-destinations", "destination-cards", "tour-packages", "search-widget", "testimonials"],
    "real-estate":         ["hero-search", "listing-grid", "property-card", "map-view", "agent-profiles"],
    "music":               ["hero-artist", "album-grid", "tour-dates", "merch-store", "music-player"],
    "education":           ["hero-learning", "course-grid", "instructor-profiles", "testimonials", "enrollment-cta"],
    "mobile-app-landing":  ["hero-device", "feature-grid", "screenshot-carousel", "app-store-badges", "faq"],
}


class ProductPlanner:
    """Analyse project goal and produce a structured ProductSpec."""

    def detect_app_type(self, goal: str) -> str:
        """Detect app type from goal string without LLM (fast path)."""
        for pattern, app_type in _APP_TYPES:
            if pattern.search(goal):
                return app_type
        return "generic-website"

    def get_pages(self, app_type: str) -> list[str]:
        return _PAGES.get(app_type, ["Home", "About", "Contact"])

    def get_features(self, app_type: str) -> list[str]:
        return _FEATURES.get(app_type, ["hero-section", "content-section", "footer"])

    async def plan(self, goal: str) -> dict[str, Any]:
        """Return a ProductSpec dict.

        Fast path: detect app_type + lookup pages/features.
        LLM enrichment: generate user_flow and content_notes (optional, non-blocking).
        """
        app_type = self.detect_app_type(goal)
        pages    = self.get_pages(app_type)
        features = self.get_features(app_type)

        logger.info("[PRODUCT_PLANNER] app_type=%r pages=%d features=%d",
                    app_type, len(pages), len(features))

        # LLM enrichment for user flow (light call)
        user_flow = await self._get_user_flow(goal, app_type, pages)

        spec: dict[str, Any] = {
            "goal":       goal,
            "app_type":   app_type,
            "pages":      pages,
            "features":   features,
            "user_flow":  user_flow,
            "page_count": len(pages),
        }
        return spec

    async def _get_user_flow(self, goal: str, app_type: str, pages: list[str]) -> str:
        """Ask LLM for a one-paragraph user flow description."""
        try:
            prompt = (
                f"Describe in one paragraph the primary user flow for a {app_type} website: '{goal}'. "
                f"Pages: {', '.join(pages)}. "
                "Be specific about what users do from landing to conversion. Max 80 words."
            )
            result = openai_client.generate(
                [{"role": "user", "content": prompt}],
                model="gpt-4o-mini",
                max_tokens=120,
            )
            return (result or "").strip()
        except Exception as exc:
            logger.debug("[PRODUCT_PLANNER] user_flow LLM skipped: %s", exc)
            return f"Users land on the homepage, explore {app_type} content, and complete their goal."
