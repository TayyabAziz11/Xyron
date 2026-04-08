"""
In-memory draft store for AI Operator.

A Draft is a generated piece of content (LinkedIn post, email, etc.)
that needs user confirmation before being executed (posted/sent).

Lifecycle: draft → confirmed → executed
                 ↘ rejected
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class DraftStatus:
    DRAFT     = "draft"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    EXECUTED  = "executed"
    REJECTED  = "rejected"
    FAILED    = "failed"


class Draft(BaseModel):
    id:            str
    command_id:    str                  # parent command that generated this
    draft_type:    str                  # linkedin_post | email | whatsapp | instagram
    title:         str
    content:       str                  # the generated text
    metadata:      dict = Field(default_factory=dict)  # recipient, subject, etc.
    status:        str = DraftStatus.DRAFT
    created_at:    str
    updated_at:    Optional[str] = None
    executed_at:   Optional[str] = None
    error:         Optional[str] = None
    execution_url: Optional[str] = None  # URL of published post


class DraftService:
    def __init__(self):
        self._store: Dict[str, Draft] = {}
        self._lock = threading.Lock()

    def create(
        self,
        command_id: str,
        draft_type: str,
        title: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> Draft:
        draft = Draft(
            id=str(uuid.uuid4()),
            command_id=command_id,
            draft_type=draft_type,
            title=title,
            content=content,
            metadata=metadata or {},
            status=DraftStatus.DRAFT,
            created_at=datetime.now(timezone.utc).isoformat() + "Z",
        )
        with self._lock:
            self._store[draft.id] = draft
        return draft

    def get(self, draft_id: str) -> Optional[Draft]:
        return self._store.get(draft_id)

    def list_pending(self) -> List[Draft]:
        with self._lock:
            return [d for d in self._store.values() if d.status == DraftStatus.DRAFT]

    def list_all(self, limit: int = 20) -> List[Draft]:
        with self._lock:
            items = sorted(self._store.values(), key=lambda d: d.created_at, reverse=True)
            return items[:limit]

    def update(self, draft_id: str, **kwargs) -> Optional[Draft]:
        with self._lock:
            d = self._store.get(draft_id)
            if not d:
                return None
            for k, v in kwargs.items():
                if hasattr(d, k):
                    setattr(d, k, v)
            d.updated_at = datetime.now(timezone.utc).isoformat() + "Z"
            return d

    def confirm(self, draft_id: str) -> Optional[Draft]:
        return self.update(draft_id, status=DraftStatus.CONFIRMED)

    def reject(self, draft_id: str) -> Optional[Draft]:
        return self.update(draft_id, status=DraftStatus.REJECTED)

    def mark_executing(self, draft_id: str) -> Optional[Draft]:
        return self.update(draft_id, status=DraftStatus.EXECUTING)

    def mark_executed(self, draft_id: str, url: Optional[str] = None) -> Optional[Draft]:
        return self.update(
            draft_id,
            status=DraftStatus.EXECUTED,
            executed_at=datetime.now(timezone.utc).isoformat() + "Z",
            execution_url=url,
        )

    def mark_failed(self, draft_id: str, error: str) -> Optional[Draft]:
        return self.update(draft_id, status=DraftStatus.FAILED, error=error)


# Module-level singleton
draft_service = DraftService()
