from __future__ import annotations

"""
ErrorAnalyzer — parse build/compile/runtime errors and suggest fixes via LLM.

Supported error sources:
- npm install stderr
- Vite dev/build output
- TypeScript compiler output (tsc)
"""

import logging
import re
from typing import Optional

from api.services.openai_client import openai_client

logger = logging.getLogger(__name__)

# Canonical error dict schema:
# {
#   "type":       str   — "npm" | "vite" | "typescript" | "runtime" | "unknown"
#   "message":    str   — human-readable summary
#   "file":       str   — relative file path (empty string if unavailable)
#   "line":       int   — line number (0 if unavailable)
#   "raw":        str   — original raw error text
#   "suggestion": str   — heuristic fix hint (may be empty; LLM enriches this)
# }
_EMPTY_ERROR: dict = {
    "type": "unknown",
    "message": "",
    "file": "",
    "line": 0,
    "raw": "",
    "suggestion": "",
}


class ErrorAnalyzer:
    """Detect, classify and propose fixes for common project errors."""

    # ── npm ───────────────────────────────────────────────────────────────────

    def parse_npm_error(self, stderr: str) -> dict:
        """Parse npm install/run stderr into a structured error dict."""
        result = {**_EMPTY_ERROR, "type": "npm", "raw": stderr}

        # npm ERR! code ENOENT / ENOTFOUND / ERESOLVE …
        code_match = re.search(r"npm ERR!\s+code\s+(\w+)", stderr)
        msg_match = re.search(r"npm ERR!\s+(.*)", stderr)
        missing_match = re.search(r"Cannot find module '([^']+)'", stderr)
        peer_match = re.search(r"peer dep missing:\s+(.+)", stderr)

        if code_match:
            code = code_match.group(1)
            result["type"] = f"npm:{code}"
            if code == "ENOENT":
                result["message"] = "A required file or command was not found."
                result["suggestion"] = "Check that Node.js is installed and the package.json path is correct."
            elif code == "ERESOLVE":
                result["message"] = "Peer dependency conflict detected."
                result["suggestion"] = "Try adding --legacy-peer-deps to the npm install command."
            elif code == "ENOTFOUND":
                result["message"] = "Network error — npm registry unreachable."
                result["suggestion"] = "Check internet connection or try a different registry."
            else:
                result["message"] = f"npm error code {code}."
        elif missing_match:
            pkg = missing_match.group(1)
            result["message"] = f"Missing module: {pkg}"
            result["suggestion"] = f"Run: npm install {pkg}"
        elif peer_match:
            result["message"] = f"Peer dependency issue: {peer_match.group(1)}"
            result["suggestion"] = "npm install --legacy-peer-deps"
        elif msg_match:
            result["message"] = msg_match.group(1).strip()

        return result

    # ── Vite ─────────────────────────────────────────────────────────────────

    def parse_vite_error(self, output: str) -> dict:
        """Parse Vite dev-server or build error output."""
        result = {**_EMPTY_ERROR, "type": "vite", "raw": output}

        # Plugin error:  [vite] Internal server error: …
        internal_match = re.search(r"\[vite\] Internal server error:\s*(.+)", output)
        # File path + line:  src/App.tsx:12:5: error: …
        file_line_match = re.search(
            r"([\w./\\-]+\.[a-z]{2,4}):(\d+):?\d*:?\s*(error|warning):\s*(.+)", output, re.IGNORECASE
        )
        # Transform error:  [plugin:vite:...] …
        plugin_match = re.search(r"\[plugin:([^\]]+)\]\s*(.+)", output)
        # Rollup build errors
        rollup_match = re.search(r"RollupError:\s*(.+)", output)

        if internal_match:
            result["message"] = internal_match.group(1).strip()
        elif file_line_match:
            result["file"] = file_line_match.group(1)
            result["line"] = int(file_line_match.group(2))
            result["message"] = file_line_match.group(4).strip()
        elif plugin_match:
            result["message"] = f"Plugin [{plugin_match.group(1)}]: {plugin_match.group(2).strip()}"
        elif rollup_match:
            result["message"] = rollup_match.group(1).strip()
        else:
            # Take the first non-blank line that looks like an error message.
            for line in output.splitlines():
                line = line.strip()
                if line and not line.startswith(("●", "PASS", "FAIL", "✓", "✗")):
                    result["message"] = line[:200]
                    break

        return result

    # ── TypeScript ────────────────────────────────────────────────────────────

    def parse_typescript_error(self, stderr: str) -> dict:
        """Parse TypeScript compiler (tsc) output."""
        result = {**_EMPTY_ERROR, "type": "typescript", "raw": stderr}

        # TS pattern:  src/App.tsx(12,5): error TS2345: …
        ts_match = re.search(
            r"([\w./\\-]+\.tsx?)\((\d+),\d+\):\s+error\s+(TS\d+):\s+(.+)", stderr
        )
        if ts_match:
            result["file"] = ts_match.group(1)
            result["line"] = int(ts_match.group(2))
            code = ts_match.group(3)
            result["message"] = f"{code}: {ts_match.group(4).strip()}"
            result["suggestion"] = self._ts_code_hint(code)
        else:
            lines = [l.strip() for l in stderr.splitlines() if l.strip()]
            result["message"] = lines[0][:200] if lines else "TypeScript compilation error"

        return result

    def _ts_code_hint(self, ts_code: str) -> str:
        """Return a quick fix hint for common TS error codes."""
        hints: dict[str, str] = {
            "TS2304": "Identifier not found — check import paths and spelling.",
            "TS2345": "Argument type mismatch — verify the function signature.",
            "TS2322": "Type assignment error — add explicit type annotation.",
            "TS7006": "Implicit 'any' — add a type annotation to the parameter.",
            "TS2307": "Module not found — ensure the package is installed and path is correct.",
            "TS2339": "Property does not exist on type — check the interface definition.",
        }
        return hints.get(ts_code, "")

    # ── LLM-based suggestion ──────────────────────────────────────────────────

    async def get_fix_suggestion(self, error: dict, file_content: str) -> str:
        """Use the LLM to generate a concrete fix suggestion.

        Returns a plaintext suggestion string (may be empty on API failure).
        """
        if not error.get("message"):
            return ""

        system_prompt = (
            "You are an expert JavaScript/TypeScript/React developer.\n"
            "The user has a build error. Provide a concise, actionable fix. "
            "No markdown headers — plain text only. Max 3 sentences."
        )
        file_snippet = file_content[:1500] if file_content else "(file content not available)"
        user_msg = (
            f"Error type: {error.get('type', 'unknown')}\n"
            f"Error message: {error.get('message', '')}\n"
            f"File: {error.get('file', 'unknown')} line {error.get('line', 0)}\n\n"
            f"Relevant file content:\n```\n{file_snippet}\n```\n\n"
            "What is the most likely fix?"
        )

        try:
            suggestion = openai_client.generate(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                model="gpt-4o-mini",
            )
            return suggestion or ""
        except Exception as exc:
            logger.warning("[ERROR_ANALYZER] LLM suggestion failed: %s", exc)
            return error.get("suggestion", "")
