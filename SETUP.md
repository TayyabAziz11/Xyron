# Xyron — Local Setup Guide

Everything you need to run Xyron that isn't in the repo (large model files, system deps, secrets).

---

## Already Running? — Pull Latest Updates

If you already have the project cloned and running (like Qasim), do this every time Tayyab pushes fixes:

```bash
cd Xyron
git pull origin main
```

Then restart the backend:

```bash
cd backend
source .venv/bin/activate
python3 -m uvicorn api.main:app --reload --port 8000
```

That's it — no reinstall needed unless `requirements.txt` changed (check with `git diff HEAD~1 requirements.txt`).

### First pull after May 9, 2026? — One extra step

The Kokoro TTS model path changed. Run this once to put the model in the right place:

```bash
cd backend
source .venv/bin/activate
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

You should see `['kokoro-v1.0.onnx', 'voices-v1.0.bin']` printed. After that, restart the backend and Kokoro TTS will work.

---

## Fresh Machine — Full Setup Checklist

Follow these steps **in order**. Each step must succeed before moving to the next.

- [ ] Step 1 — System dependencies
- [ ] Step 2 — Clone repo and Python environment
- [ ] Step 3 — Environment file (`.env`)
- [ ] Step 4 — GPU setup (WSL2 only, skip if no GPU)
- [ ] Step 5 — Kokoro TTS model download
- [ ] Step 6 — Wake word models
- [ ] Step 7 — Web dashboard
- [ ] Step 8 — Start everything and verify

---

## Step 1 — System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y espeak-ng ffmpeg
```

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.10+ | `sudo apt-get install python3 python3-venv` |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) or `sudo apt-get install nodejs npm` |
| espeak-ng | any | `sudo apt-get install espeak-ng` |
| ffmpeg | any | `sudo apt-get install ffmpeg` |

Verify:
```bash
python3 --version   # 3.10+
node --version      # 18+
ffmpeg -version     # any
espeak-ng --version # any
```

---

## Step 2 — Clone & Backend Setup

```bash
git clone https://github.com/TayyabAziz11/Xyron.git
cd Xyron/backend

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install openwakeword            # wake word detection (not in requirements.txt)
playwright install chromium         # browser control feature
```

---

## Step 3 — Environment File

Create `backend/.env` — copy this exactly and fill in your `OPENAI_API_KEY`:

```env
OPENAI_API_KEY=sk-...              # Required — get from platform.openai.com

API_PORT=8000
ONNX_PROVIDER=CUDAExecutionProvider   # use CPUExecutionProvider if no GPU

# Wake word
WAKE_WORD_MODEL=hey_jarvis
WAKE_WORD_THRESHOLD=0.5
WAKE_COOLDOWN_S=3.0

# Optional — set to avoid HuggingFace rate limits on first model download
# HF_TOKEN=hf_...

# Screen context — costs ~$0.10/day if enabled
SCREEN_CONTEXT_ENABLED=false
SCREEN_CONTEXT_INTERVAL=300

# Cost caps
XYRON_MAX_GPT4O_PER_HOUR=0
XYRON_MAX_MINI_PER_HOUR=200

# Cross-machine overrides (optional — defaults work for single-machine setups)
# XYRON_API_BASE=http://localhost:8000       # change if backend runs on a different host/port
# XYRON_BACKEND_URL=http://localhost:8000    # used by the MCP server
# OLLAMA_API_URL=http://localhost:11434/api/generate  # change if Ollama runs elsewhere
# FS_SCAN_ROOTS=/home/user,/data            # comma-separated paths to index (WSL2: auto-detects /mnt/d-g)
```

No GPU? Change `ONNX_PROVIDER=CPUExecutionProvider` — everything still works, just slower.

---

## Step 4 — GPU Setup (WSL2 with NVIDIA GPU only)

Skip this step if you have no GPU or are on native Linux/Mac — Xyron falls back to CPU automatically.

### 4a — Register CUDA libraries

```bash
echo "/usr/lib/wsl/lib" | sudo tee /etc/ld.so.conf.d/wsl-cuda.conf
sudo ldconfig

# Verify libcuda is visible
ldconfig -p | grep libcuda
```

### 4b — Install cuBLAS (required for Whisper GPU inference)

```bash
ldconfig -p | grep libcublas
```

If `libcublas.so.12` is **not listed**:

```bash
sudo apt-get update
sudo apt-get install -y libcublas-12-0
sudo ldconfig
ldconfig -p | grep libcublas   # should now show libcublas.so.12
```

If `libcublas-12-0` is not found in apt, add the NVIDIA repo first:

```bash
# Ubuntu 22.04 — change ubuntu2204 → ubuntu2004 if on 20.04
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
sudo apt-get install -y libcublas-12-0
sudo ldconfig
```

Or install the full CUDA toolkit (includes everything):

```bash
sudo apt-get install -y cuda-toolkit-12-0
sudo ldconfig
```

### 4c — Install GPU ONNX runtime

```bash
pip uninstall onnxruntime -y
pip install onnxruntime-gpu

# Verify
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
# Should include: CUDAExecutionProvider
```

### 4d — Verify GPU is detected

```bash
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# Should print: True  NVIDIA <your GPU name>
```

When working, the backend logs will show:
```
[Whisper] GPU detected: NVIDIA <GPU> — using float16
[Whisper] Model ready.
```

---

## Step 5 — Kokoro TTS Model

Kokoro is the local TTS engine. Download its model files into `~/.xyron/models/`:

