'use client'

import { useEffect, useState } from 'react'
import { Brain, Zap, Target, Cpu, Database, Shield, Activity } from 'lucide-react'

interface BrainStatus {
  ok: boolean
  autonomy_level: number
  autonomy_label: string
  current_mode: string
  active_goal: string | null
  current_emotion: string
  current_focus: string | null
  last_agent_used: string | null
  active_agents: string[]
  identity_mode: string
  session_count: number
  total_commands: number
  last_decision: {
    route?: string
    intent?: string
    confidence?: number
    reason?: string
    at?: string
  }
  confidence_avg: number
  capability_summary: string
}

interface AgentInfo {
  id: string
  name: string
  description: string
  capabilities: string[]
  is_placeholder: boolean
}

interface MemoryRecord {
  id: string
  type: string
  text: string
  importance: number
  created_at: string
  source: string
}

const AUTONOMY_COLORS = ['#666', '#4a9eff', '#ff9a3c', '#ff2020', '#ff2020']
const AUTONOMY_LABELS = ['Reactive', 'Suggestive', 'Assisted', 'Autonomous', 'High Autonomy']

const EMOTION_COLORS: Record<string, string> = {
  neutral:       '#888',
  excited:       '#ff9a3c',
  warm:          '#ffcc44',
  warm_surprise: '#ffaa33',
  hyped:         '#ff2020',
  focused:       '#4a9eff',
  relaxed:       '#44ddaa',
  empathy:       '#cc88ff',
  ambitious:     '#ff6644',
  confident:     '#4a9eff',
}

function Pill({ text, color = '#ff2020' }: { text: string; color?: string }) {
  return (
    <span
      className="inline-block rounded px-2 py-0.5 font-mono text-[10px] font-semibold tracking-widest"
      style={{ background: `${color}22`, color, border: `1px solid ${color}44` }}
    >
      {text.toUpperCase()}
    </span>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 py-1 border-b border-[rgba(255,32,32,0.07)]">
      <span className="font-mono text-[10px] tracking-widest text-[rgba(255,255,255,0.35)] shrink-0">{label}</span>
      <div className="font-mono text-[10px] text-right text-[rgba(255,255,255,0.75)]">{children}</div>
    </div>
  )
}

