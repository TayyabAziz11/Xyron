# Xyron — Complete Setup Guide

> Get Xyron running from scratch on any Windows/WSL2 machine.
> Follow every step in order. Don't skip sections.
> Last updated: 2026-05-27

---

## Already Running? — Pull Latest Updates

```bash
cd Xyron
git pull origin main
```

Restart the backend:

```bash
cd backend
PYTHONPATH=/mnt/e/Xyron/backend python3 -m uvicorn api.main:app --reload --port 8000
```

No reinstall needed unless you see a new package in `requirements.txt`.

---

## Architecture at a Glance

```
┌──────────────────────────────────────────────────────┐
│   Desktop App (Tauri + React + Vite)                 │
│   desktop-app/   — runs natively on Windows          │
│   Wake word → Voice session → Tool execution         │
└─────────────────┬────────────────────────────────────┘
                  │  HTTP + WebSocket (localhost:8000)
┌─────────────────▼────────────────────────────────────┐
│   Backend (FastAPI — Python)                         │
│   backend/api/   — 19+ routers                       │
│   Voice pipeline: Whisper STT → Intent Router →      │
│   Orchestrator → Tool Registry → Kokoro TTS          │
└──────────────────────────────────────────────────────┘
│   Web Dashboard (Next.js — optional)                 │
│   web/   — runs on localhost:3001                    │
└──────────────────────────────────────────────────────┘
```

Three independent layers. All talk through HTTP only. The desktop app is the primary UI.

---

## Full Setup Checklist (fresh machine)

- [ ] Step 1 — System dependencies (apt, Node.js)
- [ ] Step 2 — Clone repo
- [ ] Step 3 — Python packages (core + voice)
- [ ] Step 4 — GPU setup (WSL2 + NVIDIA only)
- [ ] Step 5 — Environment file (`.env`)
- [ ] Step 6 — Download Kokoro TTS model
- [ ] Step 7 — Wake word models
- [ ] Step 8 — Desktop app (Tauri)
- [ ] Step 9 — Optional: Web dashboard
- [ ] Step 10 — Start everything and verify

---

## Step 1 — System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 python3-pip \
  espeak-ng ffmpeg \
  curl wget git
```

Install Node.js 20 (required for desktop app and web dashboard):

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

Install Rust (required for the Tauri desktop app):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
```

Verify everything:

```bash
python3 --version     # 3.10+
node --version        # 20+
cargo --version       # 1.70+
espeak-ng --version   # any
ffmpeg -version       # any
```

---

## Step 2 — Clone the Repo

```bash
git clone https://github.com/TayyabAziz11/Xyron.git
cd Xyron
```

> Packages are installed **system-wide** (no virtualenv). Do NOT create a `.venv`
> — it will break the import paths that expect system Python.

---

## Step 3 — Python Packages

### 3a — Core packages

```bash
cd Xyron/backend
pip install -r requirements.txt
pip install openwakeword
playwright install chromium
```

`requirements.txt` includes: `fastapi`, `uvicorn`, `openai`, `sentence-transformers`,
`faster-whisper`, `psutil`, `rapidfuzz`, `pydantic-settings`, and all backend deps.

### 3b — Voice pipeline packages (install manually)

```bash
pip install kokoro-onnx edge-tts
pip install librosa scipy soundfile sounddevice
pip install webrtcvad-wheels
pip install huggingface-hub
```

### 3c — RVC voice conversion (optional)

> RVC is disabled for live streaming (`ENABLE_RVC=false`). Only needed for offline voice tests.

```bash
pip install rvc-python --no-deps
pip install "faiss-cpu>=1.9.0" praat-parselmouth pyworld resampy torchcrepe
```

### 3d — Verify voice packages

```bash
python3 -c "
import kokoro_onnx; print('kokoro-onnx OK')
import librosa;     print('librosa OK', librosa.__version__)
import scipy;       print('scipy OK')
import rapidfuzz;   print('rapidfuzz OK', rapidfuzz.__version__)
import faster_whisper; print('faster-whisper OK')
"
```

---

## Step 4 — GPU Setup (WSL2 + NVIDIA only)

Skip if you have no GPU — Xyron falls back to CPU automatically.

### 4a — Register CUDA libraries

```bash
echo "/usr/lib/wsl/lib" | sudo tee /etc/ld.so.conf.d/wsl-cuda.conf
sudo ldconfig
ldconfig -p | grep libcuda   # should print at least one line
```

### 4b — Install cuBLAS

```bash
ldconfig -p | grep libcublas
```

If `libcublas.so.12` is not listed:

```bash
sudo apt-get install -y libcublas-12-0
sudo ldconfig
```

### 4c — ONNX Runtime GPU

```bash
pip uninstall onnxruntime -y
pip install onnxruntime-gpu
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
# Must include: CUDAExecutionProvider
```

