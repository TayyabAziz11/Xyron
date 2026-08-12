"""
Phase 6 — Action Verifier.

Every operator action must verify its result.
Never assume success — check, retry, report.
"""
from __future__ import annotations

import logging
import time

from operator_mode.operator_types import VerifyMethod, VerifySpec

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class OperatorVerifier:

    def verify(self, spec: VerifySpec, trace_id: str = "?") -> bool:
        """
        Run verification. Returns True on success.
        Retries up to spec.timeout_ms / 500ms poll intervals.
        """
        logger.info("[TRACE %s] [VERIFY_START] method=%s expected=%r",
                    trace_id, spec.method.name, spec.expected[:40])
        deadline = time.time() + spec.timeout_ms / 1000.0
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            ok = self._check(spec)
            if ok:
                logger.info("[TRACE %s] [VERIFY_SUCCESS] method=%s attempt=%d",
                            trace_id, spec.method.name, attempt)
                return True
            if not spec.retry_on_fail:
                break
            logger.info("[TRACE %s] [VERIFY_RETRY] method=%s attempt=%d",
                        trace_id, spec.method.name, attempt)
            time.sleep(0.5)
        logger.warning("[TRACE %s] [VERIFY_FAIL] method=%s expected=%r",
                       trace_id, spec.method.name, spec.expected[:40])
        return False

    def _check(self, spec: VerifySpec) -> bool:
        if spec.method == VerifyMethod.NONE:
            return True
        if spec.method in (VerifyMethod.WINDOW_EXISTS, VerifyMethod.WINDOW_TITLE,
                           VerifyMethod.ACTIVE_WINDOW):
            return self._check_window(spec)
        if spec.method == VerifyMethod.SCREENSHOT_OCR:
            return self._check_ocr(spec)
        return True

    def _check_window(self, spec: VerifySpec) -> bool:
        try:
            from operator_mode.screen_observer import screen_observer
            state = screen_observer.capture()
            name_lower = spec.expected.lower()
            all_windows = state.open_windows + ([state.active_window] if state.active_window else [])
            return any(name_lower in w.lower() for w in all_windows)
        except Exception as exc:
            logger.debug("[VERIFY] window check error: %s", exc)
            return False

    def _check_ocr(self, spec: VerifySpec) -> bool:
        try:
            from api.tools import registry as _r
            result = _r.execute("read_screen", {}, {})
            if result.success and spec.expected.lower() in (result.text or "").lower():
                return True
        except Exception as exc:
            logger.debug("[VERIFY] ocr check error: %s", exc)
        return False


operator_verifier = OperatorVerifier()
