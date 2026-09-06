#!/usr/bin/env python3
"""
bench_stt_accuracy_parity.py — companion to bench_stt_latency.py.

Confirms int8_float16 quantization does NOT change transcripts vs float16
on 15 real recorded clips (5 per wake-phrase class) before the compute_type
change is applied to production. Prints any divergence; identical output on
all clips = safe to ship.

Run:  wsl -e bash -lc "cd /mnt/e/Xyron/backend && python3 scripts/bench_stt_accuracy_parity.py"
"""
import time
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

REPO = Path(__file__).resolve().parents[2]
CLIPS = [p for cls in ("hey_xyron", "wakeup_xyron", "xyron")
         for p in sorted((REPO / "dataset" / cls).glob("*.wav"))[:5]]

COMMON = dict(beam_size=1, language=None, task="transcribe", vad_filter=True,
              vad_parameters={"min_silence_duration_ms": 300}, temperature=0.0,
              condition_on_previous_text=False, no_repeat_ngram_size=3)


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = pcm.astype(np.float32) / 32768.0
    return audio[:: sr // 16000] if sr != 16000 else audio


def transcribe_all(model) -> dict[str, str]:
    out = {}
    for clip in CLIPS:
        segs, _ = model.transcribe(load_wav(clip), **COMMON)
        out[str(clip)] = " ".join(s.text.strip() for s in segs).strip()
    return out


def main():
    print(f"{len(CLIPS)} clips")
    t0 = time.monotonic()
    fp16 = transcribe_all(WhisperModel("small", device="cuda", compute_type="float16"))
    t16 = time.monotonic() - t0
    t0 = time.monotonic()
    i8 = transcribe_all(WhisperModel("small", device="cuda", compute_type="int8_float16"))
    t8 = time.monotonic() - t0

    diffs = 0
    for clip in CLIPS:
        if fp16[str(clip)] != i8[str(clip)]:
            diffs += 1
            print(f"DIVERGENCE {Path(clip).parent.name}/{Path(clip).name}: "
                  f"fp16={fp16[str(clip)]!r} int8={i8[str(clip)]!r}")
    print(f"\nidentical={len(CLIPS) - diffs}/{len(CLIPS)} divergent={diffs} "
          f"total_time fp16={t16:.1f}s int8_float16={t8:.1f}s")


if __name__ == "__main__":
    main()
