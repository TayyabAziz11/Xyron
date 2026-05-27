from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time
import json
import os

from .subscription import Tier


@dataclass
class XyronUser:
    id: str
    email: str
    name: str
    tier: Tier = Tier.FREE
    image_url: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    preferred_name: Optional[str] = None  # user's custom Xyron nickname

    # Prepared for future Stripe integration
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None

    # Prepared for future cloud sync
    sync_enabled: bool = False
    sync_token: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "preferred_name": self.preferred_name,
            "tier": self.tier.value,
            "image_url": self.image_url,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
        }


# ── Minimal local user store ──────────────────────────────────────────────────
# Persists to ~/.xyron/users.json. Pre-Stripe/pre-cloud: keeps one user record.
# When cloud sync lands, replace this module with a DB-backed service.

_STORE_PATH = os.path.expanduser("~/.xyron/users.json")


def _load_store() -> dict:
    try:
        os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
        with open(_STORE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_store(data: dict) -> None:
    os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
    with open(_STORE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def upsert_user(user: XyronUser) -> XyronUser:
    store = _load_store()
    store[user.id] = user.to_dict()
    _save_store(store)
    return user


def get_user(user_id: str) -> Optional[XyronUser]:
    store = _load_store()
    data = store.get(user_id)
    if not data:
        return None
    return XyronUser(
        id=data["id"],
        email=data["email"],
        name=data["name"],
        preferred_name=data.get("preferred_name"),
        tier=Tier(data.get("tier", "free")),
        image_url=data.get("image_url"),
        created_at=data.get("created_at", time.time()),
        last_seen=data.get("last_seen", time.time()),
    )


def set_preferred_name(user_id: str, preferred_name: str) -> Optional[XyronUser]:
    user = get_user(user_id)
    if not user:
        return None
    user.preferred_name = preferred_name.strip() or None
    user.last_seen = time.time()
    return upsert_user(user)


def set_user_tier(user_id: str, tier: Tier) -> Optional[XyronUser]:
    user = get_user(user_id)
    if not user:
        return None
    user.tier = tier
    user.last_seen = time.time()
    return upsert_user(user)
