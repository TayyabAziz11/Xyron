# Xyron Multilingual Voice Reference

Place a clean 6–10 second WAV file here named:

    xyron_multilingual_reference.wav

Requirements:
- Single speaker, no background noise
- 16kHz or 22kHz sample rate, mono
- Content: any clear speech (the voice quality matters, not the words)
- Format: WAV (PCM 16-bit)

This file is used by XTTS-v2 for voice cloning — non-English responses will
sound like this voice instead of a random default speaker.

If this file is absent, XTTS-v2 will use its built-in default speaker and
log [XTTS_VOICE_REFERENCE_MISSING] at startup of each multilingual synthesis.

To generate a reference from existing Kokoro audio:
  Record a Kokoro "nova" TTS output from a long sentence, trim silence,
  save as xyron_multilingual_reference.wav in this folder.

Do NOT commit binary audio files to the repository — add to .gitignore.
