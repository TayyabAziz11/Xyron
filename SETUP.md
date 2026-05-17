# Xyron — Setup Guide

Complete guide to get Xyron running from scratch on a new machine.
Written for Tayyab's friends and collaborators — follow every step in order.

---

## Already Running? — Pull Latest Updates

```bash
cd Xyron
git pull origin main
```

Then restart the backend:

```bash
cd backend
PYTHONPATH=/mnt/e/Xyron/backend python3 -m uvicorn api.main:app --reload --port 8000
```

No reinstall needed unless told otherwise.

---

## Full Setup Checklist (fresh machine)

- [ ] Step 1 — System dependencies
- [ ] Step 2 — Clone repo
- [ ] Step 3 — Python packages (core)
- [ ] Step 4 — Voice pipeline packages (extra — NOT in requirements.txt)
- [ ] Step 5 — GPU setup (WSL2 + NVIDIA only)
- [ ] Step 6 — Environment file (`.env`)
- [ ] Step 7 — Kokoro TTS model download
- [ ] Step 8 — Wake word models
- [ ] Step 9 — Web dashboard
- [ ] Step 10 — Start everything and verify

---

## Step 1 — System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip espeak-ng ffmpeg
```

Install Node.js 20 (required for the web dashboard):

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

Verify everything:

```bash
python3 --version     # must be 3.10 or higher
node --version        # must be 20+
espeak-ng --version   # any version is fine
ffmpeg -version       # any version is fine
```

---

## Step 2 — Clone the Repo

```bash
git clone https://github.com/TayyabAziz11/Xyron.git
cd Xyron
```

> **Note:** Packages are installed system-wide (no virtualenv). This is how Tayyab's
> machine is set up and is required for the backend to find all packages correctly.
> Do NOT create a `.venv` — it will break the import paths.

---

## Step 3 — Python Packages (Core)

```bash
cd Xyron/backend
pip install -r requirements.txt
pip install openwakeword
playwright install chromium
```

---

## Step 4 — Voice Pipeline Packages

These packages are NOT in `requirements.txt` — they must be installed manually.
Run all of these in order:

### 4a — Kokoro TTS and edge-tts fallback

```bash
pip install kokoro-onnx edge-tts
```

### 4b — Audio processing (librosa, scipy, soundfile)

```bash
pip install librosa scipy soundfile sounddevice
```

### 4c — Speech activity detection

```bash
pip install webrtcvad-wheels
```

### 4d — HuggingFace model hub (for model downloads)

```bash
pip install huggingface-hub
```

### 4e — RVC voice conversion (lightweight tier — for offline test samples only)

> These packages enable Xyron's RVC voice conversion engine.
> RVC is **disabled for live streaming** (ENABLE_RVC=false in .env) because
> per-chunk latency is inconsistent. It only runs in the offline test script.
>
> Install with `--no-deps` because `rvc-python` requires numpy<=1.23 but the
> rest of the system uses numpy 2.x — the `--no-deps` flag skips that conflict.

```bash
pip install rvc-python --no-deps
pip install "faiss-cpu>=1.9.0" praat-parselmouth pyworld resampy torchcrepe
```

If you have an NVIDIA GPU with CUDA, also install faiss-gpu for faster index search:

```bash
pip install faiss-gpu
```

> **fairseq (full RVC tier) — DO NOT try to install from PyPI.**
> The PyPI package has a broken build (missing `fairseq/version.txt`).
> The full RVC tier stays disabled until this is resolved upstream.
> The lightweight tier (librosa pitch shift + spectral EQ) works without it.

### 4f — Verify voice packages

```bash
python3 -c "
import kokoro_onnx; print('kokoro-onnx OK')
import librosa;     print('librosa OK', librosa.__version__)
import scipy;       print('scipy OK', scipy.__version__)
import faiss;       print('faiss OK')
import resampy;     print('resampy OK')
"
```

All five lines should print OK. If faiss fails, re-run `pip install faiss-cpu>=1.9.0`.

---

## Step 5 — GPU Setup (WSL2 + NVIDIA only)

Skip this entire step if you have no GPU — Xyron falls back to CPU automatically.

### 5a — Register CUDA libraries with WSL2

```bash
echo "/usr/lib/wsl/lib" | sudo tee /etc/ld.so.conf.d/wsl-cuda.conf
sudo ldconfig
ldconfig -p | grep libcuda   # should print at least one line
```

### 5b — Install cuBLAS (required for Whisper GPU mode)

```bash
ldconfig -p | grep libcublas
```

If `libcublas.so.12` is **not** listed:

```bash
sudo apt-get update
sudo apt-get install -y libcublas-12-0
sudo ldconfig
ldconfig -p | grep libcublas   # should now show libcublas.so.12
```

If apt can't find `libcublas-12-0`, add the NVIDIA repo first:

```bash
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
sudo apt-get install -y libcublas-12-0
sudo ldconfig
```

### 5c — ONNX runtime with GPU support

```bash
pip uninstall onnxruntime -y
pip install onnxruntime-gpu
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
# Must include: CUDAExecutionProvider
```

### 5d — PyTorch with CUDA

Tayyab's machine runs **PyTorch 2.5.1 + CUDA 12.1**. Install the matching version:

```bash
pip install torch==2.5.1 torchaudio==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# Should print: True  NVIDIA <your GPU>
```

> If your machine has a different CUDA version (check with `nvidia-smi`), change
> `cu121` to match — e.g. `cu118` for CUDA 11.8 or `cu124` for CUDA 12.4.

---

## Step 6 — Environment File

Create `backend/.env` — copy this block and fill in your `OPENAI_API_KEY`:

```env
# ── Required ──────────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...              # get from platform.openai.com

