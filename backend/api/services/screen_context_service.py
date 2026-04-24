"""
Screen Context Service — Feature #1
Captures a screenshot every 10 seconds while active, keeps last 3 in a
rolling buffer. The voice router injects the latest description into the
system prompt so "explain what I'm reading" works without a separate command.
"""
from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CAPTURE_INTERVAL = 10      # seconds between captures
_MAX_HISTORY      = 3       # rolling buffer size
_STALE_AFTER      = 60      # seconds before context is considered stale


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False

_ON_WSL = _is_wsl()


def _take_screenshot() -> Optional[str]:
    """Capture screen via PowerShell. Returns base64-encoded PNG or None."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            out_path = f.name

        if _ON_WSL:
            win_path = subprocess.run(
                ["wslpath", "-w", out_path],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                f"$bmp = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                f"$bitmap = New-Object System.Drawing.Bitmap($bmp.Width, $bmp.Height); "
                f"$g = [System.Drawing.Graphics]::FromImage($bitmap); "
                f"$g.CopyFromScreen($bmp.Location, [System.Drawing.Point]::Empty, $bmp.Size); "
                f"$bitmap.Save('{win_path}'); $g.Dispose(); $bitmap.Dispose()"
            )
            r = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=12,
            )
        else:
            import mss
            import mss.tools
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                img = sct.grab(monitor)
                mss.tools.to_png(img.rgb, img.size, output=out_path)

        data = Path(out_path).read_bytes()
        Path(out_path).unlink(missing_ok=True)
        return base64.b64encode(data).decode()
    except Exception as exc:
        logger.debug("Screenshot failed: %s", exc)
        return None


def _describe_screenshot(b64: str, openai_key: str) -> str:
    """Send screenshot to GPT-4o Vision; return 1-sentence description."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=120,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Describe what is currently on screen in 1-2 sentences. "
                            "Focus on the active application and visible content. "
                            "Be specific about text, document titles, or page names if visible. "
                            "No markdown. No preamble."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"},
                    },
                ],
            }],
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.debug("Vision describe failed: %s", exc)
        return ""


class ScreenContextService:
    """Background service: takes screenshot every 10s, stores description."""

    def __init__(self) -> None:
        self._lock        = threading.Lock()
        self._history:    list[dict] = []   # [{ts, description, b64}]
        self._running     = False
        self._thread:     threading.Thread | None = None
        self._openai_key  = ""

    def start(self, openai_key: str) -> None:
        """Start the background capture thread."""
        if self._running:
            return
        self._openai_key = openai_key
        self._running    = True
        self._thread     = threading.Thread(
            target=self._loop, daemon=True, name="screen-context"
        )
        self._thread.start()
        logger.info("ScreenContextService started (interval=%ds)", _CAPTURE_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self._capture_once()
            except Exception as exc:
                logger.debug("Capture loop error: %s", exc)
            time.sleep(_CAPTURE_INTERVAL)

    def _capture_once(self) -> None:
        b64 = _take_screenshot()
        if not b64:
            return
        desc = _describe_screenshot(b64, self._openai_key) if self._openai_key else ""
        entry = {"ts": time.time(), "description": desc, "b64": b64}
        with self._lock:
            self._history.append(entry)
            if len(self._history) > _MAX_HISTORY:
                self._history.pop(0)

    def get_context(self) -> str:
        """Return the latest screen description for injection into system prompt."""
        with self._lock:
            if not self._history:
                return ""
            latest = self._history[-1]
        if time.time() - latest["ts"] > _STALE_AFTER:
            return ""
        desc = latest.get("description", "")
        if not desc:
            return ""
        return f"CURRENT SCREEN CONTEXT (captured {int(time.time() - latest['ts'])}s ago): {desc}"

    def get_latest_b64(self) -> Optional[str]:
        """Return raw base64 of the most recent screenshot."""
        with self._lock:
            if not self._history:
                return None
            return self._history[-1].get("b64")

    def is_running(self) -> bool:
        return self._running


screen_context_service = ScreenContextService()
