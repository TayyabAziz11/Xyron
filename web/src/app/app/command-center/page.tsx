'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Mic, MicOff, Terminal, Clock, ArrowRight, Sparkles,
  CheckCircle2, XCircle, Loader2, Volume2, VolumeX, Zap, Brain,
  FileText, Radio, WifiOff, Moon, Coffee, Settings, LayoutGrid,
  Star, Cpu, Code2, Activity, Wifi, ChevronRight,
} from 'lucide-react'
import { PageTransition }      from '@/components/layout/PageTransition'
import { ConversationThread }  from '@/components/voice/ConversationThread'
import { VoiceSessionPanel }   from '@/components/voice/VoiceSessionPanel'
import { DraftPreview }        from '@/components/command/DraftPreview'
import { TaskProgressPanel }   from '@/components/tasks/TaskProgressPanel'
import { SystemInfoPanel }     from '@/components/system/SystemInfoPanel'
import { FollowUpChip }        from '@/components/voice/FollowUpChip'
import { MacrosPanel }         from '@/components/voice/MacrosPanel'
import { NotesPanel }          from '@/components/voice/NotesPanel'
import { MeetingPanel }        from '@/components/voice/MeetingPanel'
import { ProfileSwitcher }     from '@/components/voice/ProfileSwitcher'
import { ProactiveToast }      from '@/components/system/ProactiveToast'
import { useVoiceSession }     from '@/hooks/useVoiceSession'
import { useWakeWord }         from '@/hooks/useWakeWord'
import { useReminders }        from '@/hooks/useReminders'
import { useAssistantSettings } from '@/hooks/useAssistantSettings'
import { useCommands }         from '@/hooks/useCommands'
import { useVoice }            from '@/hooks/useVoice'
import { useDrafts }           from '@/hooks/useDrafts'
import { useTasks }            from '@/hooks/useTasks'
import { useProactive }        from '@/hooks/useProactive'
import { useTakeoverMode }     from '@/hooks/useTakeoverMode'
import TakeoverOrchestrator    from '@/components/takeover/TakeoverOrchestrator'
import ImHomeProtocol          from '@/components/im-home/ImHomeProtocol'
import { useImHomeProtocol }   from '@/hooks/useImHomeProtocol'
import type { Command, Draft } from '@/lib/types'
import { ThoughtStream } from '@/components/ambient'
import { useCognitiveState } from '@/hooks/useCognitiveState'
import { useThoughtGenerator } from '@/hooks/useThoughtGenerator'

// ── Helpers ───────────────────────────────────────────────────────────────────

const relTime = (iso: string) => {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60)   return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  completed: <CheckCircle2 className="h-3.5 w-3.5 text-[#00ff88]" />,
  failed:    <XCircle      className="h-3.5 w-3.5 text-[#ef4444]" />,
  running:   <Loader2      className="h-3.5 w-3.5 text-[#ff2020] animate-spin" />,
  queued:    <Loader2      className="h-3.5 w-3.5 text-text-muted animate-spin" />,
}

const STATUS_COLOR: Record<string, string> = {
  completed: '#00ff88',
  failed:    '#ef4444',
  running:   '#ff2020',
  queued:    '#94a3b8',
}

const IM_HOME_RE = /(?:xyron[\s,]+)?i(?:'m|\s+am)\s+(?:home|back)|i\s+just\s+(?:got|arrived)\s+(?:home|back)|welcome\s+me\s+home|i(?:'ve|\s+have)\s+arrived\s+home/i

// ── Animated Orb ─────────────────────────────────────────────────────────────