```bash
cd backend
source .venv/bin/activate

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

Expected output: `Kokoro ready: ['kokoro-v1.0.onnx', 'voices-v1.0.bin']`

Downloads ~100 MB on first run, cached afterwards. Kokoro works fully offline after this.

**Whisper STT** (`~500 MB`) downloads automatically on first voice use — no manual step needed.

---

## Step 6 — Wake Word Models

### Default (no files needed)

OpenWakeWord downloads `hey_jarvis` automatically via `pip install openwakeword`. Just say **"Hey Jarvis"** to activate.

### Custom "Hey Xyron" models (get from Tayyab via USB)

Tayyab will share 3 files. Place them here:

**Step 6a — Create the folder:**
```bash
mkdir -p ~/.xyron/wake_models
```

**Step 6b — Copy from USB.**
In WSL2, Windows drives appear as `/mnt/<letter>` — so USB drive `D:` = `/mnt/d`, `E:` = `/mnt/e`, etc.

```bash
# Replace /mnt/d/wake_models_xyron with your actual USB path
cp /mnt/d/wake_models_xyron/*.onnx ~/.xyron/wake_models/
```

**Step 6c — Verify:**
```bash
ls -lh ~/.xyron/wake_models/
# Should show: hey_xyron.onnx  wakeup_xyron.onnx  xyron.onnx
```

When loaded correctly, the backend logs show:
```
[WakeWord] Ready — models: hey_xyron(0.72)  wakeup_xyron(0.88)  xyron(0.78)  hey_jarvis(0.80)
```

---

## Step 7 — Web Dashboard

```bash
cd web
npm install
npm run dev          # runs on http://localhost:3001
```

---

## Step 8 — Start Everything and Verify

**Terminal 1 — Backend:**
```bash
cd backend
source .venv/bin/activate
python3 -m uvicorn api.main:app --reload --port 8000
```

First boot: 30–60 seconds while Whisper downloads. After that, instant startup.

**Terminal 2 — Web dashboard:**
```bash
cd web
npm run dev
```

**Health check:**
```bash
curl http://localhost:8000/api/v1/system/health
```

Open `http://localhost:3001/app/command-center` in your browser and tap the orb — it should start listening.

**Expected backend logs on healthy startup:**
```
[Whisper] GPU detected: NVIDIA <GPU> — using float16   (or: No CUDA — using CPU int8)
[Whisper] Loading 'small' on cuda (float16)…
[Whisper] Model ready.
[TTS] Kokoro loaded on CUDAExecutionProvider — 54 voices
[WakeWord] Ready — models: hey_xyron(0.72)  wakeup_xyron(0.88)  xyron(0.78)  hey_jarvis(0.80)
```

---

## Troubleshooting

**`Library libcublas.so.12 is not found`**
Whisper detected your GPU but cuBLAS isn't installed. Follow Step 4b above.

**Wake word fires on background noise / "Hi" triggers it**
Pull latest — this was fixed. `git pull && restart backend`.

**Kokoro TTS always falls back to edge-tts**
Run the Step 5 download script. The model files are missing from `~/.xyron/models/`.

**Whisper returns Hindi or Portuguese garbage**
Pull latest — this was fixed (language filter added). `git pull && restart backend`.

**"Hey Xyron" doesn't trigger — only "Hey Jarvis" works**
The `.onnx` files aren't in `~/.xyron/wake_models/`. Follow Step 6.

**Still seeing `cpu` after GPU setup:**
```bash
python3 -c "import torch; print(torch.cuda.is_available())"
```
If `False`: check you're on WSL2 (not WSL1) and your Windows NVIDIA driver is 525+.

**Frontend orb doesn't respond to voice:**
Check CORS — backend must be on port 8000, frontend on 3001. Both must be running.

---

## WSL2 Memory Cap (required if RAM usage hits 90%+)

By default WSL2 can consume all available RAM. Xyron loads Whisper (float16) + Kokoro ONNX + 3 wake word models + sentence-transformers simultaneously — this easily hits 12–15 GB. Without a cap, Windows starts swapping and everything slows to a crawl.

Add this file on **Windows** (not WSL2):

**File:** `C:\Users\<YourWindowsUsername>\.wslconfig`

```ini
[wsl2]
memory=10GB
processors=4
swap=4GB
```

Adjust `memory` based on your total RAM:

| Total RAM | Set memory= |
|-----------|------------|
| 16 GB | 10GB |
| 32 GB | 20GB |
| 8 GB | 6GB |

After creating/editing the file, restart WSL2 from PowerShell:

```powershell
wsl --shutdown
```

Then reopen your WSL2 terminal. RAM usage will now stay within the cap.

---

## Desktop App (optional)

```bash
cd desktop-app
npm install
npm run dev:wsl      # WSL2
npm run dev          # native Linux / Mac
```

---

## What's Automatic vs. Manual

| Component | Auto | Manual |
|-----------|------|--------|
| Whisper STT model (~500 MB) | Yes — first voice use | Nothing |
| OpenWakeWord base (`hey_jarvis`) | Yes — via pip | `pip install openwakeword` |
| Kokoro TTS model (~100 MB) | **No** | Run Step 5 script |
| "Hey Xyron" wake models | **No** | Get from Tayyab → Step 6 |
| Python packages | No | `pip install -r requirements.txt` |
| Node packages | No | `npm install` in `web/` |
| espeak-ng / ffmpeg | No | `sudo apt-get install` |
