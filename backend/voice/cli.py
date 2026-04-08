"""CLI tool for testing the voice pipeline.

Usage:
  python -m backend.voice.cli              # record + transcribe + submit
  python -m backend.voice.cli --no-submit  # record + transcribe only
  python -m backend.voice.cli --list-devices  # list audio input devices
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="AI Operator voice pipeline CLI")
    parser.add_argument("--no-submit", action="store_true", help="Transcribe only, don't submit")
    parser.add_argument("--model", default="base", help="Whisper model size (tiny/base/small)")
    parser.add_argument("--language", default="en", help="Language code")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--list-devices", action="store_true", help="List audio input devices")
    args = parser.parse_args()

    if args.list_devices:
        from .audio_utils import list_input_devices
        devices = list_input_devices()
        print(f"\nAvailable input devices ({len(devices)}):")
        for d in devices:
            print(f"  [{d['index']}] {d['name']} ({d['channels']} ch)")
        return

    from .whisper_service import set_model_size
    set_model_size(args.model)

    from .recorder import PushToTalkRecorder
    from .voice_command_router import VoiceCommandRouter

    recorder = PushToTalkRecorder()

    print("\nAI Operator Voice Pipeline")
    print("-" * 40)
    print("Press Enter to start recording... (speak, then pause)")
    input()
    print("Recording... (speak now, pause to stop)\n")

    result = recorder.record_and_transcribe(language=args.language)
    text = result.get("text", "").strip()

    if not text:
        print("No speech detected.")
        return

    print(f"\nTranscript: {text!r}")
    print(f"   Language:   {result.get('language', '?')}")
    print(f"   Duration:   {result.get('duration', '?')}s")

    if args.no_submit:
        print("\n(--no-submit: skipping API submission)")
        return

    router = VoiceCommandRouter(api_base=args.api)
    print(f"\nSubmitting to {args.api}...")
    response = router.submit(text)

    if "error" in response:
        print(f"\nError: {response['error']}")
    else:
        data = response.get("data", response)
        print(f"\nCommand submitted!")
        print(f"   ID:     {data.get('id', '?')}")
        print(f"   Status: {data.get('status', '?')}")
        intent = data.get("intent", {})
        if intent:
            print(f"   Agent:  {intent.get('agent', '?')}")
            print(f"   Skill:  {intent.get('skill', '?')}")


if __name__ == "__main__":
    main()
