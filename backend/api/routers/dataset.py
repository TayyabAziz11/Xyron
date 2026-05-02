"""
Dataset recording API — browser uploads audio clips for OWW training.

Endpoints
─────────
  POST /api/v1/dataset/upload
    Receives a single audio clip (WebM/WAV) and saves it as a numbered WAV
    in dataset/{keyword}/.

  GET  /api/v1/dataset/stats
    Returns clip counts per keyword.

  GET  /api/v1/dataset/record
    Serves the browser recording UI (self-contained HTML).
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dataset", tags=["dataset"])

DATASET_DIR = Path(__file__).parent.parent.parent.parent / "dataset"
SR          = 16_000
CLIP_SAMP   = int(SR * 2.0)

KEYWORDS = [
    "hey_xyron",
    "wakeup_xyron",
    "xyron",
    "hey_xeron",
    "hi_xyron",
    "negative",
]


def _next_index(kw_dir: Path) -> int:
    existing = sorted(kw_dir.glob("*.wav"))
    return int(existing[-1].stem) + 1 if existing else 1


def _decode_audio(audio_bytes: bytes) -> tuple[np.ndarray | None, str]:
    """
    Decode browser audio (WebM/Opus or WAV) → 16kHz mono float32.
    Returns (pcm_array, error_message).  error_message is "" on success.
    Tries ffmpeg first (handles WebM/Opus), falls back to soundfile (WAV/FLAC).
    """
    # ── ffmpeg path ──
    import shutil
    if shutil.which("ffmpeg"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                f.write(audio_bytes)
                in_path = f.name
            out_path = in_path + ".f32le"
            try:
                r = subprocess.run(
                    ["ffmpeg", "-y", "-i", in_path,
                     "-ac", "1", "-ar", str(SR), "-f", "f32le",
                     "-acodec", "pcm_f32le", out_path],
                    capture_output=True, timeout=10, check=False,
                )
                if r.returncode == 0:
                    raw = np.frombuffer(open(out_path, "rb").read(), dtype=np.float32)
                    return raw.copy(), ""
                stderr = r.stderr.decode(errors="replace")[-200:]
                logger.warning("ffmpeg decode failed: %s", stderr)
            finally:
                for p in (in_path, out_path):
                    try: os.unlink(p)
                    except Exception: pass
        except Exception as exc:
            logger.warning("ffmpeg error: %s", exc)
    else:
        logger.warning("ffmpeg not found — install with: sudo apt-get install -y ffmpeg")

    # ── soundfile fallback (WAV/FLAC only — not WebM/Opus) ──
    try:
        import soundfile as sf
        audio, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != SR:
            import scipy.signal as sps
            audio = sps.resample(audio, int(len(audio) * SR / sr)).astype(np.float32)
        return audio.astype(np.float32), ""
    except Exception:
        pass

    return None, "Could not decode audio. Install ffmpeg: sudo apt-get install -y ffmpeg"


def _normalize_and_pad(pcm: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(pcm ** 2)))
    if rms > 1e-6:
        scale = min(0.03 / rms, 0.99 / np.max(np.abs(pcm) + 1e-9))
        pcm = pcm * scale
    if len(pcm) < CLIP_SAMP:
        pad_l = (CLIP_SAMP - len(pcm)) // 2
        pad_r = CLIP_SAMP - len(pcm) - pad_l
        pcm = np.concatenate([np.zeros(pad_l), pcm, np.zeros(pad_r)])
    else:
        pcm = pcm[:CLIP_SAMP]
    return pcm.astype(np.float32)


def _save_wav(pcm: np.ndarray, path: Path) -> None:
    import struct
    int16 = (pcm * 32767).astype(np.int16)
    data  = int16.tobytes()
    with open(path, "wb") as f:
        # WAV header
        data_len = len(data)
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_len))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16))
        f.write(b"data")
        f.write(struct.pack("<I", data_len))
        f.write(data)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_clip(
    audio:   UploadFile = File(...),
    keyword: str        = Form(...),
) -> JSONResponse:
    if keyword not in KEYWORDS:
        raise HTTPException(400, f"Unknown keyword '{keyword}'. Valid: {KEYWORDS}")

    raw = await audio.read()
    if len(raw) < 100:
        return JSONResponse({"saved": False, "reason": "audio_too_small", "rms": 0.0})

    pcm, err = _decode_audio(raw)
    if pcm is None or len(pcm) < SR // 4:
        return JSONResponse({"saved": False, "reason": err or "decode_failed", "rms": 0.0})

    rms = float(np.sqrt(np.mean(pcm ** 2)))
    if rms < 0.001:
        return JSONResponse({"saved": False, "reason": "silent", "rms": round(rms, 6)})

    pcm = _normalize_and_pad(pcm)
    kw_dir = DATASET_DIR / keyword
    kw_dir.mkdir(parents=True, exist_ok=True)

    idx      = _next_index(kw_dir)
    out_path = kw_dir / f"{idx:04d}.wav"
    _save_wav(pcm, out_path)

    total = len(list(kw_dir.glob("*.wav")))
    logger.info("[Dataset] saved %s/%04d.wav  rms=%.4f  total=%d", keyword, idx, rms, total)

    return JSONResponse({
        "saved":   True,
        "file":    out_path.name,
        "keyword": keyword,
        "total":   total,
        "rms":     round(rms, 6),
    })


@router.get("/stats")
async def dataset_stats() -> JSONResponse:
    stats = {}
    for kw in KEYWORDS:
        kw_dir = DATASET_DIR / kw
        stats[kw] = len(list(kw_dir.glob("*.wav"))) if kw_dir.exists() else 0
    return JSONResponse({"keywords": stats, "dataset_dir": str(DATASET_DIR)})


@router.get("/record", response_class=HTMLResponse)
async def record_page() -> HTMLResponse:
    """Self-contained recording UI — open in browser."""
    return HTMLResponse(_RECORD_HTML)


# ── Browser recording UI ──────────────────────────────────────────────────────

_RECORD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Xyron Wake Word Recorder</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f0f13; color: #e0e0e0; min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 2rem 1rem; }
  h1 { font-size: 1.6rem; font-weight: 700; color: #fff; margin-bottom: 0.3rem; }
  .sub { color: #888; font-size: 0.9rem; margin-bottom: 2rem; }
  .card { background: #1a1a24; border: 1px solid #2a2a3a; border-radius: 12px; padding: 1.5rem; width: 100%; max-width: 560px; margin-bottom: 1.5rem; }
  label { font-size: 0.85rem; color: #888; display: block; margin-bottom: 0.4rem; }
  select, input[type=number] { width: 100%; background: #12121a; border: 1px solid #333; color: #e0e0e0; border-radius: 8px; padding: 0.6rem 0.8rem; font-size: 0.95rem; }
  .row { display: flex; gap: 1rem; margin-top: 1rem; }
  .row > div { flex: 1; }
  .prompt { background: #12121a; border-radius: 8px; padding: 1rem; text-align: center; font-size: 1.1rem; color: #a0c4ff; min-height: 3rem; display: flex; align-items: center; justify-content: center; margin: 1rem 0; }
  .big-btn { width: 100%; padding: 1rem; font-size: 1.1rem; font-weight: 700; border: none; border-radius: 10px; cursor: pointer; background: #3a6ff7; color: #fff; transition: background 0.15s; }
  .big-btn:hover:not(:disabled) { background: #5580ff; }
  .big-btn:disabled { background: #2a2a3a; color: #555; cursor: not-allowed; }
  .big-btn.recording { background: #e74c3c; animation: pulse 0.8s infinite alternate; }
  @keyframes pulse { from { opacity: 1; } to { opacity: 0.6; } }
  .meter-wrap { margin: 0.8rem 0; height: 8px; background: #12121a; border-radius: 4px; overflow: hidden; }
  .meter-bar { height: 100%; background: #3a6ff7; width: 0%; transition: width 0.05s; border-radius: 4px; }
  .progress { margin-top: 0.6rem; }
  .progress-bar-wrap { height: 6px; background: #12121a; border-radius: 3px; margin-top: 0.4rem; }
  .progress-bar { height: 100%; background: #2ecc71; border-radius: 3px; transition: width 0.3s; }
  .log { background: #12121a; border-radius: 8px; padding: 0.8rem; font-size: 0.8rem; color: #888; height: 120px; overflow-y: auto; font-family: monospace; }
  .log .ok  { color: #2ecc71; }
  .log .err { color: #e74c3c; }
  .log .info { color: #888; }
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem; margin-top: 0.6rem; }
  .stat { background: #12121a; border-radius: 8px; padding: 0.6rem; text-align: center; }
  .stat-n { font-size: 1.4rem; font-weight: 700; color: #3a6ff7; }
  .stat-l { font-size: 0.7rem; color: #666; }
  .countdown { font-size: 3rem; font-weight: 900; color: #e74c3c; text-align: center; min-height: 4rem; display: flex; align-items: center; justify-content: center; }
</style>
</head>
<body>
<h1>Xyron Wake Word Recorder</h1>
<p class="sub">Record training clips directly from your browser mic</p>

<div class="card">
  <div class="row">
    <div>
      <label>Keyword</label>
      <select id="kw">
        <option value="hey_xyron">hey xyron  (target: 150)</option>
        <option value="wakeup_xyron">wake up xyron  (target: 150)</option>
        <option value="xyron">xyron  (target: 100)</option>
        <option value="hey_xeron">hey xeron  (target: 100)</option>
        <option value="hi_xyron">hi xyron  (target: 100)</option>
        <option value="negative">negative  (target: 200)</option>
      </select>
    </div>
    <div>
      <label>Target clips</label>
      <input type="number" id="target" value="150" min="1" max="500">
    </div>
  </div>
</div>

<div class="card">
  <div class="prompt" id="prompt">Press Start to begin recording</div>
  <div class="countdown" id="countdown"></div>
  <div class="meter-wrap"><div class="meter-bar" id="meter"></div></div>
  <button class="big-btn" id="startBtn" onclick="start()">Start Recording Session</button>
  <div class="progress" id="progressWrap" style="display:none">
    <span id="progressText">0 / 150</span>
    <div class="progress-bar-wrap"><div class="progress-bar" id="progressBar" style="width:0%"></div></div>
  </div>
</div>

<div class="card">
  <label>Activity log</label>
  <div class="log" id="log"></div>
</div>

<div class="card">
  <label>Dataset stats (live)</label>
  <div class="stats" id="stats"></div>
</div>

<script>
const API = window.location.origin
const CLIP_MS  = 2100
const GAP_MS   = 700

const PROMPTS = {
  hey_xyron:    ["Say: 'hey xyron'", "Natural pace — 'hey xyron'", "Try varied tone — 'hey xyron'"],
  wakeup_xyron: ["Say: 'wake up xyron'", "Natural pace — 'wake up xyron'", "Slightly slower — 'wake up xyron'"],
  xyron:        ["Say: 'xyron'", "Just the name — 'xyron'", "Clear and confident — 'xyron'"],
  hey_xeron:    ["Say: 'hey xeron'", "'hey xeron' — alternate spelling"],
  hi_xyron:     ["Say: 'hi xyron'", "Casual — 'hi xyron'"],
  negative:     ["Say any random sentence", "Count slowly to five", "Background noise (don't speak)", "Say something similar: 'hey Byron'", "Cough, laugh, or tap the desk"],
}

let running  = false
let recorded = 0
let analyser = null
let animFrame = null

function log(msg, cls='info') {
  const el = document.getElementById('log')
  const d  = document.createElement('div')
  d.className = cls
  d.textContent = new Date().toLocaleTimeString() + '  ' + msg
  el.appendChild(d)
  el.scrollTop = el.scrollHeight
}

async function fetchStats() {
  try {
    const r = await fetch(API + '/api/v1/dataset/stats')
    const d = await r.json()
    const el = document.getElementById('stats')
    el.innerHTML = Object.entries(d.keywords).map(([k,v]) =>
      `<div class="stat"><div class="stat-n">${v}</div><div class="stat-l">${k.replace('_',' ')}</div></div>`
    ).join('')
  } catch {}
}

function beep(freq=880, dur=0.12) {
  try {
    const ctx = new (window.AudioContext||window.webkitAudioContext)()
    const o = ctx.createOscillator(), g = ctx.createGain()
    o.connect(g); g.connect(ctx.destination)
    o.frequency.value = freq
    g.gain.setValueAtTime(0.3, ctx.currentTime)
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + dur)
    o.start(); o.stop(ctx.currentTime + dur)
    o.onended = () => ctx.close()
  } catch {}
}

async function start() {
  if (running) { running = false; return }
  running  = true
  recorded = 0
  const kw     = document.getElementById('kw').value
  const target = parseInt(document.getElementById('target').value)
  const prompts = PROMPTS[kw] || ['Say the wake word']

  document.getElementById('startBtn').textContent = 'Stop'
  document.getElementById('startBtn').classList.add('recording')
  document.getElementById('progressWrap').style.display = ''
  document.getElementById('progressText').textContent = `0 / ${target}`
  document.getElementById('progressBar').style.width = '0%'

  let stream
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
    })
  } catch (e) {
    log('Mic access denied: ' + e.message, 'err')
    done()
    return
  }

  // Build analyser for RMS meter
  const actx = new (window.AudioContext||window.webkitAudioContext)()
  analyser = actx.createAnalyser()
  analyser.fftSize = 512
  actx.createMediaStreamSource(stream).connect(analyser)

  // Run meter
  const timeBuf = new Float32Array(analyser.fftSize)
  function drawMeter() {
    if (!running) return
    analyser.getFloatTimeDomainData(timeBuf)
    let sum = 0
    for (let i=0;i<timeBuf.length;i++) sum += timeBuf[i]*timeBuf[i]
    const rms = Math.sqrt(sum/timeBuf.length)
    document.getElementById('meter').style.width = Math.min(100, rms/0.05*100) + '%'
    animFrame = requestAnimationFrame(drawMeter)
  }
  drawMeter()

  log(`Starting ${kw} — target ${target} clips`, 'info')

  while (running && recorded < target) {
    const prompt = prompts[recorded % prompts.length]
    document.getElementById('prompt').textContent = prompt

    // Countdown
    for (let i=3; i>=1; i--) {
      if (!running) break
      document.getElementById('countdown').textContent = i
      beep(440 + i*100, 0.08)
      await sleep(900)
    }
    if (!running) break
    document.getElementById('countdown').textContent = '●'
    beep(880, 0.12)

    // Record
    const chunks = []
    await new Promise(res => {
      const rec = new MediaRecorder(stream)
      rec.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data) }
      rec.onstop = res
      rec.start()
      setTimeout(() => { if (rec.state !== 'inactive') rec.stop() }, CLIP_MS)
    })

    document.getElementById('countdown').textContent = '↑'

    // Upload
    const blob = new Blob(chunks, { type: 'audio/webm' })
    try {
      const form = new FormData()
      form.append('audio',   blob, 'clip.webm')
      form.append('keyword', kw)
      const r = await fetch(API + '/api/v1/dataset/upload', { method: 'POST', body: form })
      let d
      try { d = await r.json() } catch(e) { log('Bad server response: ' + e.message, 'err'); continue }
      if (!r.ok) {
        log(`✗ server error ${r.status}: ${d?.detail ?? JSON.stringify(d)}`, 'err')
        continue
      }
      if (d.saved) {
        recorded++
        const pct = (recorded / target * 100).toFixed(0)
        document.getElementById('progressText').textContent = `${recorded} / ${target}`
        document.getElementById('progressBar').style.width = pct + '%'
        log(`✓ ${d.file}  rms=${d.rms}  total=${d.total}`, 'ok')
      } else {
        log(`⚠ skipped — ${d.reason ?? 'unknown'}  rms=${d.rms ?? '?'}`, 'info')
      }
    } catch(e) {
      log('Upload error: ' + e.message, 'err')
    }

    document.getElementById('countdown').textContent = ''
    if (running && recorded < target) await sleep(GAP_MS)
  }

  stream.getTracks().forEach(t => t.stop())
  actx.close().catch(()=>{})
  done()
  if (recorded >= target) log(`Session complete — ${recorded} clips saved`, 'ok')
  fetchStats()
}

function done() {
  running = false
  if (animFrame) cancelAnimationFrame(animFrame)
  document.getElementById('startBtn').textContent = 'Start Recording Session'
  document.getElementById('startBtn').classList.remove('recording')
  document.getElementById('prompt').textContent = 'Press Start to begin recording'
  document.getElementById('countdown').textContent = ''
  document.getElementById('meter').style.width = '0%'
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

fetchStats()
setInterval(fetchStats, 5000)
</script>
</body>
</html>
"""
