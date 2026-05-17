# Xyron RVC Voice System

Retrieval-based Voice Conversion (RVC) layer between Kokoro TTS and audio FX.

---

## Architecture

```
User input
    ↓
respond-stream / synthesize-stream
    ↓
Emotion detection (emotion_engine → mood_state_machine)
    ↓
Kokoro TTS  →  WAV bytes
    ↓
voice_conversion (thin facade)
    ↓
rvc_engine.convert(audio_bytes, preset, intensity)
    ├── Full RVC tier  (rvc_python + HuBERT + model .pth)
    ├── Lightweight    (librosa pitch_shift + scipy spectral EQ)
    └── Passthrough   (ENABLE_RVC=false or deps missing)
    ↓
audio_fx  (compression, air boost, reverb, saturation)
    ↓
Audio output
```

---

## Tier Selection

`rvc_engine.py` auto-selects the best available tier at startup:

| Tier | Requirements | Quality | Latency |
|------|-------------|---------|---------|
| `full_rvc` | rvc_python + fairseq + model .pth files | Best | ~200ms/chunk |
| `lightweight` | librosa + scipy | Good | ~50-300ms/chunk |
| `passthrough` | none (fallback) | Kokoro-only | 0ms |

Check active tier: `GET /api/v1/voice/rvc-status`

---

## Environment Variables (`backend/.env`)

```env
ENABLE_RVC=true                        # master switch — false = passthrough always
RVC_MODEL_DIR=~/.xyron/models/rvc      # model file root (full_rvc tier)
RVC_DEFAULT_PRESET=neutral             # preset when emotion has no mapping
RVC_DEVICE=auto                        # cuda / cpu / auto
RVC_MAX_LATENCY_MS=250                 # latency guard threshold
RVC_LIGHTWEIGHT=false                  # force lightweight tier, skip full_rvc check
```

---

## Preset System

Each emotion state maps to an RVC preset:

| Mood | Preset | Pitch | Brightness | Warmth |
|------|--------|-------|-----------|--------|
| HYPED, EXCITED | `hyped` | +1.2 st | +0.12 | 0 |
| RELIEVED_EXCITED | `relieved` | +0.5 st | +0.05 | +0.08 |
| DOMINANT, LOCKED_IN | `dominant` | -1.5 st | -0.08 | +0.18 |
| PROTECTIVE, FOCUSED | `protective` | -0.5 st | -0.05 | +0.12 |
| CALM | `calm` | -0.4 st | -0.10 | +0.10 |
| LATE_NIGHT | `late_night` | -1.0 st | -0.15 | +0.20 |
| AUDIENCE_MODE | `audience_mode` | -1.0 st | +0.08 | +0.08 |
| NEUTRAL (default) | `neutral` | 0 | 0 | 0 |

Custom mappings: `MOOD_TO_PRESET` dict in `backend/voice/rvc_engine.py`

---

## Full RVC Model Layout

For full_rvc tier, place model files at:

```
~/.xyron/models/rvc/
├── hyped/
│   └── model.pth
├── relieved/
│   └── model.pth
├── dominant/
│   └── model.pth
├── protective/
│   └── model.pth
├── calm/
│   └── model.pth
├── late_night/
│   └── model.pth
└── audience_mode/
    └── model.pth
```

Models are lazy-loaded per preset on first use.

---

## Installing Full RVC Dependencies

```bash
# Required
pip install rvc-python --no-deps
pip install faiss-cpu>=1.9.0 praat-parselmouth pyworld resampy torchcrepe

# fairseq (for HuBERT feature extraction) — build from source
pip install git+https://github.com/facebookresearch/fairseq.git@main
```

Note: `rvc-python` requires `numpy<=1.23.5` which conflicts with numpy 2.x. Use `--no-deps` and install transitive deps manually.

---

## Latency Guard

If a conversion exceeds `RVC_MAX_LATENCY_MS` (250ms default), the engine:
1. Logs a warning
2. Returns the original Kokoro audio for that turn
3. **Resets the guard** — RVC is attempted again next turn

This prevents one slow call from permanently disabling RVC.

---

## Fallback Chain

```
full_rvc model missing → fallback to lightweight for that preset
lightweight fails      → return original audio (safe passthrough)
ENABLE_RVC=false       → passthrough immediately, no imports
```

No exception is ever raised to the caller — audio always comes back.

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/voice/rvc_engine.py` | Core engine — tier selection, conversion, latency guard |
| `backend/voice/voice_conversion.py` | Thin facade — backward-compatible public API |
| `backend/api/routers/voice.py` | `GET /rvc-status` endpoint + pipeline integration |
| `backend/api/config.py` | Pydantic Settings for all RVC env vars |
| `backend/scripts/test_rvc_pipeline.py` | WAV comparison test — generates kokoro_only.wav + rvc_<preset>.wav |

---

## Frontend Indicator

Command Center shows a subtle voice mode badge when RVC is active:

- `KOKORO` — passthrough mode
- `KOKORO + RVC LITE` — lightweight tier active
- `KOKORO + RVC` — full_rvc tier active

Source: `web/src/app/app/command-center/page.tsx` — `rvcTier` state + `/rvc-status` polling every 30s.

---

## Running the Test Script

```bash
cd /mnt/e/Xyron/backend
PYTHONPATH=/mnt/e/Xyron/backend python3 scripts/test_rvc_pipeline.py
```

Outputs to `backend/data/rvc_tests/`:
- `kokoro_only.wav` — baseline TTS
- `rvc_<preset>.wav` — per-preset conversion result
- `report.txt` — sizes, latencies, pass/fail per preset