function VoiceOrb({ isListening, isSpeaking, isProcessing, onClick }: {
  isListening: boolean; isSpeaking: boolean; isProcessing: boolean; onClick: () => void
}) {
  const barsRef = useRef<HTMLDivElement[]>([])
  const rafRef  = useRef<number>(0)

  useEffect(() => {
    const animate = () => {
      barsRef.current.forEach(bar => {
        if (!bar) return
        const active = isListening || isSpeaking
        const h = active ? (Math.random() * 28 + 6) : (Math.random() * 6 + 3)
        bar.style.height = `${h}px`
      })
      rafRef.current = requestAnimationFrame(animate)
    }
    rafRef.current = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(rafRef.current)
  }, [isListening, isSpeaking])

  const orbColor = isListening ? '#00ff88' : isSpeaking ? '#a78bfa' : isProcessing ? '#00ffff' : '#ff2020'

  return (
    <div className="relative flex items-center justify-center w-64 h-64 mx-auto cursor-pointer select-none" onClick={onClick}>
      {/* Outer ambient glow */}
      <div className="absolute inset-0 rounded-full"
        style={{ background: `radial-gradient(circle, ${orbColor}08 0%, transparent 70%)`, animation: 'orbBreath 3s ease-in-out infinite' }} />

      {/* Ring 1 — slowest */}
      <div className="absolute rounded-full border-2 w-60 h-60"
        style={{ borderColor: `${orbColor}20`, boxShadow: `0 0 24px ${orbColor}10`, animation: 'spinSlow 18s linear infinite' }} />

      {/* Ring 2 */}
      <div className="absolute rounded-full border w-52 h-52"
        style={{ borderColor: `${orbColor}30`, boxShadow: `0 0 16px ${orbColor}15`, animation: 'spinSlow 12s linear infinite reverse' }} />

      {/* Ring 3 */}
      <div className="absolute rounded-full border-2 w-44 h-44"
        style={{ borderColor: `${orbColor}40`, boxShadow: `0 0 20px ${orbColor}20`, animation: 'orbBreath 2.4s ease-in-out infinite' }} />

      {/* Ring 4 */}
      <div className="absolute rounded-full w-36 h-36"
        style={{ border: `2px solid ${orbColor}55`, boxShadow: `0 0 30px ${orbColor}30, inset 0 0 20px ${orbColor}10`, animation: 'orbBreath 2s ease-in-out infinite 0.3s' }} />

      {/* Core orb */}
      <div className="relative flex items-center justify-center w-28 h-28 rounded-full z-10"
        style={{
          background: `radial-gradient(circle at 35% 30%, ${orbColor}40, ${orbColor}10 60%, transparent)`,
          boxShadow: `0 0 40px ${orbColor}50, 0 0 80px ${orbColor}20, inset 0 0 20px ${orbColor}15`,
          border: `1.5px solid ${orbColor}60`,
          animation: 'orbBreath 2.2s ease-in-out infinite',
        }}>
        {/* Specular */}
        <div className="absolute w-10 h-10 top-3 left-3 rounded-full"
          style={{ background: 'radial-gradient(circle at 30% 30%, rgba(255,255,255,0.25), transparent 70%)' }} />

        {/* Waveform bars */}
        <div className="flex items-end gap-[3px] h-9">
          {Array.from({ length: 9 }).map((_, i) => (
            <div key={i} ref={el => { if (el) barsRef.current[i] = el }}
              className="w-1 rounded-full transition-[height] duration-75"
              style={{ height: '6px', background: orbColor, boxShadow: `0 0 4px ${orbColor}` }} />
          ))}
        </div>
      </div>

      {/* Ripple rings when listening */}
      <AnimatePresence>
        {(isListening || isSpeaking) && [0, 1, 2].map(i => (
          <motion.div key={i}
            className="absolute rounded-full"
            initial={{ opacity: 0.6, scale: 0.5 }}
            animate={{ opacity: 0, scale: 1.9 + i * 0.3 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 2, repeat: Infinity, delay: i * 0.6, ease: 'easeOut' }}
            style={{ width: 112, height: 112, border: `1px solid ${orbColor}60` }} />
        ))}
      </AnimatePresence>
    </div>
  )
}

// ── Bottom status bar ─────────────────────────────────────────────────────────

function BottomBar({ sessionActive, wakeListening }: { sessionActive: boolean; wakeListening: boolean }) {
  const [uptime, setUptime] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setUptime(u => u + 1), 1000)
    return () => clearInterval(t)
  }, [])
  const h = Math.floor(uptime / 3600)
  const m = Math.floor((uptime % 3600) / 60)
  const s = uptime % 60
  const fmt = (n: number) => String(n).padStart(2, '0')

  return (
    <div className="flex items-center overflow-x-auto no-scrollbar rounded-xl border border-[rgba(255,32,32,0.2)] bg-[rgba(0,0,0,0.6)] backdrop-blur-sm">
      {[
        {
          icon: (
            <div className="relative flex-shrink-0">
              <div className="h-2 w-2 rounded-full bg-[#ff2020] animate-pulse" style={{ boxShadow: '0 0 6px #ff2020' }} />
            </div>
          ),
          label: 'LISTENING MODE',
          value: 'WAKE WORD: XYRON',
          extra: (
            <div className="flex items-end gap-px ml-2">
              {[4,7,5,9,6,8,4].map((h, i) => (
                <div key={i} className="w-0.5 rounded-full bg-[#ff2020] opacity-70"
                  style={{ height: h, animation: `waveBar ${0.4 + i * 0.08}s ease-in-out infinite alternate` }} />
              ))}
            </div>
          ),
        },
        {
          icon: <Zap className="h-3.5 w-3.5 text-[#ff2020]" />,
          label: 'CONFIDENCE',
          value: '98%',
          extra: (
            <div className="ml-2 h-1 w-12 rounded-full bg-[rgba(255,32,32,0.15)] overflow-hidden">
              <div className="h-full rounded-full bg-[#ff2020]" style={{ width: '98%', boxShadow: '0 0 4px #ff2020' }} />
            </div>
          ),
        },
        {
          icon: <Brain className="h-3.5 w-3.5 text-[#00ffff]" />,
          label: 'MEMORY',
          value: <span className="text-[#00ff88]">Active</span>,
          extra: <span className="ml-1 font-mono text-[9px] text-[#00ff88]">128 MB</span>,
        },
        {
          icon: <Mic className="h-3.5 w-3.5 text-[#ff2020]" />,
          label: 'VOICE STATUS',
          value: sessionActive ? <span className="text-[#00ff88]">Active</span> : <span className="text-text-muted">Standby</span>,
          extra: sessionActive ? (
            <div className="flex items-end gap-px ml-2">
              {[5,8,6,9,5].map((h, i) => (
                <div key={i} className="w-0.5 rounded-full bg-[#00ff88] opacity-80"
                  style={{ height: h, animation: `waveBar ${0.35 + i * 0.1}s ease-in-out infinite alternate` }} />
              ))}
            </div>
          ) : null,
        },
        {
          icon: <Wifi className="h-3.5 w-3.5 text-[#00ff88]" />,
          label: 'NETWORK',
          value: <span className="text-[#00ff88]">Excellent</span>,
          extra: <span className="ml-1 font-mono text-[9px] text-[#00ff88]">24.8 Mbps</span>,
        },
      ].map((item, i, arr) => (
        <div key={i} className="flex items-center gap-2 px-4 py-3 flex-shrink-0 min-w-[160px]">
          {item.icon}
          <div className="min-w-0">
            <p className="font-mono text-[8px] tracking-[0.15em] text-text-muted">{item.label}</p>
            <div className="flex items-center font-mono text-[10px] font-semibold text-text-secondary">
              {item.value}
              {item.extra}
            </div>
          </div>
          {i < arr.length - 1 && <div className="ml-auto w-px h-6 bg-[rgba(255,32,32,0.15)]" />}
        </div>
      ))}
    </div>
  )
}

