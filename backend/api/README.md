# AI Operator — Backend API

FastAPI application providing the backend for the AI Operator dashboard.

## Starting the server

```bash
cd backend
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive docs: http://localhost:8000/docs

## Endpoints

All routes are prefixed with `/api/v1`.

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness check — returns `{"status": "ok"}` |
| GET | `/api/v1/status` | Detailed system status — Python version, dirs, MCP servers |

### Commands

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/commands` | Submit a natural language command |
| GET | `/api/v1/commands` | List recent commands (newest first) |
| GET | `/api/v1/commands/{id}` | Get a single command by ID |

**Note:** Commands are stored in-memory only. Restart clears history. Agent routing is wired at startup but execution is async — status will show `queued` initially.

### Approvals

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/approvals` | List approval items (filter: `?status=pending\|approved\|rejected\|all`) |
| GET | `/api/v1/approvals/{id}` | Get a single approval item |
| POST | `/api/v1/approvals/{id}/approve` | Approve — moves file to `Approved/` |
| POST | `/api/v1/approvals/{id}/reject` | Reject — moves file to `Rejected/` |

Reads from: `backend/src/ai_operator/skills/gold/Pending_Approval/`

### Activity

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/activity` | List activity events (params: `limit`, `days`) |
| GET | `/api/v1/activity/{id}` | Get a single event by derived ID |

Reads from: `Logs/YYYY-MM-DD.json` (newline-delimited JSON).

### Integrations

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/integrations` | List all integrations with status |
| GET | `/api/v1/integrations/{id}` | Get single integration status |

Status is derived from credential files in `.secrets/`.

### Workflows

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/workflows` | List workflows (filter: `?status=all\|active\|completed\|failed`) |
| GET | `/api/v1/workflows/{id}` | Get single workflow |

In v1, workflows are derived from command history.

## Response format

All endpoints return a consistent envelope:

```json
{
  "success": true,
  "data": <payload>,
  "message": "optional message"
}
```

## Configuration

Copy `backend/.env.example` to `backend/.env` and fill in:

```
OPENAI_API_KEY=sk-...
API_PORT=8000
CORS_ORIGINS=http://localhost:3000
```
