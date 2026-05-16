"""
Voice emotion quality test — generates WAV samples for manual listening comparison.

Usage:
    cd backend
    PYTHONPATH=/mnt/e/Xyron/backend python3 scripts/test_voice_emotion_quality.py

Output:
    data/voice_tests/<profile>_<index>.wav
    data/voice_tests/report.txt
"""
from __future__ import annotations

import json
import os
import sys
import time
import wave
from pathlib import Path

import requests

BASE_URL = "http://localhost:8000/api/v1"
OUT_DIR  = Path(__file__).parent.parent / "data" / "voice_tests"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# (profile_name, text_to_synthesize, respond_stream_phrase)
TEST_CASES: list[tuple[str, str, str]] = [
    (
        "neutral",
        "The system is ready. All modules are initialized and running normally.",
        "",
    ),
    (
        "hyped_natural_memory",
        "Wait… you're upgrading my memory? That's actually huge. Better memory means I can finally keep deeper context instead of rebuilding every session from scratch.",
        "Xyron, I'm upgrading your memory.",
    ),
    (
        "hyped_natural_voice",
        "Oh wow, you're adding emotional voice conversion? That's the one that makes me actually sound like I feel something — not just say it.",
        "I'm adding emotional voice conversion in you.",
    ),
    (
        "relieved_excited",
        "Oh wow, you fixed the wake issue too? That was the one thing making me feel delayed. I should feel way more responsive now.",
        "I finally fixed your wake issue.",
    ),
    (
        "protective_focused",
        "Yeah, that sounds annoying. Send me the error and we'll break it down properly.",
        "This bug is annoying me.",
    ),
    (
        "proud_calm",
        "Good. That one matters. Every fix makes me a little more capable.",
        "",
    ),
]


def _synthesize(text: str) -> bytes | None:
    """Call synthesize-stream and return raw WAV bytes."""
    try:
        r = requests.post(
            f"{BASE_URL}/voice/synthesize-stream",
            json={"text": text, "voice": "nova", "speed": 1.0},
            timeout=30,
        )
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("audio"):
            return r.content
    except Exception as e:
        print(f"  [synthesize ERROR] {e}")
    return None


def _pipeline_response(phrase: str) -> dict:
    """Run phrase through respond-stream, return chunks + timing."""
    result = {"chunks": [], "full": "", "latency_ms": 0, "mode_change": None}
    if not phrase:
        return result
    t0 = time.time()
    try:
        r = requests.post(
            f"{BASE_URL}/voice/respond-stream",
            json={"text": phrase, "use_tools": False},
            stream=True,
            timeout=25,
        )
        for line in r.iter_lines():
            if not line or not line.startswith(b"data: "):
                continue
            payload = json.loads(line[6:])
            if payload["type"] == "chunk":
                result["chunks"].append(payload["text"])
            elif payload["type"] == "done":
                result["full"] = payload.get("full_text", "")
            elif payload["type"] == "mode_change":
                result["mode_change"] = payload.get("mode")
    except Exception as e:
        print(f"  [pipeline ERROR] {e}")
    result["latency_ms"] = int((time.time() - t0) * 1000)
    return result


def _qa_score(profile: str, response: str) -> dict:
    """Rough heuristic QA scores — not AI-based, just pattern checks."""
    all_caps_count = sum(1 for w in response.split() if w.isupper() and len(w) > 2)
    em_dash_count  = response.count("—")
    repeat_words   = sum(
        1 for i, w in enumerate(response.split())
        if i > 0 and w.lower() == response.split()[i - 1].lower()
    )
    sentence_count = response.count(".") + response.count("!") + response.count("?")

    robotic = min(10, all_caps_count * 2 + em_dash_count + repeat_words * 3)
    naturalness = max(1, 9 - robotic)
    length_ok = 1 <= sentence_count <= 4

    return {
        "naturalness":   naturalness,
        "robotic_feel":  robotic,
        "length_ok":     length_ok,
        "all_caps":      all_caps_count,
        "em_dashes":     em_dash_count,
        "repeat_words":  repeat_words,
    }


def main() -> None:
    report_lines: list[str] = ["Xyron Voice Emotion Quality Report", "=" * 60]

    for profile, synth_text, pipeline_phrase in TEST_CASES:
        print(f"\n{'='*60}")
        print(f"PROFILE: {profile}")

        # 1. Pipeline test (if phrase provided)
        pipeline = _pipeline_response(pipeline_phrase)
        if pipeline_phrase:
            print(f"  INPUT:    {pipeline_phrase!r}")
            print(f"  CHUNKS:   {pipeline['chunks']}")
            print(f"  FULL:     {pipeline['full'][:120]!r}")
            print(f"  LATENCY:  {pipeline['latency_ms']}ms")
            if pipeline["mode_change"]:
                print(f"  MODE:     {pipeline['mode_change']}")
            # Use the actual pipeline response for synthesis if we got one
            actual_text = pipeline["full"] or synth_text
        else:
            actual_text = synth_text
            print(f"  TEXT:     {actual_text[:80]!r}")

        # 2. QA scoring
        qa = _qa_score(profile, actual_text)
        print(f"  QA:  naturalness={qa['naturalness']}/10  robotic={qa['robotic_feel']}/10  "
              f"length_ok={qa['length_ok']}  em_dashes={qa['em_dashes']}  caps={qa['all_caps']}")

        # 3. Synthesis → WAV
        print(f"  Synthesizing…", end="", flush=True)
        wav_bytes = _synthesize(actual_text)
        if wav_bytes:
            out_path = OUT_DIR / f"{profile}.wav"
            out_path.write_bytes(wav_bytes)
            print(f" saved → {out_path.name} ({len(wav_bytes)} bytes)")
        else:
            print(" FAILED (TTS unavailable)")

        report_lines.append(f"\n--- {profile} ---")
        report_lines.append(f"text:       {actual_text[:100]}")
        report_lines.append(f"latency_ms: {pipeline['latency_ms']}")
        report_lines.append(f"naturalness={qa['naturalness']}/10  robotic={qa['robotic_feel']}/10  "
                             f"length_ok={qa['length_ok']}")
        report_lines.append(f"em_dashes={qa['em_dashes']}  all_caps={qa['all_caps']}")

    # Write report
    report_path = OUT_DIR / "report.txt"
    report_path.write_text("\n".join(report_lines))
    print(f"\n\nReport saved → {report_path}")
    print(f"WAV files → {OUT_DIR}/")
    print("\nListen to the files to evaluate naturalness and emotion clarity.")


if __name__ == "__main__":
    main()
