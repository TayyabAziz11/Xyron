# Xyron — Local Setup Guide

Everything you need to run Xyron that isn't in the repo (large model files, system deps, secrets).

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.10+ | [python.org](https://python.org) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| CUDA Toolkit | 11.8+ (optional, for GPU) | [nvidia.com](https://developer.nvidia.com/cuda-downloads) |
| espeak-ng | any | `sudo apt-get install espeak-ng` |
| ffmpeg | any | `sudo apt-get install ffmpeg` |

---

## 1. Clone & Backend Setup

```bash
git clone https://github.com/TayyabAziz11/Xyron.git
cd Xyron/backend

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install openwakeword            # wake word detection (not in requirements.txt)
playwright install chromium         # browser control feature
```

---

## 2. Environment File

Create `backend/.env` (copy this and fill in your keys):

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
```

---

## 3. Model Files (not in repo — too large for GitHub)

### Kokoro TTS — auto-downloads on first run

Kokoro is the local text-to-speech engine. It downloads itself automatically from HuggingFace the first time the backend starts — no manual step needed.

- **Model**: `hexgrad/Kokoro-82M-ONNX` (~100 MB total)
- **Cached to**: `~/.cache/huggingface/hub/`
- **Requires internet** on first boot, then works fully offline

If the auto-download is slow or rate-limited, set `HF_TOKEN` in your `.env` or manually download:

```bash
pip install huggingface_hub
python3 -c "
from huggingface_hub import hf_hub_download
hf_hub_download('hexgrad/Kokoro-82M-ONNX', 'kokoro-v0_19.onnx')
hf_hub_download('hexgrad/Kokoro-82M-ONNX', 'voices.bin')
"
```

### Whisper STT — auto-downloads on first run

Faster-Whisper downloads the speech recognition model automatically on first use.

- **Default model**: `small` (~500 MB) — good balance of speed/accuracy
- **Cached to**: `~/.cache/huggingface/hub/`
- To use a different size, set `WHISPER_MODEL=medium` (or `tiny`, `base`, `large`) in `.env`

### Wake Word Models — auto-downloads on first run

OpenWakeWord downloads its base models (embedding + `hey_jarvis`) automatically.

- **Custom Xyron wake models**: stored in `~/.xyron/wake_models/`
- The trained `.onnx` files for "Hey Xyron" are **not in the repo** — ask Tayyab to share them, or use the default `hey_jarvis` wake word until you train your own.
- To skip custom models and use `hey_jarvis`, leave `WAKE_MODELS_DIR` unset in `.env`

#### Using the default wake word (no custom model needed)

The system falls back to `hey_jarvis` automatically if `~/.xyron/wake_models/` is empty or missing. Just say "Hey Jarvis" to activate.

#### Getting the custom "Hey Xyron" models

Tayyab has already shared 3 files with you via USB. You need to place them in the right location inside WSL2.

**The 3 files you received:**
```
hey_xyron.onnx
wakeup_xyron.onnx
xyron.onnx
```

**Step 1 — Create the folder in WSL2:**
```bash
mkdir -p ~/.xyron/wake_models
```

**Step 2 — Copy the files from your USB into WSL2.**

First find your USB drive letter (e.g. `D:`, `E:`, `F:`). In WSL2, Windows drives are mounted under `/mnt/` — so `D:` becomes `/mnt/d`, `E:` becomes `/mnt/e`, etc.

If your USB is drive `D:` and the files are in a folder called `wake_models_xyron` on it:
```bash
cp /mnt/d/wake_models_xyron/*.onnx ~/.xyron/wake_models/
```

Adjust the drive letter and folder name to match where you put them on the USB.

**Step 3 — Verify the files are in place:**
```bash
ls -lh ~/.xyron/wake_models/
```

You should see all 3 files:
```
hey_xyron.onnx
wakeup_xyron.onnx
xyron.onnx
```

**Step 4 — Restart the backend.** You should now see in the logs:
```
[WakeWord] Ready — models: hey_xyron(0.72)  wakeup_xyron(0.50)  xyron(0.65)  hey_jarvis(0.80)
```

You can now say **"Hey Xyron"** to activate instead of "Hey Jarvis".

---

## 4. Web Dashboard

```bash
cd web
npm install
npm run dev          # runs on http://localhost:3001
```

---

## 5. Desktop App (optional)

```bash
cd desktop-app
npm install

npm run dev:wsl      # WSL2 (sets up audio correctly)
npm run dev          # native Linux / Mac
```

---

## 6. Run the Backend

```bash
cd backend
source .venv/bin/activate
python3 -m uvicorn api.main:app --reload --port 8000
```

First boot will take 30–60 seconds while Whisper and Kokoro download their models. After that, startup is instant.

---

## 7. Verify Everything Works

```bash
# Health check
curl http://localhost:8000/api/v1/system/health

# List all registered tools
curl http://localhost:8000/api/v1/system/registered-tools
```

Open `http://localhost:3001/app/command-center` in your browser and tap the orb — it should start listening.

---

## GPU Setup (optional but recommended)

Xyron auto-detects your GPU on startup and shifts Whisper and the wake word models to it automatically — no code changes needed. When working correctly you'll see this in the backend logs:

```
[Whisper] GPU detected: NVIDIA RTX A2000 — using float16
[Whisper] Loading 'small' on cuda (float16)…
[Whisper] Model ready.
[Warmup] Whisper ready — device=cuda compute=float16
```

The RTX A2000 is fully supported and will run everything on float16 out of the box. If you see `cpu` instead of `cuda`, follow the steps below.

### Step 1 — Install CUDA Toolkit

Download and install from [developer.nvidia.com/cuda-downloads](https://developer.nvidia.com/cuda-downloads). Version 11.8 or newer.

After install, verify:
```bash
nvidia-smi        # should show your GPU and driver version
nvcc --version    # should show CUDA version
```

### Step 2 — Register CUDA libraries (WSL2 only)

On WSL2, CUDA libraries are installed on Windows but need to be registered inside the Linux environment:

```bash
# Register the WSL CUDA lib dir with the dynamic linker
echo "/usr/lib/wsl/lib" | sudo tee /etc/ld.so.conf.d/wsl-cuda.conf
sudo ldconfig

# Verify libcuda is visible
ldconfig -p | grep libcuda
```

Then check that **cuBLAS** is also present — Whisper needs it for actual inference:

```bash
ldconfig -p | grep libcublas
```

If `libcublas.so.12` is **not listed**, install it:

```bash
sudo apt-get update
sudo apt-get install -y libcublas-12-0
sudo ldconfig

# Confirm
ldconfig -p | grep libcublas
```

If `libcublas-12-0` isn't found in apt, add the NVIDIA CUDA apt repo first:

```bash
# For Ubuntu 22.04 (change ubuntu2204 → ubuntu2004 if on 20.04)
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
sudo apt-get install -y libcublas-12-0
sudo ldconfig
```

Alternatively, install the full CUDA 12 toolkit (includes cuBLAS and everything else):

```bash
sudo apt-get install -y cuda-toolkit-12-0
sudo ldconfig
```

After installing, **restart the backend** — Whisper will pick up the new libs on startup.

### Step 3 — Install GPU-enabled ONNX runtime

```bash
pip uninstall onnxruntime -y
pip install onnxruntime-gpu

# Verify CUDA provider is available
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
# Should include: CUDAExecutionProvider
```

### Step 4 — Set `.env`

```env
ONNX_PROVIDER=CUDAExecutionProvider
```

This is already the default in the template above. If you don't have a GPU, change it to `CPUExecutionProvider`.

### What shifts to GPU automatically

| Component | GPU benefit |
|-----------|-------------|
| Whisper STT | float16 inference — ~3–5× faster transcription |
| Wake word (OWW) | ONNX models run on CUDA — lower latency per frame |
| Kokoro TTS | CPU-only (ONNX CPU is fast enough for TTS) |

### Troubleshooting

**Error: `Library libcublas.so.12 is not found or cannot be loaded`**

This is the most common GPU issue on WSL2. It means your CUDA driver is present (so Whisper says "GPU detected") but the cuBLAS compute library is missing. Whisper transcribes nothing and the voice session silently breaks.

Fix (run in WSL2 terminal):

```bash
# 1. Register WSL CUDA libs
echo "/usr/lib/wsl/lib" | sudo tee /etc/ld.so.conf.d/wsl-cuda.conf
sudo ldconfig

# 2. Check if cuBLAS is now found
ldconfig -p | grep libcublas

# 3. If still missing — install it
sudo apt-get update
sudo apt-get install -y libcublas-12-0
sudo ldconfig

# 4. Confirm
ldconfig -p | grep libcublas   # should show libcublas.so.12
```

Then restart the backend. You should now see `cuda (float16)` in startup logs.

---

**Wake word: "Hey Xyron" doesn't trigger — only "Hey Jarvis" works**

The 3 custom model files (`hey_xyron.onnx`, `wakeup_xyron.onnx`, `xyron.onnx`) are not in the repo. They need to be placed manually. See the **"Getting the custom Hey Xyron models"** section above under Model Files — it has the exact step-by-step copy commands for WSL2.

Until the files are in place, say **"Hey Jarvis"** — it works out of the box.

---

**Still seeing `cpu` in logs after setup:**

```bash
# Check that PyTorch can see CUDA (Whisper device detection uses torch)
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

If this returns `False`, your CUDA installation or WSL2 GPU passthrough isn't configured correctly. Check that you're running WSL2 (not WSL1) and that your Windows NVIDIA driver is up to date (525+).

---

## Summary — What Downloads Automatically vs. What You Need Manually

| Component | Auto-download | Manual step |
|-----------|--------------|-------------|
| Kokoro TTS model (~100 MB) | Yes, on first backend start | None (or set HF_TOKEN) |
| Whisper `small` model (~500 MB) | Yes, on first voice use | None |
| OpenWakeWord base models | Yes, via `openwakeword` package | `pip install openwakeword` |
| "Hey Xyron" custom wake models | **No** | Get from Tayyab → `~/.xyron/wake_models/` |
| Python packages | No | `pip install -r requirements.txt` |
| Node packages | No | `npm install` in `web/` and `desktop-app/` |
| `espeak-ng` | No | `sudo apt-get install espeak-ng` |