### 4d — PyTorch + CUDA

```bash
pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# True  NVIDIA <your GPU>
```

> Change `cu121` if your CUDA version differs (`nvidia-smi` shows your CUDA version).

---

## Step 5 — Environment File

Create `backend/.env`:

```env
# ── Required ──────────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...              # platform.openai.com

# ── Auth (Clerk) ──────────────────────────────────────────────────────────────
CLERK_SECRET_KEY=sk_live_...       # Clerk dashboard → API Keys
CLERK_PUBLISHABLE_KEY=pk_live_...  # Clerk dashboard → API Keys

# ── Server ────────────────────────────────────────────────────────────────────
API_PORT=8000

# ── Voice / TTS ───────────────────────────────────────────────────────────────
ONNX_PROVIDER=CUDAExecutionProvider   # CPUExecutionProvider if no GPU
WHISPER_MODEL=medium
WHISPER_LANGUAGE=auto

# ── Wake word ─────────────────────────────────────────────────────────────────
WAKE_WORD_MODEL=hey_jarvis
WAKE_WORD_THRESHOLD=0.5
WAKE_COOLDOWN_S=3.0

# ── RVC voice conversion ──────────────────────────────────────────────────────
ENABLE_RVC=false
RVC_MODEL_DIR=~/.xyron/models/rvc
RVC_DEFAULT_PRESET=neutral
RVC_DEVICE=auto

# ── Filesystem index ──────────────────────────────────────────────────────────
# FS_SCAN_ROOTS=/mnt/d,/mnt/e   # override if auto-detection misses drives

# ── Screen context (costs ~$0.10/day) ─────────────────────────────────────────
SCREEN_CONTEXT_ENABLED=false
SCREEN_CONTEXT_INTERVAL=300

# ── Cost caps ─────────────────────────────────────────────────────────────────
XYRON_MAX_GPT4O_PER_HOUR=0
XYRON_MAX_MINI_PER_HOUR=200

# ── Optional ──────────────────────────────────────────────────────────────────
# HF_TOKEN=hf_...
# LOCAL_ONLY_MODE=true   # skips HuggingFace sentence-transformer download
# OLLAMA_API_URL=http://localhost:11434/api/generate
```

> **No GPU?** Change `ONNX_PROVIDER=CPUExecutionProvider`

---

## Step 6 — Kokoro TTS Model

Download once (~100 MB), cached forever:

```bash
python3 -c "
import os, shutil
from huggingface_hub import hf_hub_download
model  = hf_hub_download('hexgrad/Kokoro-82M-ONNX', 'kokoro-v1.0.onnx')
voices = hf_hub_download('hexgrad/Kokoro-82M-ONNX', 'voices-v1.0.bin')
dest   = os.path.expanduser('~/.xyron/models')
os.makedirs(dest, exist_ok=True)
shutil.copy(model, dest)
shutil.copy(voices, dest)
print('Kokoro ready:', os.listdir(dest))
"
```

Expected: `Kokoro ready: ['kokoro-v1.0.onnx', 'voices-v1.0.bin']`

Whisper STT (~500 MB) auto-downloads on first voice use — no manual step.

---

## Step 7 — Wake Word Models

### Default: "Hey Jarvis" (auto-downloaded)

Say **"Hey Jarvis"** out of the box. No setup needed.

### Custom "Hey Xyron" (get from Tayyab)

Place `.onnx` files in `~/.xyron/wake_models/`:

```bash
mkdir -p ~/.xyron/wake_models
# Tayyab sends the files — copy them here:
cp /path/to/wake_models/*.onnx ~/.xyron/wake_models/
```

Backend will log: `[WakeWord] Ready — models: hey_xyron(0.72) hey_jarvis(0.80)`

---

## Step 8 — Desktop App (Tauri)

The desktop app is a **Tauri** app (Rust backend + React frontend). It runs natively on Windows.

### Install Tauri system dependencies

On Windows, open **PowerShell as administrator**:

```powershell
# Install Visual Studio Build Tools (required for Rust/Tauri)
winget install Microsoft.VisualStudio.2022.BuildTools
# Install WebView2 (usually already present on Windows 11)
winget install Microsoft.EdgeWebView2Runtime
```

### Install Node packages and run

In WSL2 terminal:

```bash
cd Xyron/desktop-app
npm install
npm run dev:wsl      # WSL2 (sets PULSE_SERVER for audio)
# or:
npm run dev          # native Linux/Mac
```

> `dev:wsl` sets `PULSE_SERVER=unix:/mnt/wslg/PulseServer` for WSL2 audio.

### What the desktop app includes

