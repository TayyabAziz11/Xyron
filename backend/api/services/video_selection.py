"""
video_selection.py — title-based candidate selection for pending YouTube lists.

When search_youtube leaves a disambiguation list (pending_video_candidates),
the user picks by ordinal ("second one"), by pointer ("this one") — both
handled in voice_ws.py Tier 0f4 — or BY TITLE ("play love me like you do").
The title path must survive Whisper noise: the live bug was STT transcribing
"play love me like you do" as "Learn love me like you do.", which matched
nothing and fell through to LLM babble.

Strategy: strip leading filler/noise words (including misheard verbs), drop
decorative title tokens (Lyrics/Official/HD/...), then score each candidate
by the fraction of significant utterance tokens found in its title. A
near-exact substring match short-circuits to the top score.

Pure functions, no I/O — unit-tested in tests/test_context_memory_recall.py.
Logs: none (caller logs [YOUTUBE_SELECTION_TITLE_MATCH]).
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Leading words that carry no selection signal: confirmations, politeness,
# pronouns, and the generic media verbs. Repeatedly stripped so "no, i say
# play ..." collapses to just the title. "learn" is included because Whisper
# tiny.en demonstrably mishears "play" → "Learn" in this exact flow.
_NOISE_LEAD_RE = re.compile(
    r'^(?:\s*(?:'
    r'no|nope|yes|yeah|please|okay|ok|okaye|um|uh|erm|well|hey|xyron|'
    r'i\s+say|i\s+said|i\s+want(?:\s+to|s)?|i\'?d\s+like|can\s+you|could\s+you|'
    r'would\s+you|will\s+you|just|now|then|here|there|'
    r'play|watch|select|choose|pick|open|put|start|turn|learn|search|find'
    r')\s*[,.:!]?\s*)+',
    re.IGNORECASE,
)

# Decorative tokens that YouTube titles carry but users never say when
# picking a video — never count against the overlap score.
_TITLE_STOP = frozenset({
    "lyrics", "lyric", "official", "video", "mv", "hd", "4k", "audio",
    "music", "song", "songs", "full", "track", "remix", "live", "version",
    "ft", "feat", "ft.", "visualizer", "visualiser", "clip",
})

# Utterance tokens with zero selection signal. NOTE: song/songs/music/video
# deliberately stay OUT of this set — they're genuine title words ("Shape
# of You", "Song Title") and only become noise when the whole utterance is
# an anaphoric reference, which is detected separately before title match.
_QUERY_STOP = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
    "it", "that", "this", "one", "some", "any", "famous", "english",
    "please", "me", "my", "you",
})

_PAREN_RE   = re.compile(r'[\(\[\{].*?[\)\]\}]')
_TOKEN_RE   = re.compile(r"[a-z0-9']+")

# Generic media nouns users prepend to titles ("play the song believer").
# Dropped from the query side WHEN other signal tokens exist, so they can't
# dilute the overlap score; an utterance that is ONLY media nouns is an
# anaphor and gets rejected upstream.
_MEDIA_NOUNS = frozenset({"song", "songs", "music", "video", "videos",
                            "track", "tracks", "audio"})

# "play the song" / "play it" / "now put the music on" — a bare imperative
# referring to media by NOUN instead of title. The noun must END the
# utterance: "play the song believer" carries a title after the noun and
# must take the title-match path instead. Matched against the raw
# utterance; leading filler ("No, I say ...") is absorbed by the prefix.
_ANAPHORIC_RE = re.compile(
    r'^(?:\S+\s+){0,5}'
    r'\b(?:play|resume|start|continue|put\s+on)\s+'
    r'(?:the\s+|some\s+|my\s+)?'
    r'(?:song|video|music|track|one|it|that|this)'
    r'\s*[.!]*\s*$',
    re.IGNORECASE,
)

# Accept thresholds — tuned on the live failure: query tokens
# {learn,love,like,do} vs title tokens {ellie,goulding,love,like,do} → 0.75.
_MIN_RATIO        = 0.60
_MIN_MATCHED      = 2
_EXACT_TOKEN_MIN  = 0.99   # single-token utterance ("believer") must be exact


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _strip_noise(utterance: str) -> str:
    t = utterance.strip().strip('.!?').strip()
    prev = None
    while prev != t:
        prev = t
        t = _NOISE_LEAD_RE.sub('', t, count=1).strip()
    return t


def _significant(tokens: list[str], stop: frozenset) -> list[str]:
    return [t for t in tokens if t not in stop and len(t) > 1]


def is_anaphoric_play(utterance: str, max_words: int = 8) -> bool:
    """True for bare 'play the song' / 'play it' style references that name
    media by noun rather than title. Title-specific utterances never match
    (they carry content words after the noun). Matched against the RAW
    utterance — _strip_noise would remove the very verb this regex keys on;
    leading filler ("No, I say ...") is absorbed by the optional prefix."""
    t = utterance.strip().strip('.!?').strip()
    if not t or len(t.split()) > max_words:
        return False
    return bool(_ANAPHORIC_RE.match(t))


def match_candidate(
    utterance: str,
    candidates: list[dict],
) -> Optional[dict]:
    """
    Match an utterance against pending video candidates BY TITLE.

    Returns {"index": int, "candidate": dict, "score": float} for the best
    candidate above threshold, else None. Candidates are the dicts stored in
    pending_video_candidates (must carry "title").
    """
    t = _strip_noise(utterance)
    q_sig = _significant(_tokens(t), _QUERY_STOP)
    q_no_media = [tok for tok in q_sig if tok not in _MEDIA_NOUNS]
    if q_no_media:
        q_sig = q_no_media
    if not t or not q_sig:
        return None
    # Whole-utterance anaphors ("play it", "that one") must never title-
    # match — they carry only media-noun tokens which real titles contain,
    # so without this guard "play it" could score against e.g. "Play It
    # Again". Callers check anaphors too, but the matcher defends itself.
    if is_anaphoric_play(utterance):
        return None

    q_joined = " ".join(q_sig)

    best: Optional[dict] = None
    best_score = 0.0
    for idx, cand in enumerate(candidates or []):
        title_raw = (cand or {}).get("title") or ""
        title_clean = _PAREN_RE.sub(' ', title_raw)  # drop "(Lyrics)", "[Official]"...
        t_sig = _significant(_tokens(title_clean), _TITLE_STOP | _QUERY_STOP)
        if not t_sig:
            continue

        # Substring short-circuit — the cleaned utterance appears verbatim
        # inside the cleaned title.
        if len(q_sig) >= 2 and q_joined in " ".join(t_sig):
            score = 1.0
        else:
            t_set = set(t_sig)
            matched = [tok for tok in q_sig if tok in t_set]
            score = len(matched) / len(q_sig)
            if score >= _EXACT_TOKEN_MIN and len(q_sig) == 1:
                pass  # single exact token — accept
            elif score < _MIN_RATIO or len(matched) < _MIN_MATCHED:
                score = 0.0

        if score > best_score:
            best_score = score
            best = {"index": idx, "candidate": cand, "score": round(score, 3)}

    return best
