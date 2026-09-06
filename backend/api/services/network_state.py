"""
network_state — cheap, cached network-reachability probe.

Several model loaders in this codebase (SentenceTransformer for the intent
classifier / semantic search / semantic memory, Kokoro's hf_hub_download)
try a local-cache-only load first and fall back to a plain network call on
any failure. On a genuinely offline machine, that fallback branch hands the
request to huggingface_hub's own retry wrapper — 5 attempts with 2s/4s/8s/8s
exponential backoff against a dead DNS lookup, ~30s burned per lazy load,
repeated on every process that touches one of these loaders.

apply_offline_env() sets HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE once we detect
we're offline. Both libraries honor these globally, at the point of any
call, regardless of each call site's own local_files_only kwarg — so this
closes every current and future huggingface_hub call path in one place,
without touching each loader individually.
"""
from __future__ import annotations

import logging
import os
import socket
import time

logger = logging.getLogger(__name__)

_TTL = 30.0  # seconds — re-probe periodically so connectivity returning is noticed
_PROBE_HOST = "huggingface.co"
_PROBE_TIMEOUT = 1.0

_state = {"checked_at": 0.0, "offline": False}


def _forced_local_only() -> bool:
    return os.getenv("LOCAL_ONLY_MODE", "").lower() in ("1", "true", "yes")


def is_offline(force_recheck: bool = False) -> bool:
    """Cheap, TTL-cached check for whether the network looks reachable.

    LOCAL_ONLY_MODE always counts as offline (explicit opt-out, no probe
    needed). Otherwise probes a single fast TCP connect — if DNS resolution
    or the connection itself fails, we're offline.
    """
    if _forced_local_only():
        return True

    now = time.time()
    if not force_recheck and (now - _state["checked_at"]) < _TTL:
        return _state["offline"]

    offline = True
    try:
        socket.create_connection((_PROBE_HOST, 443), timeout=_PROBE_TIMEOUT).close()
        offline = False
    except OSError:
        offline = True

    _state["checked_at"] = now
    _state["offline"] = offline
    return offline


def apply_offline_env(force_recheck: bool = False) -> bool:
    """Probe reachability and, if offline, set HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE
    so huggingface_hub/transformers skip network attempts at the library level.

    Safe to call repeatedly (e.g. right before each lazy model load) — cheap
    once cached, and re-probes every _TTL seconds so connectivity coming back
    doesn't leave the process stuck in offline mode forever.
    """
    offline = is_offline(force_recheck=force_recheck)
    if offline:
        if os.environ.get("HF_HUB_OFFLINE") != "1":
            logger.info("[NetworkState] offline detected — setting HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    else:
        # Only clear vars we ourselves may have set — never fight an
        # operator-set env var from outside this process.
        if os.environ.get("HF_HUB_OFFLINE") == "1" and not _forced_local_only():
            os.environ.pop("HF_HUB_OFFLINE", None)
            os.environ.pop("TRANSFORMERS_OFFLINE", None)
    return offline
