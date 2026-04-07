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

| Tool | Type | Description |
|------|------|-------------|
| `get_messages` | Perception | Fetch unread WhatsApp messages |
| `find_chat` | Perception | Search for a chat by name or phone |
| `open_chat` | Perception | Open a chat and read recent messages |
| `mark_read` | Action | Mark a chat as read |
| `send_message` | **ACTION** | Send a message — requires approved plan |
| `healthcheck` | Utility | Check WhatsApp Web connection status |

## Architecture

```
Claude CLI
    │
    ├─ get_messages / find_chat / open_chat   ← PERCEPTION-ONLY
    │       │
    │       └─ WhatsAppWebClient (Playwright) → WhatsApp Web
    │
    └─ send_message                           ← ACTION (requires approved plan)
            │
            └─ WhatsAppWebClient.send_message() → WhatsApp Web
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
