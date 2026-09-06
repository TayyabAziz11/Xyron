#!/usr/bin/env python3
"""
bench_stt_latency.py — Issue 4 evidence run (work order: profile, don't guess).

Benchmarks the multilingual ("accurate") STT path on the real T1200 in WSL:
  A. small  @ float16        (current production config)
  B. small  @ int8_float16   (half encoder VRAM; candidate for 4GB cards)
  C. small  @ int8           (lowest VRAM)
  D. base   @ float16        (smaller multilingual candidate, accuracy trade-off)
Plus on A: cost of initial_prompt and of vad_filter separately.

Audio: two real ~1-2s wake-word WAVs from dataset/ (closest available proxy
for a short Roman-Urdu command — encoder cost scales with audio length, so
latency numbers transfer; accuracy conclusions do NOT and need a real mic).

Run:  wsl -e bash -lc "cd /mnt/e/Xyron/backend && python3 scripts/bench_stt_latency.py"
"""
import statistics
import time
import wave
from pathlib import Path

import numpy as np
import torch
from faster_whisper import WhisperModel

REPO = Path(__file__).resolve().parents[2]
CLIPS = [REPO / "dataset" / "hey_xyron" / "0001.wav",
         REPO / "dataset" / "hey_xyron" / "0002.wav"]

INITIAL_PROMPT = (
    "Xyron, open Chrome. Settings kholo. C drive kholo. YouTube kholo. "
    "Urdu mein baat karo. Open VS Code. Open Settings. Volume up."
)


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = pcm.astype(np.float32) / 32768.0
    if sr != 16000:  # naive decimation is fine for a latency benchmark
        audio = audio[:: sr // 16000]
    return audio


def vram_mb() -> tuple[int, int]:
    free, total = torch.cuda.mem_get_info(0)
    return free // 1048576, total // 1048576


def bench(model, audio, label, n=5, initial_prompt=INITIAL_PROMPT,
          vad_filter=True, language=None):
    common = dict(
        beam_size=1, language=language, task="transcribe",
        vad_filter=vad_filter, vad_parameters={"min_silence_duration_ms": 300},
        temperature=0.0, condition_on_previous_text=False,
        no_repeat_ngram_size=3,
    )
    if initial_prompt:
        common["initial_prompt"] = initial_prompt
    # warmup (first call compiles kernels / allocates buffers)
    for _ in range(2):
        segs, _ = model.transcribe(audio, **common)
        list(segs)
    times, text = [], ""
    for _ in range(n):
        t0 = time.monotonic()
        segs, info = model.transcribe(audio, **common)
        text = " ".join(s.text.strip() for s in segs).strip()
        times.append((time.monotonic() - t0) * 1000)
    print(f"  {label:<44} med={statistics.median(times):7.0f}ms "
          f"min={min(times):7.0f}ms text={text[:48]!r}")
    return statistics.median(times)


def main():
    clips = [load_wav(p) for p in CLIPS]
    audio = clips[0]
    print(f"audio: {CLIPS[0].name} {len(audio)/16000:.2f}s | "
          f"VRAM free={vram_mb()[0]}MB/{vram_mb()[1]}MB")
    results = {}

    for size, ct in [("small", "float16"), ("small", "int8_float16"),
                     ("small", "int8"), ("base", "float16")]:
        free_before, _ = vram_mb()
        t0 = time.monotonic()
        model = WhisperModel(size, device="cuda", compute_type=ct)
        load_ms = (time.monotonic() - t0) * 1000
        free_after, _ = vram_mb()
        print(f"\n[{size} @ {ct}] load={load_ms:.0f}ms "
              f"vram_used={free_before - free_after}MB")
        key = f"{size}@{ct}"
        results[key] = bench(model, audio, "prod-params (prompt+vad)")
        if key == "small@float16":
            results["small@float16 no-prompt"] = bench(
                model, audio, "no initial_prompt", initial_prompt=None)
            results["small@float16 no-vad"] = bench(
                model, audio, "no vad_filter", vad_filter=False)
            results["small@float16 lang=ur"] = bench(
                model, audio, "language='ur' (skip auto-detect)", language="ur")
            if len(clips) > 1:
                results["small@float16 clip2"] = bench(
                    model, clips[1], "prod-params clip2")
        del model
        torch.cuda.empty_cache()
        time.sleep(1)

    print("\n== summary (median ms, short clip, T1200) ==")
    for k, v in sorted(results.items(), key=lambda kv: kv[1]):
        print(f"  {v:7.0f}ms  {k}")


if __name__ == "__main__":
    main()
