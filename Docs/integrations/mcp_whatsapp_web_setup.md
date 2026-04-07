# WhatsApp Web MCP Setup Guide

> Uses Playwright + WhatsApp Web automation — no WhatsApp Business API / Cloud API required.

## Prerequisites

- Python 3.10+
- Playwright (`pip install playwright`)
- Chromium (`playwright install chromium`)
- System libraries (WSL): `playwright install-deps chromium`

## One-time pairing

### Option A: QR code (headed mode)

```bash
# WSL: start a virtual display first
Xvfb :0 -screen 0 1280x720x24 &
export DISPLAY=:0

python3 scripts/wa_setup.py
# Browser opens → scan QR with WhatsApp on your phone
# WhatsApp → Linked Devices → Link a Device → Scan QR
```

### Option B: Phone number (headless-friendly)

```bash
python3 scripts/wa_setup.py --phone +12345678901 --headless
# A pairing code appears in the terminal
# WhatsApp → Linked Devices → Link a Device → enter code
```

### Check status

```bash
python3 scripts/wa_setup.py --status
```

### Reset / re-pair

```bash
python3 scripts/wa_setup.py --reset
python3 scripts/wa_setup.py        # then re-pair
```

## Session storage

Session files are stored at (first found):

1. `.secrets/whatsapp_session/` — if `.secrets/` directory exists (gitignored)
2. `~/.personal_ai_employee/whatsapp_session/` — default fallback

Session status metadata: `.secrets/whatsapp_session_meta.json`

Both paths are in `.gitignore`. **Never commit session files** — they contain authentication cookies.

## MCP server

```bash
python3 mcp_servers/whatsapp_mcp/server.py
```

Or register in `~/.claude/mcp_config.json` for automatic startup with Claude CLI:

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

## Watcher (perception-only)

```bash
# Mock mode (development)
python3 src/personal_ai_employee/skills/gold/whatsapp_watcher_skill.py --mode mock

# Real mode (requires paired session)
python3 src/personal_ai_employee/skills/gold/whatsapp_watcher_skill.py --mode real

# Options
--max-results 20       # Max messages to process
--dry-run              # Show what would be created
--reset-checkpoint     # Clear processed IDs
```

Intake wrappers are created at `Social/Inbox/inbox__whatsapp__*.md`.

## Web dashboard

After pairing, the WhatsApp section in the Command Center (`/app/command-center/whatsapp`) shows:

- Session status (active / not paired)
- Intake wrappers from `Social/Inbox/`
- **Fetch New** button — runs the watcher in real mode to pull new messages
- **Send Message** — opens the approval wizard

## Gold-tier safety pipeline

```
1. Watcher (perception-only) reads unread messages
         ↓
2. Intake wrapper created in Social/Inbox/
         ↓
3. Plan created via wizard or AI planning
         ↓
4. Human approves plan in dashboard (Approvals tab)
         ↓
5. Executor sends message via WhatsAppWebClient
```

**The watcher NEVER sends messages.** Send actions always require an approved plan.

## Troubleshooting

| Symptom | Solution |
|---------|----------|
| `WhatsApp not logged in` | Run `python3 scripts/wa_setup.py` |
| Session expired after ~2 weeks | Run `--reset` then re-pair |
| `playwright._impl._errors.Error: BrowserType.launch_persistent_context` | Run `playwright install-deps chromium` |
| QR never appears in WSL | Start Xvfb: `Xvfb :0 &` + `export DISPLAY=:0` |
| Selectors fail (UI updated) | WhatsApp Web periodically changes `data-testid`; update `SEL` dict in `whatsapp_web_helper.py` |
| Lock file conflict | `rm /tmp/wa_mcp.lock` if no server is running |
