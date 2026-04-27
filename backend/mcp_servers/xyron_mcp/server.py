#!/usr/bin/env python3
"""
Xyron MCP Server — JSON-RPC 2.0 over stdin/stdout.

Exposes Xyron AI Operator capabilities as MCP tools for Claude CLI.
Bridges to the running Xyron backend at http://localhost:8000.

Tools:
  system_info()                          [PERCEPTION — read-only]
  system_health()                        [PERCEPTION — read-only]
  take_screenshot()                      [PERCEPTION — read-only]
  get_volume()                           [PERCEPTION — read-only]
  get_datetime()                         [PERCEPTION — read-only]
  open_application(app_name)             [ACTION — low risk]
  smart_open(target)                     [ACTION — low risk]
  take_action(text, session_id?)         [ACTION — full AI command pipeline]

Architecture:
  - Requires Xyron backend running at http://localhost:8000.
  - All tool calls are HTTP requests to the backend REST API.
  - No direct tool execution — the backend handles all safety checks and HITL.
  - take_action routes through the full command pipeline (intent routing, agents, memory).

Usage:
    python3 mcp_servers/xyron_mcp/server.py

Register in Claude CLI (~/.claude/mcp_config.json):
    {
      "mcpServers": {
        "xyron": {
          "command": "python3",
          "args": ["/absolute/path/to/mcp_servers/xyron_mcp/server.py"]
        }
      }
    }
"""

import sys
import json
import signal
import logging
import traceback
import urllib.request
import urllib.error
from typing import Any, Dict, Optional
from pathlib import Path

logging.basicConfig(
    level=logging.WARNING,
    format="[xyron-mcp] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("xyron_mcp")

BACKEND_URL = "http://localhost:8000"


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str) -> dict:
    url = f"{BACKEND_URL}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {path}: {body[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Xyron backend at {BACKEND_URL} — is it running? ({e.reason})")


def _post(path: str, payload: dict) -> dict:
    url = f"{BACKEND_URL}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {path}: {body[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Xyron backend at {BACKEND_URL} — is it running? ({e.reason})")


# ── Tool definitions (MCP spec) ───────────────────────────────────────────────