function AutonomyBar({ level }: { level: number }) {
  return (
    <div className="flex gap-1 items-center">
      {[0, 1, 2, 3, 4].map(i => (
        <div
          key={i}
          className="h-2 flex-1 rounded-sm transition-all duration-500"
          style={{
            background: i <= level ? AUTONOMY_COLORS[level] : 'rgba(255,255,255,0.08)',
            boxShadow: i <= level ? `0 0 4px ${AUTONOMY_COLORS[level]}` : 'none',
          }}
        />
      ))}
      <span className="font-mono text-[10px] ml-1" style={{ color: AUTONOMY_COLORS[level] }}>
        {AUTONOMY_LABELS[level]}
      </span>
    </div>
  )
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export function BrainPanel() {
  const [status, setStatus]       = useState<BrainStatus | null>(null)
  const [agents, setAgents]       = useState<AgentInfo[]>([])
  const [memories, setMemories]   = useState<MemoryRecord[]>([])
  const [capCount, setCapCount]   = useState<number | null>(null)
  const [loading, setLoading]     = useState(true)

  useEffect(() => {
    const fetchAll = async () => {
      // Fetch all 4 endpoints in parallel
      const [statusRes, agentsRes, capsRes, memRes] = await Promise.allSettled([
        fetch(`${API_BASE}/api/v1/brain/status`),
        fetch(`${API_BASE}/api/v1/brain/agents`),
        fetch(`${API_BASE}/api/v1/brain/capabilities?mode=PUBLIC`),
        fetch(`${API_BASE}/api/v1/brain/memory/recent?n=3`),
      ])

      if (statusRes.status === 'fulfilled' && statusRes.value.ok) {
        setStatus(await statusRes.value.json())
      }
      if (agentsRes.status === 'fulfilled' && agentsRes.value.ok) {
        const d = await agentsRes.value.json()
        if (d.ok) setAgents(d.agents ?? [])
      }
      if (capsRes.status === 'fulfilled' && capsRes.value.ok) {
        const d = await capsRes.value.json()
        if (d.ok) setCapCount((d.capabilities ?? []).length)
      }
      if (memRes.status === 'fulfilled' && memRes.value.ok) {
        const d = await memRes.value.json()
        if (d.ok) setMemories(d.memories?.slice(0, 3) ?? [])
      }

      setLoading(false)
    }

    fetchAll()
    const id = setInterval(fetchAll, 3000)
    return () => clearInterval(id)
  }, [])

  if (loading) {
    return (
      <div className="cyber-panel p-4">
        <div className="flex items-center gap-2 mb-4">
          <Brain size={14} className="text-[#ff2020] animate-pulse" />
          <span className="font-mono text-[10px] tracking-[0.2em] text-[#ff2020] font-semibold">BRAIN CORE</span>
        </div>
        <div className="h-20 flex items-center justify-center">
          <span className="font-mono text-[10px] text-[rgba(255,255,255,0.3)] animate-pulse">INITIALIZING...</span>
        </div>
      </div>
    )
  }

  if (!status?.ok) {
    return (
      <div className="cyber-panel p-4">
        <div className="flex items-center gap-2 mb-2">
          <Brain size={14} className="text-[#ff2020]" />
          <span className="font-mono text-[10px] tracking-[0.2em] text-[#ff2020] font-semibold">BRAIN CORE</span>
        </div>
        <span className="font-mono text-[10px] text-[rgba(255,255,255,0.3)]">OFFLINE — start backend</span>
      </div>
    )
  }

  const emotionColor = EMOTION_COLORS[status.current_emotion?.toLowerCase() ?? 'neutral'] ?? '#888'
  const confPct = Math.round(status.confidence_avg * 100)
  const activeAgents = agents.filter(a => !a.is_placeholder)

  return (
    <div className="cyber-panel p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain size={14} className="text-[#ff2020]" />
          <span className="font-mono text-[10px] tracking-[0.2em] text-[#ff2020] font-semibold">BRAIN CORE</span>
          <span className="flex h-2 w-2 flex-shrink-0">
            <span className="absolute inline-flex h-2 w-2 animate-ping rounded-full bg-[#ff2020] opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-[#ff2020]" />
          </span>
        </div>
        <Pill text={status.identity_mode} color="#4a9eff" />
      </div>

      {/* Autonomy bar */}
      <div>
        <div className="font-mono text-[9px] tracking-widest text-[rgba(255,255,255,0.3)] mb-1">AUTONOMY LEVEL {status.autonomy_level}</div>
        <AutonomyBar level={status.autonomy_level} />
      </div>

      {/* Key stats */}
      <div className="space-y-0">
        <Row label="MODE">
          <Pill text={status.current_mode} color="#ff9a3c" />
        </Row>
        <Row label="EMOTION">
          <Pill text={status.current_emotion || 'neutral'} color={emotionColor} />
        </Row>
        <Row label="ACTIVE AGENT">
          <span style={{ color: status.last_agent_used ? '#4a9eff' : '#444' }}>
            {status.last_agent_used?.replace('_', ' ').toUpperCase() || '—'}
          </span>
        </Row>
        <Row label="GOAL">
          <span>{status.active_goal || '—'}</span>
        </Row>
        <Row label="FOCUS">
          <span>{status.current_focus || '—'}</span>
        </Row>
        {capCount !== null && (
          <Row label="CAPABILITIES">
            <span style={{ color: '#44ddaa' }}>{capCount} active</span>
          </Row>
        )}
        {activeAgents.length > 0 && (
          <Row label="AGENTS">
            <span style={{ color: '#cc88ff' }}>{activeAgents.length} registered</span>
          </Row>
        )}
      </div>

      {/* Last decision */}
      {status.last_decision?.route && (
        <div className="rounded border border-[rgba(255,32,32,0.12)] bg-[rgba(0,0,0,0.3)] p-2 space-y-1">
          <div className="font-mono text-[9px] tracking-widest text-[rgba(255,255,255,0.25)] mb-1">LAST DECISION</div>
          <div className="flex gap-2 flex-wrap">
            <Pill text={status.last_decision.route} color="#ff2020" />
            {status.last_decision.intent && (
              <Pill text={status.last_decision.intent} color="#4a9eff" />
            )}
          </div>
          {status.last_decision.confidence !== undefined && (
            <div className="flex items-center gap-2 mt-1">
              <div className="flex-1 h-1 rounded-full bg-[rgba(255,255,255,0.06)] overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{
                    width: `${Math.round(status.last_decision.confidence * 100)}%`,
                    background: '#4a9eff',
                    boxShadow: '0 0 6px #4a9eff',
                  }}
                />
              </div>
              <span className="font-mono text-[9px] text-[rgba(255,255,255,0.4)]">
                {Math.round(status.last_decision.confidence * 100)}%
              </span>
            </div>
          )}
        </div>
      )}

      {/* Recent memories */}
      {memories.length > 0 && (
        <div className="space-y-1">
          <div className="font-mono text-[9px] tracking-widest text-[rgba(255,255,255,0.25)]">RECENT MEMORY</div>
          {memories.map(m => (
            <div
              key={m.id}
              className="rounded border border-[rgba(255,255,255,0.05)] bg-[rgba(255,255,255,0.02)] px-2 py-1"
            >
              <div className="font-mono text-[9px] text-[rgba(255,255,255,0.5)] truncate">{m.text}</div>
              <div className="font-mono text-[8px] text-[rgba(255,255,255,0.2)] mt-0.5 flex gap-2">
                <span>{m.type}</span>
                <span>·</span>
                <span style={{ color: `rgba(255,255,255,${m.importance * 0.6 + 0.1})` }}>
                  {Math.round(m.importance * 100)}%
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Session stats */}
      <div className="flex gap-3 pt-1">
        <div className="flex flex-col items-center flex-1">
          <span className="font-mono text-base font-bold text-[#ff2020]" style={{ textShadow: '0 0 8px #ff2020' }}>
            {status.total_commands}
          </span>
          <span className="font-mono text-[9px] tracking-widest text-[rgba(255,255,255,0.3)]">COMMANDS</span>
        </div>
        <div className="flex flex-col items-center flex-1">
          <span className="font-mono text-base font-bold" style={{ color: '#4a9eff', textShadow: '0 0 8px #4a9eff' }}>
            {confPct}%
          </span>
          <span className="font-mono text-[9px] tracking-widest text-[rgba(255,255,255,0.3)]">CONF AVG</span>
        </div>
        <div className="flex flex-col items-center flex-1">
          <span className="font-mono text-base font-bold" style={{ color: '#ff9a3c', textShadow: '0 0 8px #ff9a3c' }}>
            {status.session_count}
          </span>
          <span className="font-mono text-[9px] tracking-widest text-[rgba(255,255,255,0.3)]">SESSIONS</span>
        </div>
      </div>

      {/* Capability summary */}
      <div className="font-mono text-[9px] text-[rgba(255,255,255,0.25)] text-center pt-1 border-t border-[rgba(255,32,32,0.07)]">
        {status.capability_summary}
      </div>
    </div>
  )
}