# ── Server ────────────────────────────────────────────────────────────────────
API_PORT=8000

# ── Voice / TTS ───────────────────────────────────────────────────────────────
# Use CUDAExecutionProvider if you have a GPU, CPUExecutionProvider if not
ONNX_PROVIDER=CUDAExecutionProvider
WHISPER_MODEL=medium
WHISPER_LANGUAGE=auto

# ── Wake word ─────────────────────────────────────────────────────────────────
WAKE_WORD_MODEL=hey_jarvis
WAKE_WORD_THRESHOLD=0.5
WAKE_COOLDOWN_S=3.0

# ── RVC voice conversion ──────────────────────────────────────────────────────
# Disabled for live streaming (inconsistent per-chunk latency causes voice shifts).
# Set to true ONLY to run the offline test script: scripts/test_rvc_pipeline.py
ENABLE_RVC=false
RVC_MODEL_DIR=~/.xyron/models/rvc
RVC_DEFAULT_PRESET=neutral
RVC_DEVICE=auto
RVC_MAX_LATENCY_MS=250
RVC_LIGHTWEIGHT=false

# ── Screen context ────────────────────────────────────────────────────────────
# Costs ~$0.10/day when enabled (uses GPT-4o for screenshot analysis)
SCREEN_CONTEXT_ENABLED=false
SCREEN_CONTEXT_INTERVAL=300

# ── Cost caps ─────────────────────────────────────────────────────────────────
XYRON_MAX_GPT4O_PER_HOUR=0
XYRON_MAX_MINI_PER_HOUR=200

# ── Optional overrides ────────────────────────────────────────────────────────
# HF_TOKEN=hf_...                         # avoids HuggingFace rate limits on first download
# OLLAMA_API_URL=http://localhost:11434/api/generate
```

> **No GPU?** Change `ONNX_PROVIDER=CPUExecutionProvider` — everything still works, just slower.

---

## Step 7 — Kokoro TTS Model

Kokoro is the local voice synthesis engine. Download its model files:

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

Expected output: `Kokoro ready: ['kokoro-v1.0.onnx', 'voices-v1.0.bin']`

Downloads ~100 MB once, fully cached after that. Works offline after download.

**Whisper STT** (~500 MB for the `medium` model) downloads automatically on first voice use — no manual step needed.

---

## Step 8 — Wake Word Models

### Default (works out of the box)

`openwakeword` downloads `hey_jarvis` automatically. Say **"Hey Jarvis"** to activate.

### Custom "Hey Xyron" models (get from Tayyab)

Tayyab will share 3 `.onnx` files. Place them in `~/.xyron/wake_models/`:

```bash
mkdir -p ~/.xyron/wake_models

