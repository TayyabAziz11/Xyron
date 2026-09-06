# WhatsApp MCP Server

JSON-RPC 2.0 MCP server exposing WhatsApp Web automation as Claude tools.

Uses **Playwright + WhatsApp Web** — no WhatsApp Cloud API required.

## Quick start

```bash
# 1. Install dependencies (once)
pip install playwright
playwright install chromium
playwright install-deps chromium   # WSL: installs system libs

# 2. Pair your WhatsApp account (once)
python3 scripts/wa_setup.py

# 3. Check session status
python3 scripts/wa_setup.py --status

# 4. Start the MCP server (used by Claude CLI automatically)
python3 mcp_servers/whatsapp_mcp/server.py
```

## Claude CLI registration

Add to `~/.claude/mcp_config.json`:

```json
{
  "mcpServers": {
    "whatsapp": {
      "command": "python3",
      "args": ["/absolute/path/to/mcp_servers/whatsapp_mcp/server.py"]
    }
  }
}
```

## Available tools

| Tool | Type | Engine | Description |
|------|------|--------|-------------|
| `get_messages` | Perception | Playwright | Fetch unread WhatsApp messages |
| `find_chat` | Perception | Playwright | Search for a chat by name or phone |
| `open_chat` | Perception | Playwright | Open a chat and read recent messages |
| `mark_read` | Action | Playwright | Mark a chat as read |
| `send_message` | **ACTION** | open-wa | Send a text message — requires approved plan |
| `send_file` | **ACTION** | open-wa | Send a document — requires approved plan |
| `send_image` | **ACTION** | open-wa | Send an image — requires approved plan |
| `reply_to_message` | **ACTION** | open-wa | Send a quoted reply — requires approved plan |
| `healthcheck` | Utility | open-wa | Check the open-wa sidecar/session status |

Two engines are intentionally live side by side as of Step 1: perception
tools stay on the original Playwright `WhatsAppWebClient` (unchanged), while
outbound/health tools were migrated to `OpenWATransport` — an HTTP client to
a local open-wa sidecar — because open-wa ships file/image/reply support
that the Playwright client didn't, without hand-rolling fragile DOM
automation for it. See `backend/api/integrations/whatsapp/` for the
transport interface and `backend/integrations/whatsapp/sidecar/` for the
sidecar itself.

## Architecture

```
Claude CLI
    │
    ├─ get_messages / find_chat / open_chat / mark_read   ← Playwright, unchanged
    │       │
    │       └─ WhatsAppWebClient (Playwright) → WhatsApp Web
    │
    └─ send_message / send_file / send_image /             ← ACTION (requires approved plan)
       reply_to_message / healthcheck
            │
            └─ OpenWATransport (HTTP) → open-wa sidecar → WhatsApp Web
```

**Safety pipeline (Gold Tier):**

```
Watcher (perception) → Intake Wrapper → Plan → Approval → Executor (action)
```

The MCP `send_message` tool must only be called from an approved execution path.
It includes an explicit warning in its tool description.

## Session management

- Session stored at `.secrets/whatsapp_session/` (gitignored) or `~/.personal_ai_employee/whatsapp_session/`
- Persistent Playwright context — no re-pairing needed between restarts
- Lock file at `/tmp/wa_mcp.lock` prevents multiple concurrent server instances

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Not logged in` | Run `python3 scripts/wa_setup.py` to pair |
| `Another server running` | Check `/tmp/wa_mcp.lock`, kill stale process |
| `Playwright not found` | `pip install playwright && playwright install chromium` |
| Session expired | `python3 scripts/wa_setup.py --reset` then re-pair |
| WSL no display | QR mode needs `Xvfb :0 &` + `export DISPLAY=:0`; or use `--phone +12345678901` |

## WSL / Headless notes

The server always runs headless. For initial pairing only, you need a display (for QR) or use phone number pairing:

```bash
# Phone number pairing (headless-friendly)
python3 scripts/wa_setup.py --phone +12345678901 --headless
```