// ── Suggestion chips ──────────────────────────────────────────────────────────

const SUGGESTIONS = [
  { icon: '💻', text: 'Open VS Code' },
  { icon: '📊', text: 'Check system status' },
  { icon: '✅', text: 'Show my tasks' },
  { icon: '🎯', text: 'Start deep work mode' },
  { icon: '⚡', text: 'Optimize performance' },
]

// ── Voice profiles ────────────────────────────────────────────────────────────

const PROFILES = [
  { id: 'assistant', icon: '⚡', name: 'Default',   desc: 'Balanced and helpful' },
  { id: 'work',      icon: '💼', name: 'Work',      desc: 'Focused and efficient' },
  { id: 'chill',     icon: '😎', name: 'Chill',     desc: 'Relaxed and casual' },
  { id: 'focus',     icon: '🎯', name: 'Focus',     desc: 'Ultra-minimal replies' },
  { id: 'friendly',  icon: '❤️', name: 'Friendly',  desc: 'Warm and upbeat' },
  { id: 'boss',      icon: '👑', name: 'Boss Mode', desc: 'Calls you boss' },
]

// ── History item ──────────────────────────────────────────────────────────────

function CommandHistoryItem({ cmd, onRerun }: { cmd: Command; onRerun: (text: string) => void }) {
  return (
    <motion.div initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
      className="group flex items-center gap-3 rounded-lg border border-[rgba(255,32,32,0.1)] bg-[rgba(255,32,32,0.02)] px-3 py-2.5 hover:border-[rgba(255,32,32,0.3)] transition-colors cursor-pointer"
      onClick={() => onRerun(cmd.text)}>
      <div className="flex-1 min-w-0">
        <p className="font-mono text-[11px] text-text-primary truncate">{cmd.text}</p>
        <p className="font-mono text-[9px] text-text-muted mt-0.5">{relTime(cmd.created_at)}</p>
      </div>
      <span className="flex-shrink-0 rounded px-1.5 py-0.5 font-mono text-[9px] font-bold"
        style={{ color: STATUS_COLOR[cmd.status] ?? '#94a3b8', background: `${STATUS_COLOR[cmd.status] ?? '#94a3b8'}18`, border: `1px solid ${STATUS_COLOR[cmd.status] ?? '#94a3b8'}40` }}>
        {cmd.status.toUpperCase()}
      </span>
      <ChevronRight className="h-3 w-3 text-text-muted opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0" />
    </motion.div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function CommandCenterPage() {
  const session  = useVoiceSession()

  const wakeActivate = useCallback(() => {
    if (!session.isActive) {
      session.startSession()
    } else if (session.state === 'idle' || session.state === 'listening') {
      session.startListening()
    }
  }, [session])

  const { supported: wakeSupported, listening: wakeListening } = useWakeWord(wakeActivate)
  const { data: commands, submit, submitting, lastResult, refetch } = useCommands()
  const { settings, saveSettings, speak, stop, isSpeaking } = useVoice()
  const { getDraft, confirmDraft, rejectDraft, editDraft, executing } = useDrafts()
  const { tasks, activeTasks, submitTask, cancelTask } = useTasks()
  const { settings: aSettings, saveSettings: saveASettings } = useAssistantSettings()
  useReminders({
    active: session.isActive,
    voice:  aSettings.voice,
    speed:  aSettings.speed,
    volume: aSettings.volume,
    onFire: (_message) => {},
  })

  const [activeTab, setActiveTab] = useState<'voice'|'history'|'tasks'|'macros'|'notes'|'meeting'>('voice')
  const { notifications, dismiss: dismissNotif } = useProactive()
  const { phase: takeoverPhase, systemLine: takeoverLine, controlLevel: takeoverLevel, showDirective: takeoverDirective, stopTakeover } = useTakeoverMode()
  const { phase: imHomePhase, data: imHomeData, logs: imHomeLogs, dismiss: dismissImHome } = useImHomeProtocol()
  const [showThoughts, setShowThoughts]   = useState(false)
  const cogStateCC = useCognitiveState()
  const thoughts   = useThoughtGenerator(cogStateCC)
  const [currentDraft, setCurrentDraft]   = useState<Draft | null>(null)
  const [textInput, setTextInput]         = useState('')
  const [activeProfile, setActiveProfile] = useState('assistant')
  const [clock, setClock]                 = useState('')
  const [clockDate, setClockDate]         = useState('')
  const spokenIdRef = useRef<string | null>(null)
  const inputRef    = useRef<HTMLInputElement>(null)

  // Live clock
  useEffect(() => {
    const tick = () => {
      const now = new Date()
      setClock(now.toLocaleTimeString('en-US', { hour12: false }))
      setClockDate(now.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }))
    }
    tick()
    const t = setInterval(tick, 1000)
    return () => clearInterval(t)
  }, [])

  // Auto-play voice for non-session commands
  useEffect(() => {
    if (!lastResult || lastResult.status !== 'completed') return
    if (!lastResult.assistant_response) return
    if (spokenIdRef.current === lastResult.id) return
    if (!settings.autoPlay) return
    if (session.isActive || session.state !== 'idle') return
    spokenIdRef.current = lastResult.id
    speak(lastResult.assistant_response)
  }, [lastResult?.id, lastResult?.status, lastResult?.assistant_response, settings.autoPlay, speak, session.isActive])

  // Draft watcher
  useEffect(() => {
    if (!lastResult?.draft_id || lastResult.status !== 'completed') return
    getDraft(lastResult.draft_id).then((d) => { if (d) setCurrentDraft(d) })
  }, [lastResult?.id, lastResult?.draft_id, lastResult?.status, getDraft])

  // Action URL handler
  useEffect(() => {
    if (!lastResult || lastResult.status !== 'completed') return
    if (lastResult.action_url) window.open(lastResult.action_url, '_blank', 'noopener')
    if (lastResult.action_app) {
      const API_BASE = typeof window !== 'undefined' ? '' : 'http://localhost:8000'
      fetch(`${API_BASE}/api/v1/system/open-app`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ app: lastResult.action_app }),
      }).catch(() => {})
    }
  }, [lastResult?.id, lastResult?.status])

  const handleTextSubmit = useCallback(async (text: string) => {
    setCurrentDraft(null)
    if (IM_HOME_RE.test(text.trim())) {
      window.dispatchEvent(new CustomEvent('xyron:im-home'))
      return
    }
    await submit(text)
    refetch()
  }, [submit, refetch])

  const handleRerun = useCallback((text: string) => {
    setActiveTab('voice')
    handleTextSubmit(text)
  }, [handleTextSubmit])

  const handleConfirmDraft = async (id: string) => {
    const r = await confirmDraft(id)
    if (r.spoken && settings.enabled) speak(r.spoken)
    const updated = await getDraft(id)
    if (updated) setCurrentDraft(updated)
  }
  const handleRejectDraft = async (id: string) => {
    await rejectDraft(id)
    const updated = await getDraft(id)
    if (updated) setCurrentDraft(updated)
  }
  const handleEditDraft = async (id: string, content: string) => {
    await editDraft(id, content)
    const updated = await getDraft(id)
    if (updated) setCurrentDraft(updated)
  }

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const t = textInput.trim()
    if (!t || submitting) return
    handleTextSubmit(t)
    setTextInput('')
  }

  const isListening  = session.state === 'listening'
  const isSpeakingS  = session.state === 'speaking' || session.state === 'greeting'
  const isProcessing = session.state === 'transcribing' || session.state === 'processing'

  // CSS animations injected once
  useEffect(() => {
    if (document.getElementById('cc-anims')) return
    const s = document.createElement('style')
    s.id = 'cc-anims'
    s.textContent = `
      @keyframes orbBreath { 0%,100%{transform:scale(1);opacity:1} 50%{transform:scale(1.04);opacity:0.92} }
      @keyframes spinSlow   { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
      @keyframes glowPulse  { 0%,100%{text-shadow:0 0 8px rgba(255,32,32,0.6)} 50%{text-shadow:0 0 20px rgba(255,32,32,1),0 0 40px rgba(255,32,32,0.4)} }
      @keyframes waveBar    { 0%{transform:scaleY(0.4)} 100%{transform:scaleY(1)} }
      @keyframes svgWave    { 0%{d:path("M0,12 C20,4 40,20 60,12 C80,4 100,20 120,12 C140,4 160,20 180,12 C200,4 220,20 240,12")} 50%{d:path("M0,12 C20,20 40,4 60,12 C80,20 100,4 120,12 C140,20 160,4 180,12 C200,20 220,4 240,12")} 100%{d:path("M0,12 C20,4 40,20 60,12 C80,4 100,20 120,12 C140,4 160,20 180,12 C200,4 220,20 240,12")} }
      .no-scrollbar::-webkit-scrollbar{display:none} .no-scrollbar{-ms-overflow-style:none;scrollbar-width:none}
    `
    document.head.appendChild(s)
  }, [])

  return (
    <PageTransition>
      {/* ── Inline styles for orb rings ── */}
      <div className="relative space-y-4 p-4 pb-6">

        {/* ── Floating Voice Active button ── */}
        <AnimatePresence>
          {session.isActive && (
            <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }}
              className="fixed top-20 right-6 z-50 flex items-center gap-2 rounded-full border border-[rgba(255,32,32,0.5)] bg-[#ff2020] px-4 py-2 shadow-[0_0_20px_rgba(255,32,32,0.5)]"
              style={{ animation: 'orbBreath 2s ease-in-out infinite' }}>
              <Mic className="h-3.5 w-3.5 text-white" />
              <span className="font-mono text-[10px] font-bold text-white tracking-widest">VOICE ACTIVE</span>
              <div className="flex items-end gap-px">
                {[4,7,5,8,4].map((h, i) => (
                  <div key={i} className="w-0.5 rounded-full bg-white/80"
                    style={{ height: h, animation: `waveBar ${0.3+i*0.08}s ease-in-out infinite alternate` }} />
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Page header ── */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3">
            <Star className="h-4 w-4 text-[#ff2020]" />
            <div>
              <h1 className="font-mono text-sm font-black tracking-[0.2em] text-[#ff2020]">COMMAND CENTER</h1>
              <p className="font-mono text-[9px] text-text-muted">Direct AI Interface</p>
            </div>
          </div>

          <div className="flex items-center gap-2 rounded-lg border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.4)] px-3 py-1.5">
            <span className="font-mono text-[10px] text-text-muted">XYRON OS / v2.0.0 OMEGA</span>
            <span className="h-1.5 w-1.5 rounded-full bg-[#00ff88] animate-pulse" style={{ boxShadow: '0 0 6px #00ff88' }} />
            <span className="font-mono text-[10px] text-[#00ff88] font-bold">OPERATIONAL</span>
          </div>

          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="font-mono text-sm font-black tabular-nums text-[#ff2020]" style={{ animation: 'glowPulse 2s ease-in-out infinite' }}>
                {clock}
              </p>
              <p className="font-mono text-[9px] text-text-muted">{clockDate}</p>
            </div>
            <button className="rounded border border-[rgba(255,32,32,0.2)] p-1.5 text-text-muted hover:text-[#ff2020] hover:border-[rgba(255,32,32,0.4)] transition-all">
              <Settings className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* ── Main 2-col layout ── */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">

          {/* ── LEFT COLUMN ── */}
          <div className="lg:col-span-3 flex flex-col gap-4">

            {/* Sub-nav tabs */}
            <div className="flex overflow-x-auto no-scrollbar gap-1 border-b border-[rgba(255,32,32,0.15)] pb-0">
              {([
                ['voice',   Mic,      'Voice'],
                ['history', Clock,    'History'],
                ['tasks',   Zap,      'Tasks'],
                ['macros',  LayoutGrid,'Macros'],
                ['notes',   FileText, 'Notes'],
                ['meeting', Radio,    'Meeting'],
              ] as const).map(([tab, Icon, label]) => (
                <button key={tab} onClick={() => setActiveTab(tab)}
                  className={[
                    'relative flex items-center gap-1.5 px-4 py-2.5 font-mono text-[10px] font-semibold tracking-wider whitespace-nowrap transition-all border-b-2 -mb-px',
                    activeTab === tab
                      ? 'border-[#ff2020] text-[#ff2020]'
                      : 'border-transparent text-text-muted hover:text-text-secondary',
                  ].join(' ')}>
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                  {tab === 'tasks' && activeTasks.length > 0 && (
                    <span className="ml-1 h-4 w-4 rounded-full bg-[#ff2020] text-white text-[9px] flex items-center justify-center font-bold">
                      {activeTasks.length}
                    </span>
                  )}
                </button>
              ))}
            </div>

            <AnimatePresence mode="wait">

              {/* ── VOICE TAB ── */}
              {activeTab === 'voice' && (
                <motion.div key="voice" initial={{ opacity:0, y:8 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0, y:-8 }} transition={{ duration:0.18 }}
                  className="flex flex-col gap-4">

                  {/* Chill mode banner */}
                  <AnimatePresence>
                    {aSettings.mode === 'chill' && (
                      <motion.div initial={{ opacity:0, y:-8 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0 }}
                        className="flex items-center gap-3 rounded-xl border border-purple-500/30 bg-purple-500/10 px-4 py-3">
                        <Moon className="h-4 w-4 text-purple-400 animate-pulse" />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-purple-300">Chill mode active</p>
                          <p className="text-xs text-purple-400/70">Relaxed voice · YouTube open</p>
                        </div>
                        <Coffee className="h-3.5 w-3.5 text-purple-400/50" />
                        <button onClick={() => saveASettings({ mode: 'assistant', voice: 'nova' })}
                          className="rounded-lg border border-purple-500/30 bg-purple-500/20 px-3 py-1 text-xs text-purple-300 hover:bg-purple-500/30 transition-colors">
                          Wake up
                        </button>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Offline indicator */}
                  {session.offlineMode && (
                    <div className="flex items-center gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-xs text-yellow-400">
                      <WifiOff className="h-3.5 w-3.5" /> Offline mode — local LLM
                    </div>
                  )}

                  {/* Follow-up chip */}
                  <FollowUpChip suggestion={session.followUp} onDismiss={session.dismissFollowUp}
                    onAccept={(text) => { session.dismissFollowUp(); handleTextSubmit(text) }} />

                  {/* ── BIG ORB SECTION ── */}
                  <div className="rounded-2xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.6)] backdrop-blur-sm p-6 relative overflow-hidden">
                    {/* Ambient bg dots */}
                    <div className="absolute inset-0 pointer-events-none"
                      style={{ backgroundImage: 'radial-gradient(circle, rgba(255,32,32,0.06) 1px, transparent 1px)', backgroundSize: '24px 24px' }} />

                    {/* Orb */}
                    <VoiceOrb
                      isListening={isListening}
                      isSpeaking={isSpeakingS}
                      isProcessing={isProcessing}
                      onClick={session.isActive ? session.stopSession : session.startSession}
                    />

                    {/* Status text */}
                    <div className="flex flex-col items-center gap-2 mt-2">
                      <AnimatePresence mode="wait">
                        <motion.p key={session.state}
                          initial={{ opacity:0, y:4 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0 }}
                          className="font-mono text-sm font-black tracking-[0.25em]"
                          style={{
                            color: isListening ? '#00ff88' : isSpeakingS ? '#a78bfa' : isProcessing ? '#00ffff' : '#ff2020',
                            animation: 'glowPulse 2s ease-in-out infinite',
                          }}>
                          {isListening ? "I'M LISTENING" : isSpeakingS ? 'XYRON SPEAKING' : isProcessing ? 'PROCESSING…' : session.isActive ? 'SAY "HEY XYRON"' : 'TAP TO ACTIVATE'}
                        </motion.p>
                      </AnimatePresence>
                      <p className="font-mono text-[10px] text-text-muted">
                        {isListening ? 'Speak your command clearly' : session.isActive ? 'Wake word listening active' : 'Click orb or say wake word'}
                      </p>

                      {/* Animated SVG waveform line */}
                      <svg width="240" height="24" viewBox="0 0 240 24" className="opacity-60">
                        <path
                          d="M0,12 C20,4 40,20 60,12 C80,4 100,20 120,12 C140,4 160,20 180,12 C200,4 220,20 240,12"
                          fill="none" stroke="#ff2020" strokeWidth="1.5" strokeLinecap="round"
                          style={{ animation: isListening ? 'svgWave 0.8s ease-in-out infinite' : 'svgWave 2s ease-in-out infinite' }}
                        />
                      </svg>
                    </div>

                    {/* Mic control buttons */}
                    <div className="flex items-center justify-center gap-5 mt-4">
                      <button
                        onClick={() => saveSettings({ enabled: !settings.enabled })}
                        className="flex h-11 w-11 items-center justify-center rounded-full border border-[rgba(255,255,255,0.1)] bg-[rgba(255,255,255,0.05)] text-text-muted transition-all hover:border-[rgba(255,32,32,0.4)] hover:text-[#ff2020]">
                        {settings.enabled ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
                      </button>

                      {/* Main mic button */}
                      <button
                        onClick={session.isActive ? session.stopSession : session.startSession}
                        className="flex h-16 w-16 items-center justify-center rounded-full transition-all"
                        style={{
                          background: session.isActive ? '#ff2020' : 'rgba(255,32,32,0.15)',
                          border: '2px solid #ff2020',
                          boxShadow: session.isActive ? '0 0 20px rgba(255,32,32,0.7), 0 0 40px rgba(255,32,32,0.3)' : '0 0 12px rgba(255,32,32,0.2)',
                          animation: session.isActive ? 'orbBreath 1.5s ease-in-out infinite' : undefined,
                        }}>
                        <Mic className="h-6 w-6 text-white" />
                      </button>

                      <button
                        onClick={() => { if (isSpeaking) stop() }}
                        className="flex h-11 w-11 items-center justify-center rounded-full border border-[rgba(255,255,255,0.1)] bg-[rgba(255,255,255,0.05)] text-text-muted transition-all hover:border-[rgba(255,32,32,0.4)] hover:text-[#ff2020]">
                        <Activity className="h-4 w-4" />
                      </button>
                    </div>

                    {/* Conversation — inline, no page scroll */}
                    <AnimatePresence>
                      {session.messages.length > 0 && (
                        <motion.div
                          initial={{ opacity: 0, height: 0 }}
                          animate={{ opacity: 1, height: 'auto' }}
                          exit={{ opacity: 0, height: 0 }}
                          transition={{ duration: 0.2 }}
                          className="mt-4 overflow-hidden rounded-lg border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.4)]"
                        >
                          <div className="flex items-center justify-between px-3 py-2 border-b border-[rgba(255,32,32,0.1)]">
                            <span className="font-mono text-[9px] tracking-[0.15em] text-text-muted">CONVERSATION</span>
                            <button onClick={session.clearMessages} className="font-mono text-[9px] text-text-muted hover:text-[#ff2020] transition-colors">CLEAR</button>
                          </div>
                          <div className="max-h-48 overflow-y-auto px-3 no-scrollbar">
                            <ConversationThread messages={session.messages} />
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>

                    {/* Text input */}
                    <form onSubmit={handleFormSubmit} className="mt-4 flex gap-2">
                      <input ref={inputRef} value={textInput} onChange={e => setTextInput(e.target.value)}
                        placeholder="Type your command or press Enter..."
                        disabled={submitting}
                        className="flex-1 rounded-lg border border-[rgba(255,32,32,0.2)] bg-[rgba(0,0,0,0.5)] px-4 py-2.5 font-mono text-[11px] text-text-primary placeholder:text-text-muted focus:border-[rgba(255,32,32,0.5)] focus:outline-none transition-colors"
                      />
                      <button type="submit" disabled={!textInput.trim() || submitting}
                        className="flex items-center gap-1.5 rounded-lg border border-[rgba(255,32,32,0.3)] bg-[rgba(255,32,32,0.12)] px-4 py-2.5 font-mono text-[10px] font-bold text-[#ff2020] transition-all hover:bg-[rgba(255,32,32,0.2)] disabled:opacity-40 disabled:cursor-not-allowed">
                        {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowRight className="h-3.5 w-3.5" />}
                        Run
                      </button>
                    </form>

                    {/* Suggestion chips */}
                    <div className="mt-3 flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-[9px] text-text-muted mr-1">Try saying:</span>
                      {SUGGESTIONS.map(s => (
                        <button key={s.text} onClick={() => handleTextSubmit(s.text)}
                          className="flex items-center gap-1 rounded-full border border-[rgba(255,32,32,0.15)] bg-[rgba(255,32,32,0.05)] px-2.5 py-1 font-mono text-[9px] text-text-muted transition-all hover:border-[rgba(255,32,32,0.4)] hover:text-[#ff2020] hover:-translate-y-px">
                          <span>{s.icon}</span> {s.text}
                        </button>
                      ))}
                    </div>
                  </div>

                </motion.div>
              )}

              {/* ── HISTORY TAB ── */}
              {activeTab === 'history' && (
                <motion.div key="history" initial={{ opacity:0, y:8 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0 }} transition={{ duration:0.18 }}
                  className="rounded-xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.4)] overflow-hidden">
                  <div className="px-4 py-2.5 border-b border-[rgba(255,32,32,0.1)]">
                    <span className="font-mono text-[9px] tracking-[0.15em] text-text-muted">RECENT COMMANDS</span>
                  </div>
                  <div className="max-h-[540px] overflow-y-auto p-3 space-y-1.5">
                    {!commands?.length ? (
                      <div className="flex flex-col items-center py-12 text-center">
                        <Terminal className="h-8 w-8 text-text-muted mb-3 opacity-30" />
                        <p className="font-mono text-[11px] text-text-muted">No commands yet</p>
                      </div>
                    ) : commands.slice(0, 20).map(cmd => (
                      <CommandHistoryItem key={cmd.id} cmd={cmd} onRerun={handleRerun} />
                    ))}
                  </div>
                </motion.div>
              )}

              {activeTab === 'tasks' && (
                <motion.div key="tasks" initial={{ opacity:0, y:8 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0 }} transition={{ duration:0.18 }}
                  className="rounded-xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.4)] overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-2.5 border-b border-[rgba(255,32,32,0.1)]">
                    <span className="font-mono text-[9px] tracking-[0.15em] text-text-muted flex items-center gap-1.5"><Zap className="h-3 w-3" /> TASKS</span>
                    {activeTasks.length > 0 && <span className="font-mono text-[9px] text-[#ff2020]">{activeTasks.length} ACTIVE</span>}
                  </div>
                  <div className="p-4">
                    <TaskProgressPanel tasks={tasks} onCancel={cancelTask} onSubmit={submitTask} />
                  </div>
                </motion.div>
              )}

              {activeTab === 'macros' && (
                <motion.div key="macros" initial={{ opacity:0, y:8 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0 }} transition={{ duration:0.18 }}
                  className="rounded-xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.4)] p-4">
                  <MacrosPanel />
                </motion.div>
              )}

              {activeTab === 'notes' && (
                <motion.div key="notes" initial={{ opacity:0, y:8 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0 }} transition={{ duration:0.18 }}
                  className="rounded-xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.4)] p-4">
                  <NotesPanel />
                </motion.div>
              )}

              {activeTab === 'meeting' && (
                <motion.div key="meeting" initial={{ opacity:0, y:8 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0 }} transition={{ duration:0.18 }}
                  className="rounded-xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.4)] p-4">
                  <MeetingPanel />
                </motion.div>
              )}

            </AnimatePresence>
          </div>

          {/* ── RIGHT COLUMN ── */}
          <div className="lg:col-span-2 flex flex-col gap-4">

            {/* Last result */}
            <div className="rounded-xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.5)] overflow-hidden">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-[rgba(255,32,32,0.1)]">
                <span className="font-mono text-[9px] tracking-[0.15em] text-text-muted">LAST RESULT</span>
                <button className="font-mono text-[9px] text-[#ff2020] hover:underline">View all</button>
              </div>
              <div className="p-4">
                <AnimatePresence mode="wait">
                  {!lastResult ? (
                    <motion.div key="empty" initial={{ opacity:0 }} animate={{ opacity:1 }}
                      className="flex flex-col items-center py-8 text-center">
                      <Sparkles className="h-8 w-8 text-text-muted mb-3 opacity-20" />
                      <p className="font-mono text-[10px] text-text-muted">Results will appear here</p>
                    </motion.div>
                  ) : (
                    <motion.div key={lastResult.id} initial={{ opacity:0, y:6 }} animate={{ opacity:1, y:0 }} className="space-y-3">
                      <div className="flex items-start gap-2">
                        <CheckCircle2 className="h-3.5 w-3.5 text-[#00ff88] mt-0.5 flex-shrink-0" />
                        <p className="font-mono text-[10px] text-text-secondary leading-relaxed line-clamp-2">
                          {lastResult.assistant_response || lastResult.text}
                        </p>
                      </div>
                      <p className="font-mono text-[9px] text-text-muted">{relTime(lastResult.created_at)}</p>

                      {/* Metric chips */}
                      {lastResult.status === 'completed' && (
                        <div className="grid grid-cols-2 gap-1.5">
                          {[
                            { label: 'STATUS', value: 'Optimal', color: '#00ff88' },
                            { label: 'RESULT', value: lastResult.status.toUpperCase(), color: STATUS_COLOR[lastResult.status] },
                          ].map(m => (
                            <div key={m.label} className="rounded border px-2 py-1.5"
                              style={{ borderColor: `${m.color}30`, background: `${m.color}08` }}>
                              <p className="font-mono text-[8px] text-text-muted">{m.label}</p>
                              <p className="font-mono text-[10px] font-bold" style={{ color: m.color }}>{m.value}</p>
                              <div className="mt-1 h-0.5 rounded-full w-full overflow-hidden" style={{ background: `${m.color}20` }}>
                                <div className="h-full rounded-full" style={{ width: '85%', background: m.color }} />
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      {(lastResult.status === 'running' || lastResult.status === 'queued') && (
                        <div className="flex items-center gap-2 rounded-lg border border-[rgba(255,32,32,0.2)] bg-[rgba(255,32,32,0.05)] px-3 py-2">
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-[#ff2020]" />
                          <span className="font-mono text-[10px] text-[#ff2020]">Processing…</span>
                        </div>
                      )}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>

            {/* Draft */}
            <AnimatePresence>
              {currentDraft && (
                <motion.div initial={{ opacity:0, y:10 }} animate={{ opacity:1, y:0 }} exit={{ opacity:0, y:-10 }}>
                  <p className="font-mono text-[9px] tracking-[0.15em] text-text-muted mb-2">DRAFT — REVIEW &amp; CONFIRM</p>
                  <DraftPreview draft={currentDraft} onConfirm={handleConfirmDraft} onReject={handleRejectDraft}
                    onEdit={handleEditDraft} isExecuting={executing === currentDraft.id} />
                </motion.div>
              )}
            </AnimatePresence>

            {/* Voice profiles */}
            <div className="rounded-xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.5)] overflow-hidden">
              <div className="px-4 py-2.5 border-b border-[rgba(255,32,32,0.1)]">
                <span className="font-mono text-[9px] tracking-[0.15em] text-text-muted">VOICE PROFILE</span>
              </div>
              <div className="p-3 grid grid-cols-2 gap-1.5">
                {PROFILES.map(p => (
                  <button key={p.id} onClick={() => { setActiveProfile(p.id); saveASettings({ mode: p.id as any }) }}
                    className="relative rounded-lg border p-2 text-left transition-all"
                    style={{
                      borderColor: activeProfile === p.id ? '#ff2020' : 'rgba(255,32,32,0.1)',
                      background:  activeProfile === p.id ? 'rgba(255,32,32,0.08)' : 'rgba(0,0,0,0.3)',
                      boxShadow:   activeProfile === p.id ? '0 0 10px rgba(255,32,32,0.15)' : 'none',
                    }}>
                    {activeProfile === p.id && (
                      <CheckCircle2 className="absolute top-1.5 right-1.5 h-3 w-3 text-[#ff2020]" />
                    )}
                    <span className="text-base leading-none">{p.icon}</span>
                    <p className="font-mono text-[10px] font-bold mt-1" style={{ color: activeProfile === p.id ? '#ff2020' : '#94a3b8' }}>{p.name}</p>
                    <p className="font-mono text-[8px] text-text-muted leading-tight mt-0.5">{p.desc}</p>
                  </button>
                ))}
              </div>
              <p className="px-3 pb-3 font-mono text-[8px] text-text-muted italic">
                Say <span className="text-[#ff2020] not-italic">"switch to work mode"</span> by voice too
              </p>
            </div>

            {/* Profile switcher (existing component) */}
            <div className="rounded-xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.4)] p-3">
              <ProfileSwitcher />
            </div>

            {/* System info */}
            <div className="rounded-xl border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.5)] overflow-hidden">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-[rgba(255,32,32,0.1)]">
                <span className="font-mono text-[9px] tracking-[0.15em] text-text-muted">SYSTEM INFO</span>
                <button className="font-mono text-[9px] text-[#ff2020] hover:underline">View details</button>
              </div>
              <div className="p-3 grid grid-cols-2 gap-2">
                {[
                  { icon: <Clock className="h-3.5 w-3.5 text-[#00ffff]" />, label: 'UPTIME',   value: <UptimeCounter /> },
                  { icon: <Cpu   className="h-3.5 w-3.5 text-[#ff2020]" />, label: 'OS',       value: 'Windows 11 Pro' },
                  { icon: <Brain className="h-3.5 w-3.5 text-[#00ff88]" />, label: 'MODEL',    value: 'Xyron AI Core' },
                  { icon: <Code2 className="h-3.5 w-3.5 text-[#f59e0b]" />, label: 'VERSION',  value: 'v2.0.0 Omega' },
                ].map(({ icon, label, value }) => (
                  <div key={label} className="rounded-lg border border-[rgba(255,32,32,0.1)] bg-[rgba(0,0,0,0.3)] p-2.5 flex items-center gap-2">
                    {icon}
                    <div className="min-w-0">
                      <p className="font-mono text-[8px] text-text-muted">{label}</p>
                      <p className="font-mono text-[10px] font-bold text-text-secondary truncate">{value}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* SystemInfoPanel (existing component, collapsed) */}
            <SystemInfoPanel apiBase={typeof window !== 'undefined' ? '' : 'http://localhost:8000'} autoRefresh={session.isActive} collapsed={true} />
          </div>
        </div>

        {/* ── Bottom status bar ── */}
        <BottomBar sessionActive={session.isActive} wakeListening={wakeListening} />

      </div>

      {/* Proactive notifications */}
      <ProactiveToast notifications={notifications} onDismiss={dismissNotif} />

      {/* Cinematic takeover */}
      <TakeoverOrchestrator phase={takeoverPhase} systemLine={takeoverLine} controlLevel={takeoverLevel} showDirective={takeoverDirective} onClose={stopTakeover} />

      {/* I'm Home protocol overlay */}
      <ImHomeProtocol phase={imHomePhase} data={imHomeData} logs={imHomeLogs} onDismiss={dismissImHome} />

      {/* Thoughts toggle button */}
      <button
        onClick={() => setShowThoughts(t => !t)}
        className="fixed top-20 right-6 z-50 flex items-center gap-1.5 rounded-full border border-white/20 bg-black/60 px-3 py-1.5 font-mono text-[10px] tracking-widest text-white/60 backdrop-blur-sm transition-colors hover:border-white/40 hover:text-white/90"
        style={{ top: '5.5rem' }}
      >
        <Brain className="h-3 w-3" />
        THOUGHTS
      </button>

      {/* Thought stream overlay */}
      <ThoughtStream visible={showThoughts} thoughts={thoughts} />
    </PageTransition>
  )
}

// ── Uptime counter ─────────────────────────────────────────────────────────────

function UptimeCounter() {
  const [s, setS] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setS(p => p + 1), 1000)
    return () => clearInterval(t)
  }, [])
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  return <span className="tabular-nums">{String(h).padStart(2,'0')}h {String(m).padStart(2,'0')}m {String(sec).padStart(2,'0')}s</span>
}
