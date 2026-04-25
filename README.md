# Xyron — Voice-Driven AI Assistant

> Talk naturally. It does the work.

---

## What is Xyron?

Imagine having a super-smart assistant that lives on your computer and listens to your voice. You say something like **"open my IT Course folder"** or **"set volume to 50 and play that video"** — and it just does it. No clicking. No searching. Just talk.

That's Xyron.

It's a **voice-first AI assistant for your Windows PC** (running through WSL2). You speak a command, it figures out what you mean, runs the right action, and talks back to you with the result.

Here's what it can do out of the box:

- Open files, folders, apps, and websites by name
- Control your volume and screen brightness
- Search the web and answer questions
- Read your Gmail inbox
- Take screenshots
- Remember what you asked before (so you can say "play **that** video" and it knows what you mean)
- Remind you about upcoming calendar events
- Run compound commands like **"open Chrome and then set volume to 30"**
- Detect wake words like **"hey Xyron"** so you don't have to press anything

---

## How It's Built (The Three Pieces)

```
Xyron/
  backend/       ← The brain (Python + FastAPI)
  web/           ← The dashboard you see in your browser (Next.js)
  desktop-app/   ← The Electron tray app on Windows/WSL2
```

| Piece | What it does | Tech |
|---|---|---|
| **Backend** | Processes your voice, routes commands, calls tools | Python 3.10+, FastAPI |
| **Web Dashboard** | Shows history, stats, approvals, command center | Next.js, TypeScript, Tailwind |
| **Desktop App** | Puts Xyron in your system tray (always accessible) | Electron |

The backend does the heavy lifting. The web and desktop apps are the faces you interact with.

---

## Before You Start — What You Need

Make sure you have these installed:

| Tool | Why | Check if installed |
|---|---|---|
| **Python 3.10+** | Runs the backend | `python3 --version` |
| **Node.js 18+** | Runs the web dashboard | `node --version` |
| **npm** | Installs web packages | `npm --version` |
| **Git** | Downloads the code | `git --version` |
| **WSL2** (Windows only) | Runs Linux on Windows | You're in WSL2 if this path works: `/mnt/c/` |

You'll also need a free **OpenAI API key** from [platform.openai.com](https://platform.openai.com) — this powers the voice understanding and AI responses.

---

## Setup: Step by Step

### Step 1 — Clone the Repository

Open a terminal and run:

```bash
git clone https://github.com/YOUR_USERNAME/Xyron.git
cd Xyron
```

> Replace `YOUR_USERNAME` with the actual GitHub username where the repo lives.

---

### Step 2 — Set Up the Backend (The Brain)

```bash
cd backend
```

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

> This installs everything the backend needs — the voice pipeline, AI models, tool handlers, etc. It might take a few minutes on the first run because it downloads some AI models.

Now create your environment file (this tells the backend your API key and settings):

```bash
cp .env.example .env
```

Open the `.env` file and fill in your real values:

```env
OPENAI_API_KEY=sk-proj-YOUR_KEY_HERE
API_PORT=8000
CORS_ORIGINS=http://localhost:3000,http://localhost:3001
```

> **Important:** Never share your `.env` file or commit it to GitHub. It contains your private API key.

Start the backend server:

```bash
python3 -m uvicorn api.main:app --reload --port 8000
```

You should see something like:

```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
```

Leave this terminal open — the backend needs to keep running.

---

### Step 3 — Set Up the Web Dashboard

Open a **new terminal tab/window** and run:

```bash
cd Xyron/web
npm install
npm run dev
```

The web dashboard starts at **http://localhost:3001**

---

### Step 4 — (Optional) Set Up the Desktop App

The Electron tray app lets you use Xyron without opening a browser.

```bash
cd Xyron/desktop-app
npm install
npm run dev:wsl       # use this if you're on WSL2
# OR
npm run dev           # use this on native Linux/Mac
```

---

## All the URLs

Once both the backend and web are running:

| What | URL |
|---|---|
| Homepage | http://localhost:3001 |
| Command Center (voice) | http://localhost:3001/app/command-center |
| Dashboard | http://localhost:3001/app/dashboard |
| Conversation History | http://localhost:3001/app/history |
| Usage Stats | http://localhost:3001/app/stats |
| Approvals Queue | http://localhost:3001/app/approvals |
| Activity Log | http://localhost:3001/app/activity |
| Integrations | http://localhost:3001/app/integrations |
| Settings | http://localhost:3001/app/settings |
| Backend API | http://localhost:8000 |
| API Docs (auto-generated) | http://localhost:8000/docs |

---

## How to Use It

1. Go to **http://localhost:3001/app/command-center**
2. Click the microphone button (or say **"hey Xyron"** if wake word is on)
3. Speak your command
4. Xyron thinks, acts, and responds

**Example commands to try:**
- `"Open Chrome"`
- `"What's my battery level?"`
- `"Set volume to 40"`
- `"Open my downloads folder"`
- `"Take a screenshot"`
- `"Open Chrome and set volume to 50"` ← runs two things at once

---

## Project Structure (For the Curious)

```
Xyron/
  backend/
    api/
      main.py              ← FastAPI app entry point
      routers/             ← API endpoints (voice, history, memory, etc.)
      services/            ← Core logic (intent routing, episodic memory, etc.)
      tools/               ← Things Xyron can actually DO (open files, etc.)
    voice/                 ← Voice recording and text-to-speech
    requirements.txt       ← Python dependencies
    .env.example           ← Template for your secrets

  web/
    src/
      app/                 ← Next.js pages
      components/          ← Reusable UI pieces
      hooks/               ← Data-fetching logic
      lib/                 ← Shared utilities

  desktop-app/
    src/                   ← Electron main + renderer process

  shared/                  ← Types shared between frontend and backend
  docs/                    ← Integration setup guides
```

---

## Common Issues

**Backend won't start?**
- Make sure you're in the `backend/` folder
- Check that your `.env` file exists and has a valid `OPENAI_API_KEY`
- Try: `python3 --version` — needs to be 3.10 or higher

**Microphone not working in browser?**
- Use **Chrome or Edge** (Firefox has limited mic support)
- Make sure you clicked "Allow" when the browser asked for mic permission

**Voice commands not understood?**
- Speak clearly and don't start talking until the mic indicator turns red/active
- Try simpler commands first to test the connection

**`npm install` fails?**
- Run `node --version` — needs to be 18 or higher
- Try: `npm install --legacy-peer-deps`

---

## License

See [LICENSE](./LICENSE).
