# Manual Real-Speech Validation — Urdu / Roman Urdu / Mixed

Synthetic (espeak/TTS-generated) audio proves nothing about real Urdu STT
quality — this must be validated by actually speaking these commands.
Nothing in this pass claims Urdu speech recognition is production-ready;
that claim can only be made after this checklist is run against real speech.

## How to capture the trace

Every turn now logs one line: `[ML_TURN] original=... stt_model=... stt_conf=...
detected_lang=... normalized=... fast_path=... local_qwen_used=... intent=...
response_lang=... tts_engine=... total_ms=...`

After each command below, grab the matching `[ML_TURN]` line from the backend
log (or `grep ML_TURN` over the session's log output) and send it back —
that's enough to tell what the pipeline actually did without re-running it
myself.

## 1. Roman Urdu — should stay on the fast path (no Qwen)

- [ ] Chrome kholo
- [ ] Settings kholo
- [ ] mera Xyron folder open karo
- [ ] Downloads mein invoices dhundo
- [ ] kal wali presentation kholo
- [ ] pending orders dikhao
- [ ] meri screen pe kya hai
- [ ] Downloads mein invoices dhundo
- [ ] latest GitHub project open karo
- [ ] mujhe Urdu mein jawab do

**Expect:** `detected_lang=ur_roman`, most of these `fast_path=True local_qwen_used=False`
(a few — "Downloads mein invoices dhundo", "meri screen pe kya hai" — may
legitimately fall to `local_qwen_used=True` since they're not exact
`ml_normalizer` matches; that's correct behavior, not a bug).

## 2. Urdu script / spoken Urdu

- [ ] کروم کھولو
- [ ] سیٹنگز کھولو
- [ ] میری سکرین پر کیا ہے
- [ ] آج کے آرڈرز دکھاؤ
- [ ] کل والی پریزنٹیشن کھولو
- [ ] ڈاؤن لوڈز میں انوائسز تلاش کرو

**Expect:** `detected_lang=ur`. First two should be `fast_path=True` (exact
`ml_normalizer` matches). The rest may use `local_qwen_used=True`.
`response_lang` should come back `ur` and be spoken via XTTS unless the
`[ML_TTS_COLD_START]` log shows it was still warming up for that turn — if
so, it should switch to XTTS within the next turn or two, not the whole
session (see `[XTTS_BG_LOAD_STARTED]` / `[XTTS_INIT_DONE]`).

## 3. Mixed Urdu-English (code-switching)

- [ ] WhatsApp orders sales sheet ke saath compare karo
- [ ] GitHub pe mera latest project kholo
- [ ] Chrome mein latest AI news search karo
- [ ] is PDF ko Urdu mein summarize karo

**Expect:** `detected_lang=mixed` or `ur_roman`. "compare"/"summarize" style
requests have no direct tool (nothing fabricates one) — expect
`local_qwen_used=True`, `intent=general_query`, and a natural-language
`response_lang`-matched answer, not silence or an English echo.

## 4. Full hackathon workflow — language memory + follow-ups

Speak these back-to-back in one session:

1. "Xyron, aaj ke orders check karo aur sales files ke saath compare karke batao kya pending hai."
2. "Pending wale dikhao."
3. "Sirf unpaid wale."
4. "Ab Urdu mein batao."
5. "English mein explain karo."

**Expect:** language stays `ur_mixed`/`ur_roman` through turns 1-3 (no need
to restate the task), switches to `ur` at turn 4 and `en` at turn 5, and the
active task/entities from turn 1 are still referenced correctly at turns 2-3
(check via `context_stack`/`follow_up_resolver_v2` logs, unchanged by this
work).

## 5. Regression spot-check — must be unaffected

- [ ] "What time is it" (plain English, fast path)
- [ ] "Open Chrome" (plain English, fast path)
- [ ] Wake word still triggers normally
- [ ] A normal English conversational question still answers correctly and quickly

## What "pass" looks like

- Fast, obvious commands (English or Roman Urdu) never show `local_qwen_used=True`.
- Complex/unmatched Urdu commands get a real structured `intent` (not `unknown`)
  and either execute the right tool or get a sensible natural-language answer
  in the right language — never silence, never "I heard: ...", never a forced
  English reply to Urdu input.
- Session language sticks across short follow-ups and switches immediately on
  an explicit request ("Urdu mein batao" / "English mein explain karo").

## What this checklist does NOT claim

- It does not claim Whisper's Urdu transcription accuracy is good — only that
  *if* the transcript comes out reasonably right, the rest of the pipeline
  (comprehension → routing → response language → TTS) behaves correctly.
  Whisper "small" accuracy on your actual voice/accent/mic is exactly what
  this run is meant to reveal.
- It does not claim XTTS's Urdu pronunciation is native-quality — it borrows
  Arabic phonemes as documented in `voice/xtts_service.py`. Listening to it
  is a separate, useful signal for the pronunciation-preprocessor work in
  `voice/pronunciation_preprocessor.py`, which currently has no overrides
  applied (pass-through) pending exactly this kind of listening test.
