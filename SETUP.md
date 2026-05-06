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

Ask Tayyab to share the `~/.xyron/wake_models/` directory (contains `.onnx` files ~1–5 MB each). Place them at:

```
~/.xyron/wake_models/hey_xyron.onnx
~/.xyron/wake_models/wakeup_xyron.onnx
# etc.
```

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

If you have an NVIDIA GPU, install the CUDA-enabled ONNX runtime for much faster wake word detection and Whisper transcription:

```bash
pip uninstall onnxruntime
pip install onnxruntime-gpu

# Verify CUDA is found
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
# Should include: CUDAExecutionProvider
```

Then set `ONNX_PROVIDER=CUDAExecutionProvider` in `backend/.env` (already the default).

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
