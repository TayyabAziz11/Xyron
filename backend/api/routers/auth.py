"""
Auth + user management router.

Endpoints:
  POST /api/v1/auth/sync     — upsert user after Clerk auth
  GET  /api/v1/auth/me       — fetch current user
  POST /api/v1/auth/tier     — update subscription tier
  POST /api/v1/auth/signout  — sign-out housekeeping
"""

import time
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from api.models.user import XyronUser, get_user, upsert_user, set_user_tier, set_preferred_name
from api.models.subscription import Tier

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    id: str
    email: str
    name: str
    image_url: Optional[str] = None


class TierUpdateRequest(BaseModel):
    tier: Tier


class PreferredNameRequest(BaseModel):
    preferred_name: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_user(body: SyncRequest):
    """Called by the frontend after Clerk authenticates. Creates or updates the user record."""
    existing = get_user(body.id)
    if existing:
        existing.name = body.name
        existing.email = body.email
        existing.image_url = body.image_url
        existing.last_seen = time.time()
        user = upsert_user(existing)
    else:
        user = upsert_user(XyronUser(
            id=body.id,
            email=body.email,
            name=body.name,
            image_url=body.image_url,
        ))
    return user.to_dict()


@router.get("/me")
async def get_me(x_user_id: Optional[str] = Header(default=None)):
    if not x_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user id")
    user = get_user(x_user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user.to_dict()


@router.post("/tier")
async def update_tier(
    body: TierUpdateRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    if not x_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user id")
    user = set_user_tier(x_user_id, body.tier)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user.to_dict()


@router.post("/preferred-name")
async def update_preferred_name(
    body: PreferredNameRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    """Set the user's preferred Xyron nickname (used in greetings)."""
    if not x_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user id")
    name = body.preferred_name.strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid name")
    import logging as _log
    _log.getLogger(__name__).info("[PREFERRED_NAME_LOADED] user=%s name=%r", x_user_id, name)
    user = set_preferred_name(x_user_id, name)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user.to_dict()


@router.post("/signout")
async def signout(x_user_id: Optional[str] = Header(default=None)):
    """Optional cleanup on sign-out — logs the event for analytics later."""
    return {"status": "ok", "user_id": x_user_id}