- **Wake word** — always listening in background
- **Voice session** — full duplex voice with Xyron
- **Dashboard** — real-time CPU/RAM/GPU/Network charts
- **Command center** — text commands
- **Settings** — voice, appearance, behavior config
- **Activity timeline** — history of everything Xyron did
- **Auth** — Clerk-based login (required)

---

## Step 9 — Web Dashboard (optional)

```bash
cd Xyron/web
npm install
npm run dev   # runs on http://localhost:3001
```

The web dashboard mirrors the desktop app but runs in a browser. Desktop app is primary.

---

## Step 10 — Start Everything and Verify

**Terminal 1 — Backend:**

```bash
cd Xyron/backend
PYTHONPATH=/mnt/e/Xyron/backend python3 -m uvicorn api.main:app --reload --port 8000
```

> Replace `/mnt/e/Xyron` with your actual clone path.

**Terminal 2 — Desktop app:**

```bash
cd Xyron/desktop-app
npm run dev:wsl
```

**Health check:**

```bash
curl http://localhost:8000/api/v1/health
```

**Expected backend logs on clean startup:**

```
[DRIVE_DISCOVERY] scanning /mnt/ for mounted drives
[DRIVE_FOUND] drive=D path=/mnt/d
[DRIVE_FOUND] drive=E path=/mnt/e
fs_index: background worker thread started
[Whisper] GPU detected: NVIDIA <GPU> — using float16
[Whisper] Loading 'medium' on cuda...
[TTS] Kokoro loaded — 54 voices
[WakeWord] Ready — models: hey_jarvis(0.80)
```

---

## WSL2 Memory Cap (strongly recommended)

Xyron loads Whisper + Kokoro + sentence-transformers simultaneously (~12–15 GB RAM).

Create/edit `C:\Users\YourUsername\.wslconfig` in Windows:

```ini
[wsl2]
memory=10GB
processors=4
swap=4GB
```

| Total RAM | Set memory= |
|-----------|-------------|
| 8 GB      | 6GB         |
| 16 GB     | 10GB        |
| 32 GB     | 20GB        |

Then from PowerShell: `wsl --shutdown`

---

## What's Automatic vs. Manual

| Component | Auto | Manual |
|-----------|------|--------|
| Whisper STT model | Yes (first voice use) | — |
| OpenWakeWord (`hey_jarvis`) | Yes (via pip) | `pip install openwakeword` |
| Kokoro TTS model | No | Step 6 download script |
| "Hey Xyron" wake word | No | Get from Tayyab, Step 7 |
| Core Python packages | No | `pip install -r requirements.txt` |
| Voice packages (kokoro-onnx, librosa…) | No | Step 3b |
| rapidfuzz | Yes (in requirements.txt) | — |
| Node packages | No | `npm install` in each dir |
| Rust (for Tauri) | No | Step 1 |

---

## Troubleshooting

**`Library libcublas.so.12 is not found`**
→ Follow Step 4b

**`ModuleNotFoundError: No module named 'kokoro_onnx'`**
→ `pip install kokoro-onnx`

**`ModuleNotFoundError: No module named 'rapidfuzz'`**
→ `pip install rapidfuzz` (also in requirements.txt — should auto-install)

**`ModuleNotFoundError: No module named 'faster_whisper'`**
→ `pip install faster-whisper`

**Kokoro always falls back to edge-tts**
→ Model files missing. Run Step 6 download script.

**Drive not found / "E drive doesn't exist"**
→ Check that the drive appears in `/mnt/`: `ls /mnt/` — if not, it may need mounting in WSL2.
→ Override with `FS_SCAN_ROOTS=/mnt/d,/mnt/e` in `.env`

**"Hey Xyron" doesn't wake — only "Hey Jarvis" works**
→ `.onnx` files missing from `~/.xyron/wake_models/` — follow Step 7

**Desktop app won't start (Tauri)**
→ Make sure Visual Studio Build Tools and WebView2 are installed (Step 8)
→ On WSL2, run `npm run dev:wsl` not `npm run dev`

**Voice not heard in WSL2**
→ Make sure using `npm run dev:wsl` (sets PULSE_SERVER)
→ Check microphone permissions in Windows Settings → Privacy → Microphone

**Frontend shows "backend offline"**
→ Backend must be running on port 8000. Check: `curl http://localhost:8000/api/v1/health`

---

## Package Versions (Tayyab's machine — reference)

| Package | Version |
|---------|---------|
| Python | 3.10.12 |
| torch | 2.5.1+cu121 |
| kokoro-onnx | 0.5.0 |
| faster-whisper | 1.2.1 |
| sentence-transformers | 5.4.1 |
| rapidfuzz | 3.14.5 |
| openai | 2.32.0 |
| fastapi | 0.115.0 |
| uvicorn | 0.32.1 |
| psutil | 6.0+ |
| Node.js | 20.20.0 |
| Rust | 1.78+ |
| Tauri CLI | 2.x |
