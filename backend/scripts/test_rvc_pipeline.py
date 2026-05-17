"""
RVC pipeline comparison test — generates WAV samples to compare tiers.

Usage:
    cd /mnt/e/Xyron/backend
    PYTHONPATH=/mnt/e/Xyron/backend python3 scripts/test_rvc_pipeline.py

Output:
    data/rvc_tests/kokoro_only.wav
    data/rvc_tests/rvc_<preset>.wav   (one per preset)
    data/rvc_tests/report.txt
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_URL = "http://localhost:8000/api/v1"
OUT_DIR  = Path(__file__).parent.parent / "data" / "rvc_tests"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TEST_TEXT = "Wait… you upgraded my memory? That's actually huge. Better memory means I can finally keep context properly instead of rebuilding every session."

PRESETS = ["neutral", "hyped", "relieved", "dominant", "protective", "calm", "late_night", "audience_mode"]


def synthesize(text: str) -> bytes | None:
    try:
        r = requests.post(
            f"{BASE_URL}/voice/synthesize-stream",
            json={"text": text, "voice": "nova", "speed": 1.0},
            timeout=30,
        )
        if r.status_code == 200 and "audio" in r.headers.get("content-type", ""):
            return r.content
    except Exception as e:
        print(f"  [synthesize ERROR] {e}")
    return None


def rvc_status() -> dict:
    try:
        r = requests.get(f"{BASE_URL}/voice/rvc-status", timeout=5)
        if r.status_code == 200:
            return r.json().get("data", {})
    except Exception:
        pass
    return {}


def direct_rvc_test(preset: str) -> tuple[bytes | None, float]:
    """Call rvc_engine directly for isolated testing (no HTTP)."""
    from voice.rvc_engine import rvc_engine
    from voice.rvc_engine import _float_to_wav
    import numpy as np

    # Load kokoro audio first
    wav = synthesize(TEST_TEXT)
    if not wav:
        return None, 0.0

    t0 = time.monotonic()
    out = rvc_engine.convert(wav, preset=preset, emotion_state=preset.upper(), intensity="HIGH")
    ms = (time.monotonic() - t0) * 1000
    return out, ms


def main() -> None:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    os.environ.setdefault("PYTHONPATH", str(Path(__file__).parent.parent))

    report: list[str] = ["Xyron RVC Pipeline Comparison Report", "=" * 60]

    # Check RVC status
    status = rvc_status()
    if status:
        print(f"\nRVC Status: tier={status.get('tier')} enabled={status.get('enabled')}")
        print(f"  Presets: {status.get('available_presets', [])}")
        report.append(f"tier={status.get('tier')}  enabled={status.get('enabled')}")
    else:
        print("\nRVC Status: (backend not reachable, using direct import)")

    # 1. Kokoro-only baseline
    print(f"\n{'='*60}")
    print("BASELINE: Kokoro only")
    print(f"TEXT: {TEST_TEXT[:80]!r}")
    t0 = time.monotonic()
    kokoro_wav = synthesize(TEST_TEXT)
    ms = (time.monotonic() - t0) * 1000
    if kokoro_wav:
        out_path = OUT_DIR / "kokoro_only.wav"
        out_path.write_bytes(kokoro_wav)
        print(f"  saved → kokoro_only.wav ({len(kokoro_wav)} bytes, {ms:.0f}ms)")
        report.append(f"\nkokoro_only: {len(kokoro_wav)}B  {ms:.0f}ms")
    else:
        print("  FAILED — is backend running?")

    # 2. RVC per-preset
    from voice.rvc_engine import rvc_engine
    print(f"\nRVC tier: {rvc_engine.get_tier()}")

    for preset in PRESETS:
        print(f"\n{'='*60}")
        print(f"PRESET: {preset}")
        result, ms = direct_rvc_test(preset)
        if result and result != kokoro_wav:
            out_path = OUT_DIR / f"rvc_{preset}.wav"
            out_path.write_bytes(result)
            size_diff = len(result) - (len(kokoro_wav) if kokoro_wav else 0)
            print(f"  saved → rvc_{preset}.wav ({len(result)}B, {ms:.0f}ms, Δsize={size_diff:+d}B)")
            report.append(f"rvc_{preset}: {len(result)}B  {ms:.0f}ms  Δsize={size_diff:+d}B")
        elif result == kokoro_wav:
            print(f"  passthrough (no conversion applied — tier={rvc_engine.get_tier()})")
            report.append(f"rvc_{preset}: passthrough")
        else:
            print("  FAILED")
            report.append(f"rvc_{preset}: FAILED")

    report_path = OUT_DIR / "report.txt"
    report_path.write_text("\n".join(report))
    print(f"\n\nReport → {report_path}")
    print(f"WAV files → {OUT_DIR}/")


if __name__ == "__main__":
    main()
