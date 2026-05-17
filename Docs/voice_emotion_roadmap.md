# Xyron Voice Emotion Roadmap

**Last updated:** 2026-05-16  
**Status:** Phase 1 complete — natural emotional delivery working

---

## Current State (Phase 1 — Complete)

Xyron's voice pipeline delivers emotionally differentiated speech through:

- **Emotion profiles** — per-event pacing and FX, not just raw mood state
- **Natural language reactions** — no ALL CAPS, no em-dash spam, no scripted shouting
- **Prosody planner** — sentence-level splits with emotion-matched pauses
- **Kokoro TTS** + **AudioFX chain** — warm, clean output at controlled speed

### Verified Pipeline (live tested 2026-05-16)

```
Text input
  → emotional_guard (intent classification)
  → self_upgrade_detector (upgrade type: memory/voice/voice_conversion/prosody/…)
  → mood_machine (HYPED / PROTECTIVE / EXCITED / …)
  → emotion profile selection (HYPED_NATURAL / RELIEVED_EXCITED / PROTECTIVE_FOCUSED / PROUD_CALM)
  → LLM response generation (GPT-4o-mini, seeded with natural example)
  → prosody_planner (sentence splits, 130ms natural pauses)
  → Kokoro TTS (1.10x HYPED_NATURAL / 0.96x PROTECTIVE / 1.05x RELIEVED_EXCITED)
  → AudioFX (hyped preset: gentle compression, light air boost, 0.92 normalize)
  → SSE stream to frontend → orb state update
```

### Log Markers (all verified)

| Marker | Location | Purpose |
|---|---|---|
| `[EMOTION_STATE]` | mood machine update | Current mood + energy |
| `[EMOTION_INTENSITY]` | upgrade detection | EXTREME vs HIGH |
| `[EMOTION_FORCE]` | branch entry | Which profile was set |
| `[TTS_EMOTION]` | synthesize-stream | Speed applied |
| `[AUDIO_FX]` | synthesize-stream | FX preset applied |
| `[PROSODY]` | _kokoro_to_wav | Chunk count + speed |
| `[UI_EMOTION_EVENT]` | respond-stream | Frontend event sent |
| `[ORB_STATE]` | respond-stream | Orb variant applied |
| `[EMOTION_QA]` | respond-stream | Quality metadata |

### Emotion Profiles

| Profile | Trigger | Speed | Pacing | FX |
|---|---|---|---|---|
| HYPED_NATURAL | Self-upgrade events | 1.10x | Sentence splits, 130ms pauses | Hyped (gentle) |
| RELIEVED_EXCITED | Achievement / bug-fixed | 1.05x | First chunk slower, rest normal | Hyped (gentle) |
| PROTECTIVE_FOCUSED | Frustration / blockers | 0.96x | Single chunk, calm | Warm |
| PROUD_CALM | Praise / completion | 1.00x | Single chunk, stable | Subtle |

### QA Baseline (2026-05-16)

| Profile | Naturalness | Robotic Feel | Em-dashes | ALL CAPS |
|---|---|---|---|---|
| neutral | 9/10 | 0/10 | 0 | 0 |
| hyped_natural_memory | 9/10 | 0/10 | 0 | 0 |
| hyped_natural_voice | 9/10 | 0/10 | 0 | 0 |
| relieved_excited | 9/10 | 0/10 | 0 | 0 |
| protective_focused | 9/10 | 0/10 | 0 | 0 |
| proud_calm | 9/10 | 0/10 | 0 | 0 |

WAV samples: `backend/data/voice_tests/`

---

## Known Limitations (Kokoro Base TTS)

Kokoro with AudioFX can produce clean, natural speech — but it has architectural ceilings:

1. **Flat prosody within sentences** — Kokoro doesn't modulate pitch mid-sentence based on emotion. Words like "HUGE" get the same pitch as "small." Emphasis only comes from speed and pause structure.

2. **No intra-word dynamics** — Human excitement rises and falls within a single phrase. Kokoro synthesizes each chunk at uniform energy.

3. **Desire engine repetition** — The connected-desire system currently returns the same appended sentence for similar upgrade types, making long responses feel templated. Needs content variety.

4. **No true voice character differentiation** — HYPED and CALM Xyron sound like the same speaker at different speeds. A real emotional voice character would have timbral differences.

---

## Phase 2 — RVC Emotional Voice Conversion

**Goal:** Give each emotion profile a distinct vocal character, not just different pacing.

### What RVC provides

RVC (Retrieval-based Voice Conversion) runs a trained voice model over Kokoro's output to reshape the timbre, pitch envelope, and formants — producing a voice that sounds genuinely different for different emotional states, not just faster or slower.

### Integration Plan

```
Kokoro TTS output (WAV)
  → voice_conversion.convert(wav, mood)   ← already wired in _kokoro_to_wav
  → RVC model (per emotion profile)       ← Phase 2 target
  → AudioFX chain
  → final WAV
```

The `voice_conversion.py` module at `backend/voice/voice_conversion.py` is the integration point. It currently has a passthrough or lightweight transform. Phase 2 replaces it with a real RVC inference call.

### Required Models

| Model | Purpose | Expected location |
|---|---|---|
| `xyron_base.pth` | Base Xyron voice | `~/.xyron/models/rvc/` |
| `xyron_hyped.pth` | Excited / upgrade reactions | `~/.xyron/models/rvc/` |
| `xyron_calm.pth` | Calm / protective states | `~/.xyron/models/rvc/` |

Training data: ~10 minutes of clean voice per character profile. Can be synthesized from Kokoro then fine-tuned with target voice samples.

### Required Packages

```bash
pip install fairseq torch torchaudio
# RVC inference library (choose one):
pip install rvc-python      # simplest
# or clone and use: https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI
```

### Expected Improvement

| Dimension | Current (Kokoro + FX) | After RVC |
|---|---|---|
| Timbral emotion | None | Distinct per profile |
| Pitch envelope | Flat | Modulated by model |
| Natural prosody | Speed-only | Full vocal dynamics |
| Latency overhead | 0ms | +80–200ms (GPU) |

### Fallback Behavior

If RVC model is missing or inference fails, `voice_conversion.convert()` returns the input WAV unchanged. Kokoro + FX output is the fallback — users hear the current natural delivery, not silence.

---

## Phase 3 — SSML / Kokoro Markup

Kokoro supports limited markup for emphasis and pausing. Phase 3 investigates using this to add intra-sentence dynamics without RVC overhead.

Target: `<emphasis>huge</emphasis>` → Kokoro renders with +15% amplitude on that word.

---

## Phase 4 — Streaming Prosody

Current: entire response synthesized, then played.  
Target: chunk-by-chunk streaming where chunk 1 plays while chunk 2 is being synthesized.

Already partially supported by the prosody planner's chunk structure — needs frontend changes to the `useVoiceSession` hook to support sequential chunk audio queuing.
