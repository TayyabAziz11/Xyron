# Xyron MCP Server

Exposes Xyron AI Operator capabilities as MCP tools for Claude CLI (and any MCP client).

Bridges to the running Xyron backend at `http://localhost:8000` using pure stdlib HTTP — no extra dependencies.

## Prerequisites

The Xyron backend must be running:

```bash
cd backend
source .venv/bin/activate
python3 -m uvicorn api.main:app --reload --port 8000
```

## Tools

| Tool | Type | Description |
|---|---|---|
| `system_info` | Perception | OS, CPU, RAM, drives with sizes |
| `system_health` | Perception | Live CPU %, RAM %, disk % |
| `take_screenshot` | Perception | Capture screen, returns base64 PNG |
| `get_volume` | Perception | Current volume level and mute state |
| `get_datetime` | Perception | Current date, time, timezone, uptime |
| `open_application` | Action | Launch app by name (chrome, spotify, …) |
| `smart_open` | Action | Open file, folder, URL, or app |
| `take_action` | Action | Full natural-language command pipeline |

## Register in Claude CLI

Add to `~/.claude/mcp_config.json`:

```json
{
  "mcpServers": {
    "xyron": {
      "command": "python3",
      "args": ["/home/ps_qasim/projects/xyron/backend/mcp_servers/xyron_mcp/server.py"]
    }
  }
}
```

Then in Claude CLI:

```
claude "use xyron to take a screenshot"
claude "use xyron to open chrome"
claude "use xyron take_action: summarize my inbox"
```

## Manual test

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}' \
  | python3 backend/mcp_servers/xyron_mcp/server.py

echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | python3 backend/mcp_servers/xyron_mcp/server.py
```
