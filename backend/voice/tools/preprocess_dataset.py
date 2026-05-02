"""
Preprocess wake-word dataset clips for OWW training.

What it does
────────────
1. Scans dataset/{keyword}/*.wav
2. Converts every clip to 16kHz mono float32 WAV
3. Trims leading/trailing silence (energy-based)
4. Normalizes RMS to -23 dBFS  (peak-protect: no clipping)
5. Pads/trims to exactly 2 seconds
6. Writes to dataset_processed/{keyword}/*.wav
7. (Optional) Augmentation pass: speed ±5%, pitch ±1 semitone, noise injection

Usage
─────
  python -m voice.tools.preprocess_dataset            # process all
  python -m voice.tools.preprocess_dataset --keyword hey_xyron
  python -m voice.tools.preprocess_dataset --augment  # also augment
  python -m voice.tools.preprocess_dataset --stats    # show per-keyword stats only

Dependencies: soundfile, scipy  (pip install soundfile scipy)
Optional for augmentation: audiomentations  (pip install audiomentations)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATASET_DIR   = Path(__file__).parent.parent.parent.parent / "dataset"
PROCESSED_DIR = Path(__file__).parent.parent.parent.parent / "dataset_processed"

SR           = 16_000
CLIP_SECS    = 2.0
CLIP_SAMP    = int(SR * CLIP_SECS)
TARGET_RMS   = 0.03    # ~-23 dBFS
SILENCE_TRIM = 0.01    # RMS below this is silence


def _load_audio(path: Path) -> tuple[np.ndarray, int] | None:
    try:
        import soundfile as sf
        audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio, sr
    except Exception as exc:
        log.warning("Cannot load %s: %s", path.name, exc)
        return None


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return audio
    import scipy.signal as sps
    return sps.resample(audio, int(len(audio) * dst_sr / src_sr)).astype(np.float32)


def _trim_silence(audio: np.ndarray, frame_ms: int = 20) -> np.ndarray:
    frame = int(SR * frame_ms / 1000)
    # Find first/last frame with energy above threshold
    start = 0
    end = len(audio)
    for i in range(0, len(audio) - frame, frame):
        if _rms(audio[i:i + frame]) > SILENCE_TRIM:
            start = max(0, i - frame)  # keep one frame before speech
            break
    for i in range(len(audio) - frame, 0, -frame):
        if _rms(audio[i:i + frame]) > SILENCE_TRIM:
            end = min(len(audio), i + 2 * frame)
            break
    trimmed = audio[start:end]
    return trimmed if len(trimmed) > frame else audio  # don't over-trim


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio ** 2))) if len(audio) > 0 else 0.0


def _normalize(audio: np.ndarray) -> np.ndarray:
    rms = _rms(audio)
    if rms < 1e-6:
        return audio
    scale = TARGET_RMS / rms
    # Peak-protect: don't clip
    peak = np.max(np.abs(audio))
    if peak * scale > 0.99:
        scale = 0.99 / peak
    return (audio * scale).astype(np.float32)


def _pad_or_trim(audio: np.ndarray) -> np.ndarray:
    if len(audio) >= CLIP_SAMP:
        return audio[:CLIP_SAMP]
    pad = np.zeros(CLIP_SAMP - len(audio), dtype=np.float32)
    # Center the audio in the clip
    pad_left  = (CLIP_SAMP - len(audio)) // 2
    pad_right = CLIP_SAMP - len(audio) - pad_left
    return np.concatenate([
        np.zeros(pad_left,  dtype=np.float32),
        audio,
        np.zeros(pad_right, dtype=np.float32),
    ])


def process_clip(src: Path, dst: Path) -> bool:
    result = _load_audio(src)
    if result is None:
        return False
    audio, sr = result
    audio = _resample(audio, sr, SR)
    audio = _trim_silence(audio)
    audio = _normalize(audio)
    audio = _pad_or_trim(audio)

    try:
        import soundfile as sf
        sf.write(str(dst), audio, SR, subtype="PCM_16")
        return True
    except Exception as exc:
        log.warning("Cannot write %s: %s", dst, exc)
        return False


# ── Augmentation ──────────────────────────────────────────────────────────────

def _augment_clip(audio: np.ndarray) -> list[np.ndarray]:
    """Return a list of augmented variants from one clip."""
    variants: list[np.ndarray] = []

    # Speed stretch ±5%
    try:
        import scipy.signal as sps
        for factor in (0.95, 1.05):
            stretched = sps.resample(audio, int(len(audio) / factor)).astype(np.float32)
            variants.append(_pad_or_trim(_normalize(stretched)))
    except Exception:
        pass

    # Gaussian noise injection
    for snr_db in (20, 15):
        signal_power = np.mean(audio ** 2)
        noise_power  = signal_power / (10 ** (snr_db / 10))
        noise = np.random.normal(0, np.sqrt(noise_power), len(audio)).astype(np.float32)
        variants.append(_pad_or_trim(_normalize(audio + noise)))

    # Volume variation ±6dB
    for gain in (0.5, 2.0):
        variants.append(_pad_or_trim(_normalize(audio * gain)))

    return variants


def augment_keyword(keyword: str) -> int:
    proc_dir = PROCESSED_DIR / keyword
    if not proc_dir.exists():
        log.warning("No processed clips for %s — run without --augment first", keyword)
        return 0

    aug_dir = PROCESSED_DIR / f"{keyword}_aug"
    aug_dir.mkdir(parents=True, exist_ok=True)

    clips = sorted(proc_dir.glob("*.wav"))
    count = 0
    for clip_path in clips:
        result = _load_audio(clip_path)
        if result is None:
            continue
        audio, _ = result
        for i, variant in enumerate(_augment_clip(audio)):
            try:
                import soundfile as sf
                out = aug_dir / f"{clip_path.stem}_aug{i:02d}.wav"
                sf.write(str(out), variant, SR, subtype="PCM_16")
                count += 1
            except Exception:
                pass

    log.info("[%s] augmented → %d clips in %s", keyword, count, aug_dir)
    return count


# ── Stats ─────────────────────────────────────────────────────────────────────

def print_stats() -> None:
    print("\n  Dataset Statistics")
    print(f"  {'Keyword':<22} {'Raw':>6} {'Processed':>10} {'Augmented':>10}")
    print(f"  {'─'*22} {'─'*6} {'─'*10} {'─'*10}")

    keywords = [d.name for d in DATASET_DIR.iterdir() if d.is_dir()] if DATASET_DIR.exists() else []
    for kw in sorted(keywords):
        raw_n  = len(list((DATASET_DIR / kw).glob("*.wav")))
        proc_n = len(list((PROCESSED_DIR / kw).glob("*.wav"))) if (PROCESSED_DIR / kw).exists() else 0
        aug_n  = len(list((PROCESSED_DIR / f"{kw}_aug").glob("*.wav"))) if (PROCESSED_DIR / f"{kw}_aug").exists() else 0
        print(f"  {kw:<22} {raw_n:>6} {proc_n:>10} {aug_n:>10}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def process_keyword(keyword: str) -> int:
    src_dir = DATASET_DIR / keyword
    if not src_dir.exists():
        log.warning("No dataset directory: %s", src_dir)
        return 0

    dst_dir = PROCESSED_DIR / keyword
    dst_dir.mkdir(parents=True, exist_ok=True)

    clips   = sorted(src_dir.glob("*.wav"))
    ok = fail = 0
    for clip in clips:
        dst = dst_dir / clip.name
        if process_clip(clip, dst):
            ok += 1
        else:
            fail += 1

    log.info("[%s] processed %d/%d clips → %s", keyword, ok, ok + fail, dst_dir)
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Preprocess wake-word dataset")
    parser.add_argument("--keyword", help="Single keyword to process (default: all)")
    parser.add_argument("--augment", action="store_true", help="Also generate augmented clips")
    parser.add_argument("--stats",   action="store_true", help="Print stats and exit")
    args = parser.parse_args()

    if args.stats:
        print_stats()
        return

    keywords = [args.keyword] if args.keyword else [
        d.name for d in sorted(DATASET_DIR.iterdir()) if d.is_dir()
    ] if DATASET_DIR.exists() else []

    if not keywords:
        log.error("No dataset found at %s — record clips first.", DATASET_DIR)
        return

    for kw in keywords:
        process_keyword(kw)
        if args.augment:
            augment_keyword(kw)

    print_stats()


if __name__ == "__main__":
    main()