TOOLS = [
    {
        "name": "system_info",
        "description": (
            "Get detailed system information: OS version, CPU name and core count, "
            "total RAM, and all drives with their sizes and free space. "
            "PERCEPTION ONLY — read-only, no side effects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "system_health",
        "description": (
            "Get live system health metrics: CPU usage %, RAM usage %, "
            "and disk usage % for every mounted drive. "
            "PERCEPTION ONLY — read-only, no side effects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "take_screenshot",
        "description": (
            "Capture a screenshot of the current screen. "
            "Returns a base64-encoded PNG image and the file path where it was saved. "
            "PERCEPTION ONLY — read-only, no side effects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_volume",
        "description": (
            "Get the current system audio volume level (0–100) and mute state. "
            "PERCEPTION ONLY — read-only, no side effects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_datetime",
        "description": (
            "Get the current date, time, day of week, timezone, and system uptime. "
            "PERCEPTION ONLY — read-only, no side effects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "open_application",
        "description": (
            "Launch an application by name on the user's computer. "
            "Examples: 'chrome', 'vs code', 'spotify', 'calculator', 'notepad'. "
            "ACTION — opens a window on the user's desktop."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Name of the application to open (e.g. 'chrome', 'vs code', 'spotify')",
                },
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "smart_open",
        "description": (
            "Intelligently open a file, folder, URL, or application by name or path. "
            "Handles: file paths, folder paths, URLs (http/https), app names. "
            "ACTION — opens the target on the user's desktop."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "What to open: file path, folder path, URL, or app name",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "take_action",
        "description": (
            "Execute any natural-language command through the full Xyron AI pipeline. "
            "Routes through intent detection, agent selection, memory context, and tool execution. "
            "Use this for complex tasks: 'draft an email to Alice', 'summarize my inbox', "
            "'what are my pending approvals', 'open settings and check wifi'. "
            "ACTION — may trigger real side effects depending on the command."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Natural-language command to execute (English or Urdu/Roman Urdu)",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID for conversation continuity (optional, defaults to 'mcp_session')",
                },
            },
            "required": ["text"],
        },
    },
]


# ── Tool handlers ─────────────────────────────────────────────────────────────

def handle_system_info(_args: Dict) -> str:
    result = _get("/api/v1/system/system-info")
    return json.dumps(result, ensure_ascii=False)


def handle_system_health(_args: Dict) -> str:
    result = _get("/api/v1/system/system-health")
    return json.dumps(result, ensure_ascii=False)


def handle_take_screenshot(_args: Dict) -> str:
    result = _post("/api/v1/automation/screenshot", {})
    return json.dumps(result, ensure_ascii=False)


def handle_get_volume(_args: Dict) -> str:
    result = _post("/api/v1/system/execute-tool", {"tool": "get_volume", "params": {}})
    return json.dumps(result, ensure_ascii=False)


def handle_get_datetime(_args: Dict) -> str:
    result = _post("/api/v1/system/execute-tool", {"tool": "get_date_time", "params": {}})
    return json.dumps(result, ensure_ascii=False)


def handle_open_application(args: Dict) -> str:
    app_name = args.get("app_name", "").strip()
    if not app_name:
        return json.dumps({"success": False, "error": "app_name is required"})
    result = _post("/api/v1/system/open-app", {"app_name": app_name})
    return json.dumps(result, ensure_ascii=False)


def handle_smart_open(args: Dict) -> str:
    target = args.get("target", "").strip()
    if not target:
        return json.dumps({"success": False, "error": "target is required"})
    # smart_open registry tool uses "query", not "target"
    result = _post("/api/v1/system/execute-tool", {"tool": "smart_open", "params": {"query": target}})
    return json.dumps(result, ensure_ascii=False)


def handle_take_action(args: Dict) -> str:
    text = args.get("text", "").strip()
    if not text:
        return json.dumps({"success": False, "error": "text is required"})
    session_id = args.get("session_id", "mcp_session")
    result = _post("/api/v1/commands", {"text": text, "session_id": session_id})
    return json.dumps(result, ensure_ascii=False)


HANDLERS: Dict[str, Any] = {
    "system_info":        handle_system_info,
    "system_health":      handle_system_health,
    "take_screenshot":    handle_take_screenshot,
    "get_volume":         handle_get_volume,
    "get_datetime":       handle_get_datetime,
    "open_application":   handle_open_application,
    "smart_open":         handle_smart_open,
    "take_action":        handle_take_action,
}


# ── JSON-RPC 2.0 server loop ──────────────────────────────────────────────────

def send(obj: Dict):
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def error_response(req_id: Any, code: int, message: str) -> Dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def ok_response(req_id: Any, result: Any) -> Dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def dispatch(msg: Dict) -> Optional[Dict]:
    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    if req_id is None and method not in ("initialize",):
        return None

    if method == "initialize":
        return ok_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "xyron", "version": "1.0.0"},
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return ok_response(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        handler = HANDLERS.get(tool_name)
        if not handler:
            return error_response(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            result_text = handler(tool_args)
            return ok_response(req_id, {
                "content": [{"type": "text", "text": result_text}]
            })
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("Tool %s failed: %s\n%s", tool_name, e, tb)
            return ok_response(req_id, {
                "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                "isError": True,
            })

    if method == "ping":
        return ok_response(req_id, {})

    return error_response(req_id, -32601, f"Method not found: {method}")


def _shutdown(sig, frame):
    logger.info("Xyron MCP server shutting down …")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("Xyron MCP server started — listening on stdin")

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as e:
            send(error_response(None, -32700, f"Parse error: {e}"))
            continue

        try:
            response = dispatch(msg)
        except Exception as e:
            response = error_response(msg.get("id"), -32603, f"Internal error: {e}")

        if response is not None:
            send(response)

    _shutdown(None, None)


if __name__ == "__main__":
    main()
