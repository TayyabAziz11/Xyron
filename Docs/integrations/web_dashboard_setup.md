# Personal AI Employee — Web Dashboard Setup

Command center web app built with Next.js 14 + Neon Postgres + NextAuth (GitHub).

---

## Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Neon Postgres Setup](#2-neon-postgres-setup)
3. [GitHub OAuth App Setup](#3-github-oauth-app-setup)
4. [Local Development](#4-local-development)
5. [Prisma Migrations](#5-prisma-migrations)
6. [Vercel Deployment](#6-vercel-deployment)
7. [Environment Variables Reference](#7-environment-variables-reference)
8. [Features](#8-features)

---

## 1. Architecture Overview

```
apps/web/                   ← Next.js 14 App Router
├── prisma/schema.prisma    ← Neon Postgres schema (Prisma ORM)
├── src/
│   ├── app/                ← Pages + API routes
│   │   ├── page.tsx        ← Public marketing site (/)
│   │   ├── login/          ← GitHub sign-in (/login)
│   │   ├── app/            ← Protected dashboard (/app/*)
│   │   └── api/            ← REST endpoints
│   ├── components/         ← UI components (shadcn-style)
│   ├── lib/
│   │   ├── auth.ts         ← NextAuth configuration
│   │   ├── db.ts           ← Prisma client singleton
│   │   ├── fs-reader.ts    ← Reads vault files from repo root
│   │   └── sync.ts         ← FS → Postgres sync service
│   └── middleware.ts       ← Protects /app/* routes
```

**Data flow:**
- Filesystem vault (Plans/, Needs_Action/, Logs/) ← bridge → Neon Postgres ← bridge → Next.js UI
- `POST /api/sync` triggers the bridge sync
- `POST /api/run/daily-cycle` spawns Python scripts (local only)

---

## 2. Neon Postgres Setup

**Free tier is more than enough for this project.**

### Steps:
1. Go to **[https://console.neon.tech](https://console.neon.tech)** and sign up (free, no credit card)
2. Click **"New Project"**
   - Name: `personal-ai-employee`
   - Region: `us-east-2` (or closest to you)
   - Postgres version: 16
3. Click **"Create Project"**
4. In the dashboard, go to **"Connection Details"**
5. Select **"Prisma"** from the connection string dropdown
6. Copy the `DATABASE_URL` — looks like:
   ```
   postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
7. Paste it into `apps/web/.env.local` as `DATABASE_URL`

> **Neon free tier:** 0.5 GB storage, unlimited API calls, 1 compute unit — plenty for MVP.

---

## 3. GitHub OAuth App Setup

1. Go to **[https://github.com/settings/developers](https://github.com/settings/developers)**
2. Click **"OAuth Apps"** → **"New OAuth App"**
3. Fill in:
   - **Application name:** `Personal AI Employee (local)` (or `(prod)` for Vercel)
   - **Homepage URL:** `http://localhost:3000` (or your Vercel URL)
   - **Authorization callback URL:** `http://localhost:3000/api/auth/callback/github`
4. Click **"Register application"**
5. Copy **Client ID** → `GITHUB_CLIENT_ID`
6. Click **"Generate a new client secret"** → copy → `GITHUB_CLIENT_SECRET`

> For Vercel: create a **second** OAuth App with your production URL (e.g., `https://yourapp.vercel.app`).

---

## 4. Local Development

### Prerequisites
- Node.js 18+
- Python 3.10+ (for running agent scripts)
- npm or pnpm

### Setup

```bash
# 1. Navigate to web app
cd apps/web

# 2. Install dependencies
npm install

# 3. Copy env template
cp .env.local.example .env.local
# → Edit .env.local with your actual values

# 4. Generate Prisma client
npm run db:generate

# 5. Push schema to Neon (creates tables)
npm run db:push

# 6. Start dev server
npm run dev
```

Visit **[http://localhost:3000](http://localhost:3000)**

### Setting REPO_ROOT

For the sync button to read vault files, set:
```bash
# .env.local
REPO_ROOT="/mnt/e/Certified Cloud Native.../Personal AI Employee"
```

> On Windows/WSL use the WSL path. On Mac/Linux use the absolute path.

---

## 5. Prisma Migrations

```bash
cd apps/web

# Push schema changes (development — no migration file)
npm run db:push

# Create a named migration (for production)
npm run db:migrate --name init

# Open Prisma Studio (visual DB browser)
npm run db:studio
```

### Schema models:
| Model | Purpose |
|-------|---------|
| `User` | NextAuth user (GitHub profile) |
| `Account`, `Session`, `VerificationToken` | NextAuth adapter tables |
| `Connection` | Watcher health (linkedin, gmail, etc.) |
| `Run` | Daily cycle run records |
| `EventLog` | Timeline events from logs |
| `InboxItem` | Normalized inbox (from vault + watcher logs) |
| `Plan` | Plans from Plans/ directory |

---

## 6. Vercel Deployment

### One-click deploy

1. Push your repo to GitHub
2. Go to **[https://vercel.com/new](https://vercel.com/new)**
3. Import your GitHub repo
4. Set **Root Directory** to `apps/web`
5. Framework preset: **Next.js** (auto-detected)

### Set environment variables on Vercel:
Go to **Project → Settings → Environment Variables** and add:

| Key | Value |
|-----|-------|
| `DATABASE_URL` | Your Neon connection string |
| `NEXTAUTH_SECRET` | `openssl rand -base64 32` output |
| `NEXTAUTH_URL` | `https://your-project.vercel.app` |
| `GITHUB_CLIENT_ID` | From your production OAuth app |
| `GITHUB_CLIENT_SECRET` | From your production OAuth app |

> **Do NOT set `REPO_ROOT` on Vercel** — filesystem access won't work in serverless. The UI will show data from the DB (synced when running locally).

### After deploy:
```bash
# Run DB push against Neon (from local)
DATABASE_URL="your-neon-url" npm run db:push
```

> **Free Vercel tier:** 100 GB-hours serverless functions/month, 100 GB bandwidth — plenty for this project.

---

## 7. Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✅ Yes | Neon Postgres connection string |
| `NEXTAUTH_SECRET` | ✅ Yes | Random secret for JWT signing (min 32 chars) |
| `NEXTAUTH_URL` | ✅ Yes | Base URL of the app (http://localhost:3000 in dev) |
| `GITHUB_CLIENT_ID` | ✅ Yes | GitHub OAuth App Client ID |
| `GITHUB_CLIENT_SECRET` | ✅ Yes | GitHub OAuth App Client Secret |
| `REPO_ROOT` | ⚡ Local only | Absolute path to repo root for vault file sync |
| `DISABLE_PYTHON_EXEC` | Optional | Set to `true` to disable Python script spawning |

Generate `NEXTAUTH_SECRET`:
```bash
openssl rand -base64 32
```

---

## 8. Features

### Public (`/`)
- Marketing landing page with "How It Works" 5-step flow
- Channel badges (Gmail, LinkedIn, Odoo, Twitter, WhatsApp)
- Safety principles section
- CTA → GitHub sign-in

### Protected (`/app/*`)
Requires GitHub authentication.

| Route | Description |
|-------|-------------|
| `/app` | Command Center dashboard |
| `/app/inbox` | Normalized inbox with source filters |
| `/app/plans` | Plan status tracker |
| `/app/logs` | Full event timeline |
| `/app/settings` | Connections + env readiness |

### API Routes

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/[...nextauth]` | GET/POST | NextAuth handler |
| `/api/sync` | POST | Sync filesystem vault → Neon Postgres |
| `/api/run/daily-cycle?mode=dry_run\|execute` | POST | Spawn daily cycle Python scripts |
| `/api/status` | GET | Watcher health + last run |
| `/api/inbox` | GET | Paginated inbox items |
| `/api/plans` | GET | Paginated plans |
| `/api/logs` | GET | Paginated event logs |

### Dashboard widgets
- **Agent Status cards** — live watcher health (LinkedIn, Gmail, Odoo, Twitter, WhatsApp)
- **Needs Action** — file counts from Needs_Action/ + Pending_Approval/
- **Inbox Summary** — unread counts by source
- **Last Run** — most recent daily cycle status + timing
- **Event Timeline** — scrollable feed of agent events
- **Run Controls** — Dry-Run and Execute buttons with confirmation modal
- **Sync Button** — triggers FS → DB sync

### Sync workflow (local dev)
1. Run `npm run dev` with `REPO_ROOT` set
2. Click **"Sync Now"** in the dashboard or settings
3. Calls `POST /api/sync` which reads vault files and upserts into Neon
4. Dashboard refreshes to show synced data

### Python script execution (local only)
The `POST /api/run/daily-cycle` endpoint:
- Disabled on Vercel (`process.env.VERCEL` check)
- Runs scripts sequentially from `scripts/` directory
- Captures stdout/stderr per script
- Saves results to `Run` + `EventLog` tables
- Returns run ID immediately; execution continues in background

---

## Troubleshooting

**"PrismaClientInitializationError"** → Check `DATABASE_URL` is set and Neon project is active.

**"GitHub OAuth callback mismatch"** → Ensure the callback URL in GitHub exactly matches `NEXTAUTH_URL + /api/auth/callback/github`.

**"Sync returns 0 items"** → Check `REPO_ROOT` points to the actual repo root (where `Plans/`, `Logs/` etc. live).

**"Python execution disabled"** → Set `DISABLE_PYTHON_EXEC=false` and ensure `REPO_ROOT` is set locally.

**Vercel build fails** → Ensure `prisma generate` runs via `postinstall` script in `package.json`. ✅ Already configured.
