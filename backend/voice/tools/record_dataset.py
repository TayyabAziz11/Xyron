"""
Record a labeled wake-word dataset for OWW training.

Usage
─────
  python -m voice.tools.record_dataset                  # interactive menu
  python -m voice.tools.record_dataset --keyword hey_xyron --count 100
  python -m voice.tools.record_dataset --keyword negative  --count 150

Output structure
────────────────
  dataset/
    hey_xyron/      0001.wav … N.wav
    wakeup_xyron/   0001.wav …
    xyron/          0001.wav …
    hey_xeron/      0001.wav …
    hi_xyron/       0001.wav …
    negative/       0001.wav …

Recording spec
──────────────
  - 16 kHz mono PCM-16
  - 2-second clips (just enough for a single wake phrase)
  - Visual countdown + RMS meter in terminal
  - Pause between clips (configurable, default 0.8 s)
  - Skip/re-record with keyboard: SPACE=skip, Q=quit

Dependencies: sounddevice, soundfile  (pip install sounddevice soundfile)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure the script works regardless of CWD
_BACKEND = Path(__file__).parent.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import numpy as np

# ── Dataset root ──────────────────────────────────────────────────────────────
DATASET_DIR = Path(__file__).parent.parent.parent.parent / "dataset"

KEYWORDS = [
    "hey_xyron",
    "wakeup_xyron",
    "xyron",
    "hey_xeron",
    "hi_xyron",
    "negative",
]

PROMPTS: dict[str, list[str]] = {
    "hey_xyron":    ["Say: 'hey xyron'", "Natural speed", "Say it like you mean it"],
    "wakeup_xyron": ["Say: 'wake up xyron'", "Slightly slower", "Normal conversational tone"],
    "xyron":        ["Say: 'xyron'", "Just the name, clearly", "Try varying pitch slightly"],
    "hey_xeron":    ["Say: 'hey xeron' (alternate spelling)", "Clear and natural"],
    "hi_xyron":     ["Say: 'hi xyron'", "Casual greeting tone"],
    "negative":     [
        "Speak any random sentence (not a wake phrase)",
        "Background noise — don't speak",
        "Cough, laugh, or make other sounds",
        "Say something similar but wrong: 'hey Byron', 'hey neutron'",
        "Count to five slowly",
    ],
}

SR        = 16_000
CLIP_SECS = 2.0
CLIP_SAMP = int(SR * CLIP_SECS)
PAUSE_S   = 0.8     # between clips
RMS_BAR   = 40      # terminal width for RMS meter


def _rms(arr: np.ndarray) -> float:
    return float(np.sqrt(np.mean(arr.astype(np.float32) ** 2))) if len(arr) else 0.0


def _rms_bar(rms: float, width: int = RMS_BAR) -> str:
    filled = int(min(1.0, rms / 0.05) * width)
    bar = "█" * filled + "░" * (width - filled)
    level = "LOW " if rms < 0.005 else ("OK  " if rms < 0.04 else "HIGH")
    return f"[{bar}] {level} rms={rms:.4f}"


def record_clip(prompt: str) -> np.ndarray | None:
    """Record one clip.  Returns int16 array or None on skip/quit."""
    try:
        import sounddevice as sd
    except ImportError:
        print("ERROR: sounddevice not installed.  Run: pip install sounddevice")
        sys.exit(1)

    print(f"\n  Prompt: {prompt}")
    for i in range(3, 0, -1):
        print(f"  Recording in {i}...", end="\r", flush=True)
        time.sleep(1.0)
    print("  >>> RECORDING <<<          ", flush=True)

    audio = sd.rec(CLIP_SAMP, samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    arr = audio.flatten()

    rms = _rms(arr)
    print(f"  {_rms_bar(rms)}")
    if rms < 0.002:
        print("  [!] Very quiet — clip may be silent.  Press ENTER to keep, 's' to skip.")
        ans = input("  Keep/skip? [enter/s] ").strip().lower()
        if ans == "s":
            return None

    return arr


def next_index(kw_dir: Path) -> int:
    existing = sorted(kw_dir.glob("*.wav"))
    if not existing:
        return 1
    return int(existing[-1].stem) + 1


def record_keyword(keyword: str, target_count: int) -> None:
    try:
        import soundfile as sf
    except ImportError:
        print("ERROR: soundfile not installed.  Run: pip install soundfile")
        sys.exit(1)

    kw_dir = DATASET_DIR / keyword
    kw_dir.mkdir(parents=True, exist_ok=True)

    prompts_cycle = PROMPTS.get(keyword, [f"Say: '{keyword.replace('_', ' ')}'"])
    idx = next_index(kw_dir)
    recorded = 0

    print(f"\n{'─'*60}")
    print(f"  Keyword : {keyword}")
    print(f"  Target  : {target_count} clips")
    print(f"  Saved to: {kw_dir}")
    print(f"  Tip     : Press Ctrl+C to stop early")
    print(f"{'─'*60}")

    try:
        while recorded < target_count:
            prompt = prompts_cycle[(idx - 1) % len(prompts_cycle)]
            print(f"\n  [{recorded + 1}/{target_count}]  clip #{idx:04d}")

            arr = record_clip(prompt)
            if arr is None:
                print("  [skipped]")
                continue

            out_path = kw_dir / f"{idx:04d}.wav"
            import soundfile as sf
            sf.write(str(out_path), arr, SR, subtype="PCM_16")
            print(f"  Saved → {out_path.name}")

            idx += 1
            recorded += 1

            if recorded < target_count:
                time.sleep(PAUSE_S)

    except KeyboardInterrupt:
        print(f"\n\n  Stopped early — recorded {recorded} clips for '{keyword}'")

    print(f"\n  Done.  Total clips in {kw_dir}: {len(list(kw_dir.glob('*.wav')))}")


def interactive_menu() -> None:
    print("\n╔══════════════════════════════════════╗")
    print("║   Xyron Wake-Word Dataset Recorder   ║")
    print("╚══════════════════════════════════════╝")
    print("\nKeywords:")
    for i, kw in enumerate(KEYWORDS, 1):
        existing = len(list((DATASET_DIR / kw).glob("*.wav"))) if (DATASET_DIR / kw).exists() else 0
        print(f"  {i}. {kw:<20} ({existing} clips)")

    print("\nOptions:")
    print("  Enter number to record a keyword")
    print("  'a' to record ALL keywords")
    print("  'q' to quit")

    choice = input("\nChoice: ").strip().lower()
    if choice == "q":
        return
    if choice == "a":
        count = int(input("Clips per keyword [100]: ").strip() or "100")
        for kw in KEYWORDS:
            record_keyword(kw, count)
        return

    try:
        idx = int(choice) - 1
        kw = KEYWORDS[idx]
        count = int(input(f"Number of clips for '{kw}' [100]: ").strip() or "100")
        record_keyword(kw, count)
    except (ValueError, IndexError):
        print("Invalid choice.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record wake-word dataset clips")
    parser.add_argument("--keyword", choices=KEYWORDS + ["all"], help="Keyword to record")
    parser.add_argument("--count", type=int, default=100, help="Number of clips to record")
    args = parser.parse_args()

    if args.keyword is None:
        interactive_menu()
    elif args.keyword == "all":
        for kw in KEYWORDS:
            record_keyword(kw, args.count)
    else:
        record_keyword(args.keyword, args.count)


if __name__ == "__main__":
    main()
