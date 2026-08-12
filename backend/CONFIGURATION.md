# Configuration

Audited 2026-07 (platform-stabilization pass). Xyron has **two parallel
configuration mechanisms** — this is itself the main finding of this
audit, documented rather than silently worked around:

1. **`api/config.py`'s `Settings` class** (pydantic-settings) — ~10 fields,
   loaded once from `backend/.env`, accessed as `settings.<field>`.
2. **~48 scattered `os.getenv()` calls** across `voice/`, `api/services/`,
   `api/routers/`, each with its own inline default and no central
   registry — found by grepping the whole tree for
   `os.(getenv|environ.get)`.

Neither is wrong on its own (env-var-with-inline-default is a completely
normal pattern), but having two mechanisms with no documented boundary
between "goes in Settings" vs "reads its own env var" makes it hard to
answer "what can I configure?" without grepping the whole codebase — which
is what this document is for.

## Settings class (`api/config.py`) — the canonical, typed configuration

| Field | Default | Purpose |
|---|---|---|
| `api_host` | `0.0.0.0` | bind address |
| `api_port` | `8000` | bind port |
| `debug` | `True` | debug mode |
| `cors_origins` | localhost:3000/3001/5173/4173/5174/1420 + Tauri origins | allowed frontend origins |
| `repo_root` | auto-detected (walks up for README.md + backend/) | absolute repo path — makes CWD irrelevant |
| `openai_api_key` | `""` | required for LLM features; empty = degraded mode, not a crash |
| `onnx_provider` | `""` (kokoro_onnx decides) | e.g. `CUDAExecutionProvider` for GPU TTS |
| `enable_rvc` | `False` | RVC voice conversion toggle |
| `rvc_model_dir` | `""` → `~/.xyron/models/rvc` | RVC model storage |
| `rvc_default_preset` | `neutral` | |
| `rvc_device` | `auto` | `cuda`/`cpu`/`auto` |
| `rvc_max_latency_ms` | `250` | RVC latency budget |
| `rvc_lightweight` | `False` | force lightweight tier |

Plus five computed path properties (`logs_dir`, `pending_approval_dir`,
`approved_dir`, `rejected_dir`, `secrets_dir`, `mcp_servers_dir`) derived
from `repo_root` — not independently configurable, listed here because
they're effectively part of the configuration surface.

## Scattered environment variables, grouped by subsystem

**Voice — wake word** (`voice/wake_word_service.py`)
- `WAKE_MODELS_DIR` (default `~/.xyron/wake_models`)
- `WAKE_WORD_MODEL` (default `hey_jarvis`)
- `WAKE_WORD_THRESHOLD` (default `0.50`)
- `WAKE_COOLDOWN_S` (default `2.0`)
- `WAKE_THRESHOLDS` (JSON, default `{}` — per-model threshold overrides)

**Voice — STT** (`voice/whisper_service.py`)
- `WHISPER_MODEL` (default `small` — accurate pass)
- `WHISPER_FAST_MODEL` (default `tiny.en` — fast first pass)
- `WHISPER_LANGUAGE` (default `auto`)
- `WHISPER_CONFIDENCE_THRESHOLD` (default `-1.0`, i.e. disabled)

**Voice — response behavior**
- `XYRON_IMMEDIATE_ACK_ENABLED` (default `false`) — early filler response
- `RESPONSE_LANGUAGE_MODE` (default `auto`)
- `MULTILINGUAL_TTS_PRELOAD` — gates XTTS-v2 preload (~2GB VRAM), off by
  default per its own inline comment in `main.py`

**LLM rate limiting** (`api/services/openai_client.py`)
- `XYRON_MAX_GPT4O_PER_HOUR`, `XYRON_MAX_MINI_PER_HOUR` — hourly call caps,
  defaults defined as named constants in the same file (not inlined),
  the one example of a scattered-var default that's already
  well-factored.

**Perception / screen context**
- `SCREEN_CONTEXT_ENABLED` (default `false` — costs real GPT-4o-mini
  vision API money, opt-in)
- `SCREEN_CONTEXT_INTERVAL` (default `300` seconds)

**Filesystem intelligence**
- `FS_SCAN_ROOTS` (default: auto-discovered drives + Windows home) —
  comma-separated override for `fs_index.py`'s scan roots.

**Browser / CDP** (`api/services/cdp_config.py`)
- `XYRON_CDP_LOCAL_PORT` (default `9222` — Chrome's own debug port)
- `XYRON_CDP_BRIDGE_PORT` (default `9223`, tries `9223-9230`) — separate
  from `9222` specifically because Chrome and the WSL2 `portproxy` bridge
  fight over that port if both try to use it.
- `XYRON_CDP_PROFILE_DIR`

**Integrations** — `ODOO_BASE_URL`/`ODOO_DATABASE`/`ODOO_USERNAME`/
`ODOO_PASSWORD`/`ODOO_API_VERSION`, `GMAIL_OAUTH_CREDENTIALS`,
`WHATSAPP_PHONE`/`WHATSAPP_HEADLESS`/`WHATSAPP_SESSION_DIR`,
`OLLAMA_API_URL`/`OLLAMA_MODEL` — self-explanatory per-integration
credentials/endpoints, one group per external service.

**Misc** — `ONNX_PROVIDER` (also settable via `Settings`, read raw in
`main.py:30` before Settings loads — see note below), `OPERATOR_MODE`
(referenced under `operator_mode`/`src/ai_operator`, **not** currently
gating anything reachable from `api/` — see TECHNICAL_DEBT.md),
`LOCAL_ONLY_MODE` (skips network calls in `intent_router.py`'s Tier 3
classifier load), `XTTS_MODEL`, `XYRON_BACKEND_URL`/`XYRON_API_BASE`
(frontend-facing).

## Known duplication

`ONNX_PROVIDER` is read twice: once raw via `os.environ.get` in `main.py`
(before the `Settings` object is guaranteed initialized, to propagate into
`kokoro_onnx`'s environment before any model loads) and once as
`settings.onnx_provider`. This is a deliberate ordering workaround
(documented inline in `main.py`), not an oversight — noted here so it isn't
"fixed" into a subtle startup-ordering bug later.

## Recommendation (not executed this pass — see TECHNICAL_DEBT.md)

Migrate the scattered `os.getenv()` calls into `Settings` fields with
`Field(default=..., description=...)`, grouped by the sections above. This
would give a single source of truth, IDE autocomplete, and validation
(e.g. `WAKE_WORD_THRESHOLD` as a bounded float instead of a raw string
parse). Estimated effort: medium — ~48 call sites across ~15 files, each
individually low-risk (same default, same env var name, just centralized)
but the volume makes it a dedicated pass, not a drive-by fix.