# If Tayyab sends via USB — replace /mnt/d/wake_models_xyron with your actual USB path
# (In WSL2: Windows D: drive = /mnt/d, E: drive = /mnt/e, etc.)
cp /mnt/d/wake_models_xyron/*.onnx ~/.xyron/wake_models/

# Verify
ls -lh ~/.xyron/wake_models/
# Should show: hey_xyron.onnx  wakeup_xyron.onnx  xyron.onnx
```

When loaded, the backend prints:
```
[WakeWord] Ready — models: hey_xyron(0.72)  wakeup_xyron(0.88)  xyron(0.78)  hey_jarvis(0.80)
```

---

## Step 9 — Web Dashboard

```bash
cd Xyron/web
npm install
npm run dev   # runs on http://localhost:3001
```

---

## Step 10 — Start Everything and Verify

**Terminal 1 — Backend:**

```bash
cd Xyron/backend
PYTHONPATH=/mnt/e/Xyron/backend python3 -m uvicorn api.main:app --reload --port 8000
```

> Change `/mnt/e/Xyron` to wherever you cloned the repo.
> Example: if you cloned to `/home/user/Xyron`, use `PYTHONPATH=/home/user/Xyron/backend`.

**Terminal 2 — Web dashboard:**

```bash
cd Xyron/web
npm run dev
```

**Health check:**

```bash
curl http://localhost:8000/api/v1/system/health
```

Open `http://localhost:3001/app/command-center` in your browser.
Tap the orb — it starts listening.

**Expected backend logs on clean startup:**

```
[Whisper] GPU detected: NVIDIA <GPU> — using float16
[Whisper] Loading 'medium' on cuda (float16)...
[Whisper] Model ready.
[TTS] Kokoro loaded on CUDAExecutionProvider — 54 voices
[WakeWord] Ready — models: ...
[RVC_RESPONSE] enabled=False reason=streaming_disabled
```

---

## WSL2 Memory Cap (strongly recommended)

Xyron loads Whisper + Kokoro + sentence-transformers + wake word models simultaneously.
This can hit 12–15 GB RAM. Without a cap, Windows starts paging and everything crawls.

Create this file **on Windows** (not inside WSL2):

**File:** `C:\Users\YourWindowsUsername\.wslconfig`

```ini
[wsl2]
memory=10GB
processors=4
swap=4GB
```

Adjust `memory` based on your total RAM:

| Total RAM | Set memory= |
|-----------|-------------|
| 8 GB      | 6GB         |
| 16 GB     | 10GB        |
| 32 GB     | 20GB        |

After saving, restart WSL2 from PowerShell:

```powershell
wsl --shutdown
```

Then reopen your WSL2 terminal.

---

## Desktop App (optional)

```bash
cd Xyron/desktop-app
npm install
npm run dev:wsl    # WSL2 (sets up audio bridge automatically)
npm run dev        # native Linux / Mac
```

---

## Troubleshooting

**`Library libcublas.so.12 is not found`**
Whisper found your GPU but cuBLAS is missing. Follow Step 5b.

**`ModuleNotFoundError: No module named 'kokoro_onnx'`**
Run: `pip install kokoro-onnx`

**`ModuleNotFoundError: No module named 'librosa'`**
Run: `pip install librosa`

**`ModuleNotFoundError: No module named 'faiss'`**
Run: `pip install "faiss-cpu>=1.9.0"`

**Kokoro always falls back to edge-tts**
The model files are missing. Run the Step 7 download script.

**`rvc_python.infer` fails with `No module named 'fairseq'`**
This is expected — fairseq cannot be installed from PyPI. Full RVC tier is disabled.
The lightweight tier (librosa + scipy) works fine. Leave ENABLE_RVC=false.

**Whisper returns gibberish or wrong language**
Check `WHISPER_LANGUAGE=auto` in `.env`. Pull latest — the language filter was fixed.

**"Hey Xyron" doesn't wake — only "Hey Jarvis" works**
The `.onnx` files are missing from `~/.xyron/wake_models/`. Follow Step 8.

**STT hears "Here's Aaron" or "Zairon" instead of "Xyron"**
This is fixed in the latest code — pull latest. The normalizer maps all phonetic
variants back to "xyron" before routing.

**Frontend orb doesn't respond**
Make sure both backend (port 8000) and frontend (port 3001) are running.
Check browser console for CORS errors — backend must be on exactly port 8000.

**Torch not finding GPU after install**
```bash
python3 -c "import torch; print(torch.cuda.is_available())"
```
If False: check you're on WSL2 (not WSL1) and your Windows NVIDIA driver is 525+.
Run `nvidia-smi` from PowerShell — if it shows a GPU, WSL2 should see it too.

---

## What's Automatic vs. Manual

| Component | Auto | Manual step |
|-----------|------|-------------|
| Whisper STT model (~500 MB) | Yes — first voice use | Nothing |
| OpenWakeWord base (`hey_jarvis`) | Yes — via pip | `pip install openwakeword` |
| Kokoro TTS model (~100 MB) | **No** | Step 7 download script |
| "Hey Xyron" wake word models | **No** | Get from Tayyab — Step 8 |
| Core Python packages | **No** | `pip install -r requirements.txt` |
| Voice pipeline packages | **No** | Step 4 (kokoro-onnx, librosa, etc.) |
| Node packages (web) | **No** | `npm install` in `web/` |
| espeak-ng / ffmpeg | **No** | `sudo apt-get install` — Step 1 |
| PyTorch + CUDA | **No** | Step 5d (GPU only) |
| faiss-cpu / rvc-python | **No** | Step 4e (RVC optional) |

---

## Package Version Reference

These are the exact versions running on Tayyab's machine (use as a reference if something breaks):

| Package | Version | Notes |
|---------|---------|-------|
| Python | 3.10.12 | minimum 3.10 required |
| torch | 2.5.1+cu121 | CUDA 12.1 build |
| kokoro-onnx | 0.5.0 | local TTS |
| faster-whisper | 1.2.1 | local STT |
| edge-tts | 7.2.8 | fallback TTS (needs internet) |
| librosa | 0.11.0 | audio processing / RVC lightweight |
| scipy | 1.15.3 | signal processing |
| faiss-cpu | 1.13.2 | vector search |
| rvc-python | 0.1.5 | RVC (disabled for streaming) |
| sentence-transformers | 5.4.1 | semantic intent routing |
| openai | 2.15.0 | GPT-4o-mini fallback |
| fastapi | 0.115.0 | backend framework |
| uvicorn | 0.32.1 | ASGI server |
| numpy | 2.2.6 | note: rvc-python requires <=1.23.5 but works with --no-deps |
| Node.js | 20.20.0 | web dashboard |
| Next.js | 15.5.14 | web framework |
