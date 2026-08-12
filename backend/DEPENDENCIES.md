# Dependencies

Audited 2026-07 (platform-stabilization pass). For each package in
`requirements.txt`: where it's actually imported, whether it's used, and
why it exists. Methodology: grepped the entire `backend/` tree for the
package's actual import name (which often differs from its PyPI name).

**Result: zero genuinely unused packages found in `requirements.txt`.**
Every declared dependency has at least one real import site.

## Core

| Package | Import name | Used in | Purpose |
|---|---|---|---|
| fastapi | `fastapi` | 38 files | web framework |
| uvicorn[standard] | `uvicorn` | 6 files | ASGI server |
| pydantic | `pydantic` | 26 files | schemas/validation |
| pydantic-settings | `pydantic_settings` | `config.py` | env-based Settings singleton |
| python-multipart | — (used indirectly by FastAPI) | form/file upload parsing | |
| python-dotenv | `dotenv` | 3 files | `.env` loading |

## Voice pipeline

| Package | Import name | Used in | Purpose |
|---|---|---|---|
| faster-whisper | `faster_whisper` | `voice/whisper_service.py` (2 files) | STT — the actual STT engine (not `openai-whisper`, see below) |
| sounddevice | `sounddevice` | 3 files | audio capture |
| numpy | `numpy` | 21 files | array ops, audio buffers, embeddings |
| scipy | `scipy` | 6 files | signal processing |
| pyttsx3 | `pyttsx3` | 4 files | local TTS |
| kokoro-onnx | `kokoro` | 13 files | local high-quality TTS |
| edge-tts | `edge_tts` | 2 files | cloud TTS fallback |
| soundfile | `soundfile` | 9 files | audio decode |

## AI / LLM

| Package | Import name | Used in | Purpose |
|---|---|---|---|
| openai | `openai` | 28 files | LLM client (also drives Ollama via its OpenAI-compatible base_url) |
| ollama | `ollama` | 2 files | local LLM fallback |
| sentence-transformers | `sentence_transformers` | 3 files | local embeddings (fs semantic search + intent classification — **loaded independently in both**, see below) |
| chromadb | `chromadb` | 5 files, all in `brain/`/`cognition/` | vector store for the `brain/` memory subsystem |

## Filesystem intelligence (Phase 1, this arc)

| Package | Import name | Used in | Purpose |
|---|---|---|---|
| faiss-cpu | `faiss` | `semantic_index.py` | fs semantic search vector index |
| watchdog | `watchdog` | 3 files | real-time filesystem change detection |
| pymupdf | `fitz` | `content_extractor.py` | PDF text extraction |
| python-docx | `docx` | `content_extractor.py` | DOCX text extraction |
| openpyxl | `openpyxl` | `content_extractor.py` | XLSX text extraction |
| python-pptx | `pptx` | `content_extractor.py` | PPTX text extraction |
| rapidfuzz | `rapidfuzz` | 6 files | fuzzy string matching (filename search, phase 1.5 resolution) |

## System monitoring / integrations

| Package | Import name | Used in | Purpose |
|---|---|---|---|
| psutil | `psutil` | 10 files | system monitoring |
| httpx | `httpx` | 4 files (all `api/`) | HTTP client — the current-generation choice |
| google-auth, google-auth-oauthlib, google-auth-httplib2, google-api-python-client | `google.auth`, `google_auth_oauthlib`, `googleapiclient` | 5-6 files each | Gmail integration |
| playwright | `playwright` | 25 files | browser CDP automation |

## Fixed this pass

- **`requests` (>=2.28.0) — added to requirements.txt.** Already declared
  in `pyproject.toml`'s core `dependencies` (which scopes to the
  `src/ai_operator` package), and already installed/working, but missing
  from `requirements.txt` — the file an actual fresh-install follows per
  `CLAUDE.md`'s setup instructions. Used in 7 files: `dev/`,
  `scripts/test_voice_emotion_quality.py`, `scripts/test_rvc_pipeline.py`,
  `src/ai_operator/agents/dev_agent.py`, `src/ai_operator/core/
  content_generator.py`, `src/ai_operator/core/linkedin_api_helper.py`,
  `voice/voice_command_router.py`. `src/ai_operator` is imported at
  backend startup (`main.py`), so this was a real gap: a clean checkout +
  `pip install -r requirements.txt` would be missing a dependency the app
  needs at boot.
- **`pyproject.toml`'s `[voice]` extra — removed `openai-whisper` and
  `pyaudio`.** Zero import sites anywhere in the codebase for either.
  `faster-whisper` (a different, already-declared package) is the actual
  STT engine — these were dead declarations, not a live alternative path.

## Not fixed this pass — documented for a future consolidation

- **`requests` vs `httpx` split**: not an accidental duplicate so much as
  an old-vs-new boundary. All current `api/` code uses `httpx`
  consistently; `requests` is used only by the legacy/adjacent trees
  (`dev/`, `scripts/`, `src/ai_operator/`). Consolidating those call sites
  onto `httpx` would let `requests` be dropped again — a real but
  cross-cutting change, out of scope for a dependency-list fix.
- **SentenceTransformer loaded twice**: `semantic_index.py` (fs semantic
  search) and `intent_router.py` (Tier 3 semantic classifier) each
  instantiate their own `SentenceTransformer("all-MiniLM-L6-v2")`. Measured
  cold load time: **~19 seconds**. Not a duplicate *package* (both use
  `sentence-transformers` correctly), but a duplicate *runtime load* of the
  same model — real, measured cost. See TECHNICAL_DEBT.md.
- **PowerShell invocation pattern, not a package**: 24 files still spawn
  `powershell.exe` directly via `subprocess.run` (cold: ~400-800ms) instead
  of the warm persistent session in `ps_session.py` (~20-90ms). Flagged
  here because it's the same "two ways to do one thing" audit finding, just
  at the code-pattern level rather than the package level. See
  TECHNICAL_DEBT.md.

## Installed but not required

`pip list` shows ~417 installed packages against ~29 declared lines in
`requirements.txt` — the rest are ordinary transitive dependencies pulled
in by the packages above, not a sign of drift on their own.
