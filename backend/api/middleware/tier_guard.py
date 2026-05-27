"""
Tier access guard — use as a FastAPI dependency.

Usage:
    from api.middleware.tier_guard import require_feature

    @router.post("/some-pro-endpoint")
    async def handler(user=Depends(require_feature(Feature.ADVANCED_AI))):
        ...

Until Clerk JWT verification is wired in, user_id is read from the
X-User-Id request header (set by the frontend after auth).
"""

from fastapi import Header, HTTPException, status
from typing import Optional
from functools import lru_cache

from api.models.subscription import Feature, Tier, can_access
from api.models.user import get_user, XyronUser


def _get_user_from_header(x_user_id: Optional[str]) -> XyronUser | None:
    if not x_user_id:
        return None
    return get_user(x_user_id)


def _effective_tier(user: Optional[XyronUser]) -> Tier:
    """Unauthenticated requests get Free-tier access."""
    if user is None:
        return Tier.FREE
    return user.tier


def require_feature(feature: Feature):
    """FastAPI dependency factory that gates an endpoint behind a feature flag."""
    async def _dep(x_user_id: Optional[str] = Header(default=None)):
        user = _get_user_from_header(x_user_id)
        tier = _effective_tier(user)
        if not can_access(tier, feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "tier_required",
                    "required_for": feature.value,
                    "current_tier": tier.value,
                    "message": f"This feature requires a higher subscription tier.",
                }
            )
        return user
    return _dep


def get_current_user():
    """Non-gating dependency — resolves user or None without blocking."""
    async def _dep(x_user_id: Optional[str] = Header(default=None)):
        return _get_user_from_header(x_user_id)
    return _dep
