'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { imHomeAudio } from '@/lib/im-home-audio'

// ── Types ──────────────────────────────────────────────────────────────────────

export type ImHomePhase =
  | 'idle'
  | 'phase1'   // 0–4s:   presence detected
  | 'phase2'   // 4–10s:  system initialization
  | 'phase3'   // 10–18s: agent network
  | 'phase4'   // 18–24s: world awareness
  | 'phase5'   // 24–28s: daily directive
  | 'complete'

export interface WeatherData {
  temp_c:      number | null
  humidity:    number | null
  wind_kmh:    number | null
  rain_pct:    number | null
  description: string
}

export interface SystemData {
  cpu_pct:       number
  ram_pct:       number
  ram_used_gb:   number
  ram_total_gb:  number
  uptime_str:    string
  process_count: number
}

export interface NewsItem {
  title:  string
  url:    string
  points: number
}

export interface ImHomeData {
  weather: WeatherData
  system:  SystemData
  news:    NewsItem[]
}

const API_BASE = typeof window !== 'undefined' ? '' : (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000')

// ── Defaults (shown instantly while real data loads) ──────────────────────────

const DEFAULT_DATA: ImHomeData = {
  weather: { temp_c: null, humidity: null, wind_kmh: null, rain_pct: null, description: 'syncing...' },
  system:  { cpu_pct: 0, ram_pct: 0, ram_used_gb: 0, ram_total_gb: 16, uptime_str: 'calculating...', process_count: 0 },
  news:    [],
}

// Fallback AI headlines shown if HN API returns nothing
const FALLBACK_NEWS: NewsItem[] = [
  { title: 'GPT-4o receives new real-time voice capabilities update',          url: '', points: 842 },
  { title: 'Anthropic releases Claude with extended context window',            url: '', points: 631 },
  { title: 'Google DeepMind Gemini 2.0 benchmark results exceed expectations', url: '', points: 589 },
  { title: 'Open-source LLM Mistral Large achieves GPT-4 parity on coding',   url: '', points: 473 },
  { title: 'Meta releases Llama 3.1 — outperforms closed models on reasoning', url: '', points: 391 },
]

// ── TTS script builder ────────────────────────────────────────────────────────

function buildScript(data: ImHomeData): string {
  const h        = new Date().getHours()
  const greeting = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'

  const w = data.weather
  const weatherLine = w.temp_c != null
    ? `Temperature in Karachi is ${Math.round(w.temp_c)} degrees Celsius, ${w.description}.${w.rain_pct != null && w.rain_pct > 30 ? ` Rain probability at ${w.rain_pct} percent tonight.` : ''}`
    : 'External weather feed is being synchronized.'

  const s = data.system
  const systemLine = s.cpu_pct > 0
    ? `CPU load at ${s.cpu_pct} percent. Memory at ${s.ram_pct} percent — ${s.ram_used_gb} of ${s.ram_total_gb} gigabytes in use. Uptime: ${s.uptime_str}.`
    : 'System diagnostics running.'

  const news = data.news.length > 0 ? data.news : FALLBACK_NEWS
  const clean  = (t: string) => t.replace(/[^a-zA-Z0-9 .,!?():'-]/g, '').slice(0, 90)
  const top3   = news.slice(0, 3)
  const newsLine = `${news.length} AI developments detected since your last session. ` +
    top3.map((n, i) => `${['First', 'Second', 'Third'][i]}: ${clean(n.title)}.`).join(' ')

  return [
    `${greeting}, Tayyab.`,
    'Presence synchronization complete.',
    'Environmental systems online.',
    weatherLine,
    'Neural agents remain active.',
    'Development systems are fully operational.',
    'Memory indexing completed.',
    'Vision pipeline standing by.',
    systemLine,
    newsLine,
    'Three active objectives remain incomplete.',
    'Primary directive remains Xyron evolution.',
    'Your systems are prepared.',
    'We build further today.',
  ].join(' ')
}

// ── Log lines ─────────────────────────────────────────────────────────────────

const LOG_LINES = [
  'INITIALIZING XYRON SYSTEMS...',
  '[OK] Neural Core',
  '[OK] Memory Graph',
  '[OK] Voice Engine',
  '[OK] Vision Pipeline',
  '[OK] Context Memory',
  '[OK] Agent Network',
  '[OK] Environmental Awareness',
  '[OK] Autonomous Planning',
  '[OK] System Monitoring',
  '[OK] Threat Detection',
  '[OK] Passive Learning Systems',
  '[OK] Workspace Synchronization',
]

// ── Hook ───────────────────────────────────────────────────────────────────────

export function useImHomeProtocol() {
  const [phase, setPhase] = useState<ImHomePhase>('idle')
  const [data,  setData]  = useState<ImHomeData>(DEFAULT_DATA)
  const [logs,  setLogs]  = useState<string[]>([])

  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([])
  const abortRef  = useRef<AbortController | null>(null)
  const activeRef = useRef(false)

  const clearTimers = useCallback(() => {
    timersRef.current.forEach(clearTimeout)
    timersRef.current = []
  }, [])

  const schedule = useCallback((fn: () => void, ms: number) => {
    timersRef.current.push(setTimeout(fn, ms))
  }, [])

  const dismiss = useCallback(() => {
    activeRef.current = false
    clearTimers()
    abortRef.current?.abort()
    imHomeAudio.cancel()
    setPhase('idle')
    setLogs([])
    setData(DEFAULT_DATA)
  }, [clearTimers])

  const trigger = useCallback(() => {
    if (activeRef.current) return
    activeRef.current = true
    clearTimers()
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    // ── Start UI immediately — no waiting ────────────────────────────────────
    setPhase('phase1')
    setLogs([])
    setData(DEFAULT_DATA)

    // Phase transitions fire on schedule regardless of data
    schedule(() => { if (activeRef.current) setPhase('phase2')   }, 3_000)
    schedule(() => { if (activeRef.current) setPhase('phase3')   }, 7_500)
    schedule(() => { if (activeRef.current) setPhase('phase4')   }, 13_000)
    schedule(() => { if (activeRef.current) setPhase('phase5')   }, 17_500)
    schedule(() => { if (activeRef.current) setPhase('complete') }, 21_000)
    // No auto-dismiss — stays visible until user manually closes (Escape / dismiss button)

    // Init log lines start typing at phase 2
    LOG_LINES.forEach((line, i) => {
      schedule(() => {
        if (activeRef.current) setLogs(prev => [...prev, line])
      }, 3_100 + i * 290)
    })

    // ── Fetch real data + TTS concurrently — update UI when ready ────────────
    const fetchData = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/system/home-briefing`, { signal: ctrl.signal })
        if (!res.ok) throw new Error('non-ok')
        const body = await res.json()
        if (ctrl.signal.aborted || !body?.data) return null

        const briefing: ImHomeData = {
          weather: body.data.weather ?? DEFAULT_DATA.weather,
          system:  body.data.system  ?? DEFAULT_DATA.system,
          news:    Array.isArray(body.data.news) && body.data.news.length > 0
            ? body.data.news
            : FALLBACK_NEWS,
        }
        setData(briefing)   // update UI live as soon as data arrives
        return briefing
      } catch {
        // Use fallback news so the ticker always has content
        setData(prev => ({ ...prev, news: FALLBACK_NEWS }))
        return null
      }
    }

    const fetchTts = async (briefing: ImHomeData | null) => {
      const script = buildScript(briefing ?? { ...DEFAULT_DATA, news: FALLBACK_NEWS })
      try {
        const r = await fetch(`${API_BASE}/api/v1/voice/synthesize`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ text: script, voice: 'onyx', speed: 0.92 }),
          signal:  ctrl.signal,
        })
        if (r.ok && !ctrl.signal.aborted) {
          const blob = await r.blob()
          if (!ctrl.signal.aborted) imHomeAudio.playBlob(blob).catch(() => {})
        }
      } catch { /* silent */ }
    }

    // Run fetch pipeline — UI is already animating, data fills in behind it
    fetchData().then(fetchTts).catch(() => {})

  }, [clearTimers, schedule, dismiss])

  useEffect(() => {
    const handler = () => trigger()
    window.addEventListener('xyron:im-home', handler)
    return () => window.removeEventListener('xyron:im-home', handler)
  }, [trigger])

  useEffect(() => () => dismiss(), [dismiss])

  return { phase, data, logs, dismiss, trigger }
}
