"""
Pronunciation lexicon -- TTS-friendly spoken forms for words Kokoro mispronounces.

Applied as whole-word substitutions before Kokoro synthesis so espeak-ng
phonemizes correctly. Only include words with confirmed mispronunciation issues.
"""
from __future__ import annotations

# ── Brand / product names ─────────────────────────────────────────────────────

BRAND_NAMES: dict[str, str] = {
    # "Xy" is read as two letters X,Y by espeak -- "Zyron" sounds correct
    "Xyron":     "Zyron",
    # espeak reads as "Oh-LAY-ma" without nudge
    "Ollama":    "Oh lama",
    # espeak splits "Chroma" and "DB" oddly
    "ChromaDB":  "Kroma D B",
    # espeak sometimes slurs into one unrecognizable sound
    "WhatsApp":  "Whats App",
    # espeak: "Vs-Code" as one syllable
    "VSCode":    "V S Code",
    # espeak reads "API" wrong as a word
    "FastAPI":   "Fast A P I",
    # espeak: "pi-torch" (rhymes with "witch")
    "PyTorch":   "Pie Torch",
    # espeak: "HUB-ert" with wrong stress
    "HuBERT":    "Hoo bert",
    # espeak: wrong initial vowel
    "Uvicorn":   "Yoo vi corn",
    # GitHub, GitLab, Kokoro, Whisper, Mistral are fine in espeak -- excluded
}

# ── Acronyms -- letter-by-letter ─────────────────────────────────────────────

ACRONYMS: dict[str, str] = {
    "API":   "A P I",
    "LLM":   "L L M",
    "LLMs":  "L L Ms",
    "GPU":   "G P U",
    "CPU":   "C P U",
    "VRAM":  "V ram",
    "RTX":   "R T X",
    "GTX":   "G T X",
    "MCP":   "M C P",
    "STT":   "S T T",
    "TTS":   "T T S",
    "WSL":   "W S L",
    "WSL2":  "W S L 2",
    "URL":   "U R L",
    "UI":    "U I",
    "UX":    "U X",
    "IDE":   "I D E",
    "NLP":   "N L P",
    "ASR":   "A S R",
    "VAD":   "V A D",
    "JSON":  "Jay son",
    "HTTP":  "H T T P",
    "SSH":   "S S H",
    "ONNX":  "O N N X",
}

# ── Technical terms ───────────────────────────────────────────────────────────

TECHNICAL: dict[str, str] = {
    "CUDA":     "Cooda",
    "CUDNN":    "Coo D N N",
    "SQLite":   "S Q Lite",
    "numpy":    "Num pie",
    "scipy":    "Sigh pie",
    "pytest":   "Pie test",
}

# ── Merged lookup (brand overrides technical overrides acronyms) ─────────────

ALL: dict[str, str] = {**ACRONYMS, **TECHNICAL, **BRAND_NAMES}
