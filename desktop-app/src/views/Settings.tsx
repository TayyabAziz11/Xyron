
import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Mic, Volume2, VolumeX, CheckCircle, XCircle, Play, ChevronDown, ChevronRight, Zap, Radio, LogOut, User, Sparkles, Crown, ArrowRight, Check } from 'lucide-react'
import { useAuthStore } from '@desktop/stores/authStore'
import { useSubscriptionStore } from '@desktop/stores/subscriptionStore'
import { useSignOut } from '@desktop/auth/AuthProvider'
import { TIER_META } from '@desktop/lib/tiers'
import { useApi } from '@/hooks/useApi'
import { api } from '@/lib/api'
import {
  useAssistantSettings, MODE_OPTIONS,
  type OpenAIVoice, type BehaviorMode,
} from '@/hooks/useAssistantSettings'
import { cn } from '@/lib/utils'

interface KokoroVoice { id: string; label: string; desc: string }
interface KokoroGroup { name: string; voices: KokoroVoice[] }

const API_BASE = typeof window !== 'undefined' ? '' : 'http://localhost:8000'

const ENV_VARS = ['OPENAI_API_KEY', 'REPO_ROOT', 'API_PORT', 'CORS_ORIGINS']

// ── Shared UI ─────────────────────────────────────────────────────────────────

function CyberPanel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`cyber-panel p-4 ${className}`}>{children}</div>
}

function ST({ label }: { label: string }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      <span className="font-mono text-[10px] font-semibold tracking-[0.2em] text-[#ff2020]">{label}</span>
      <div className="flex-1 h-px bg-[rgba(255,32,32,0.2)]" />
    </div>
  )
}

function Toggle({ checked, onChange, disabled }: { checked: boolean; onChange: () => void; disabled?: boolean }) {
  return (
    <button type="button" onClick={onChange} disabled={disabled} role="switch" aria-checked={checked}
      className={cn(
        'relative inline-flex h-5 w-9 items-center rounded-full transition-colors disabled:opacity-40',
        checked ? 'bg-[#ff2020] shadow-[0_0_8px_rgba(255,32,32,0.5)]' : 'bg-[rgba(255,255,255,0.1)]',
      )}>
      <span className={cn(
        'inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform',
        checked ? 'translate-x-4' : 'translate-x-1',
      )} />
    </button>
  )
}

function Row({ label, sub, children }: { label: string; sub?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-[rgba(255,32,32,0.08)] py-3 last:border-0">
      <div>
        <p className="font-mono text-[11px] text-text-secondary">{label}</p>
        {sub && <p className="font-mono text-[10px] text-text-muted">{sub}</p>}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { data: health }  = useApi(() => api.health.ping())
  const { data: status }  = useApi(() => api.health.status())
  const { settings, saveSettings } = useAssistantSettings()

  const [previewing, setPreviewing]     = useState<string | null>(null)
  const [previewErr, setPreviewErr]     = useState<string | null>(null)
  const [voiceGroups, setVoiceGroups]   = useState<KokoroGroup[]>([])
  const [expandedGrp, setExpandedGrp]   = useState<Set<string>>(new Set(['American Female']))

  // Voice Identity state
  interface VoiceModeInfo { name: string; description: string; speed: number; active: boolean }
  interface VoiceProfileInfo { id: string; display_name: string; description: string; speed: number; fx_preset: string; active: boolean }
  const [voiceModes, setVoiceModes]           = useState<VoiceModeInfo[]>([])
  const [voiceProfiles, setVoiceProfiles]     = useState<VoiceProfileInfo[]>([])
  const [activeMode, setActiveMode]           = useState<string>('DEFAULT')
  const [activeProfile, setActiveProfile]     = useState<string>('echo')
  const [fxEnabled, setFxEnabled]             = useState(true)
  const [identityPreviewing, setIdentityPreviewing] = useState(false)
  const identityAudioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/voice/voices`)
      .then(r => r.json())
      .then(d => { if (d?.data?.groups) setVoiceGroups(d.data.groups) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/voice/identity/mode`)
      .then(r => r.json())
      .then(d => { setVoiceModes(d.modes ?? []); setActiveMode(d.mode ?? 'DEFAULT') })
      .catch(() => {})
    fetch(`${API_BASE}/api/v1/voice/identity/profiles`)
      .then(r => r.json())
      .then(d => { setVoiceProfiles(d.profiles ?? []); setActiveProfile(d.active_profile ?? 'echo'); setFxEnabled(d.fx_enabled ?? true) })
      .catch(() => {})
  }, [])

  async function handleSetMode(mode: string) {
    setActiveMode(mode)
    setVoiceModes(prev => prev.map(m => ({ ...m, active: m.name === mode })))
    try {
      await fetch(`${API_BASE}/api/v1/voice/identity/mode`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      })
    } catch {}
  }

  async function handleSetProfile(profile: string) {
    setActiveProfile(profile)
    setVoiceProfiles(prev => prev.map(p => ({ ...p, active: p.id === profile })))
    try {
      await fetch(`${API_BASE}/api/v1/voice/identity/profile`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile }),
      })
    } catch {}
  }

  async function handleToggleFx() {
    const next = !fxEnabled
    setFxEnabled(next)
    try {
      await fetch(`${API_BASE}/api/v1/voice/identity/fx`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      })
    } catch {}
  }

  async function handleIdentityPreview() {
    if (identityPreviewing) {
      identityAudioRef.current?.pause()
      setIdentityPreviewing(false)
      return
    }
    setIdentityPreviewing(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/voice/identity/preview`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: activeMode, profile: activeProfile }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      const blob = await resp.blob()
      const url  = URL.createObjectURL(blob)
      const audio = new Audio(url)
      identityAudioRef.current = audio
      audio.onended  = () => { URL.revokeObjectURL(url); setIdentityPreviewing(false) }
      audio.onerror  = () => { setIdentityPreviewing(false) }
      await audio.play()
    } catch {
      setIdentityPreviewing(false)
    }
  }

  async function previewVoice(voice: string) {
    if (previewing) return
    setPreviewing(voice); setPreviewErr(null)
    try {
      const label = voice.includes('_') ? voice.split('_')[1] : voice
      const resp = await fetch(`${API_BASE}/api/v1/voice/synthesize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: `Hi! I'm ${label}, your XYRON voice.`, voice }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      const blob = await resp.blob()
      const url  = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => { URL.revokeObjectURL(url); setPreviewing(null) }
      audio.onerror = () => { setPreviewing(null) }
      await audio.play()
    } catch (e) {
      setPreviewErr(e instanceof Error ? e.message : 'Preview failed')
      setPreviewing(null)
    }
  }

  const isHealthy = health?.status === 'ok'

  const navigate = useNavigate()
  const { user } = useAuthStore()
  const { tier } = useSubscriptionStore()
  const signOut = useSignOut()

  const currentTierMeta = TIER_META.find(t => t.id === tier) ?? TIER_META[0]

  return (
    <div className="space-y-5 p-5">
      <div>
        <h1 className="text-sm font-black tracking-[0.2em] text-[#ff2020]">SYSTEM SETTINGS</h1>
        <p className="font-mono text-[10px] text-text-muted">Configure XYRON AI Operator</p>
      </div>

      {/* ── Account ── */}
      <CyberPanel>
        <ST label="ACCOUNT" />
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-[#7c3aed]/10 border border-[#7c3aed]/20 flex items-center justify-center flex-shrink-0">
              {user?.imageUrl
                ? <img src={user.imageUrl} className="w-full h-full rounded-full object-cover" alt="" />
                : <User className="w-5 h-5 text-[#7c3aed]" />}
            </div>
            <div>
              <p className="text-sm font-semibold text-white">{user?.name ?? 'User'}</p>
              <p className="text-[11px] text-white/40 font-mono">{user?.email ?? '—'}</p>
            </div>
          </div>
          <button
            onClick={() => signOut()}
            className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs text-white/40 hover:text-red-400 hover:bg-red-500/8 border border-white/8 hover:border-red-500/20 transition-all"
          >
            <LogOut className="w-3.5 h-3.5" />
            Sign Out
          </button>
        </div>
      </CyberPanel>

      {/* ── Subscription ── */}
      <CyberPanel>
        <ST label="SUBSCRIPTION" />
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              {tier === 'free' && <Zap className="w-4 h-4 text-[#94a3b8]" />}
              {tier === 'pro' && <Sparkles className="w-4 h-4 text-[#7c3aed]" />}
              {tier === 'enterprise' && <Crown className="w-4 h-4 text-[#10b981]" />}
              <span className="text-sm font-bold text-white">{currentTierMeta.name} Plan</span>
            </div>
            <p className="text-[11px] text-white/40">{currentTierMeta.tagline}</p>
          </div>
          {tier === 'free' && (
            <button
              onClick={() => navigate('/onboarding/plan')}
              className="flex-shrink-0 flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold text-white bg-[#7c3aed] hover:bg-[#6d28d9] transition-colors shadow-[0_0_12px_rgba(124,58,237,0.25)]"
            >
              <Sparkles className="w-3.5 h-3.5" />
              Upgrade
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {currentTierMeta.features.slice(0, 6).map(f => (
            <div key={f} className="flex items-center gap-2">
              <Check className="w-3 h-3 text-[#00ff88] flex-shrink-0" />
              <span className="text-[11px] text-white/50">{f}</span>
            </div>
          ))}
        </div>
      </CyberPanel>

      {/* API Status */}
      <CyberPanel>
        <ST label="SYSTEM STATUS" />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <div className="flex items-center gap-2.5 rounded border border-[rgba(255,32,32,0.15)] bg-[rgba(255,32,32,0.03)] p-3">
            {isHealthy
              ? <CheckCircle className="h-4 w-4 flex-shrink-0 text-[#00ff88]" />
              : <XCircle     className="h-4 w-4 flex-shrink-0 text-[#ef4444]" />}
            <div>
              <p className="font-mono text-[11px] text-text-secondary">API BACKEND</p>
              <p className="font-mono text-[10px]" style={{ color: isHealthy ? '#00ff88' : '#ef4444' }}>
                {isHealthy ? 'CONNECTED — localhost:8000' : 'OFFLINE'}
              </p>
            </div>
          </div>
          {status && (
            <div className="rounded border border-[rgba(255,32,32,0.15)] bg-[rgba(255,32,32,0.03)] p-3">
              <p className="font-mono text-[10px] text-text-muted">PYTHON {status.python_version}</p>
              <p className="font-mono text-[10px] text-text-muted truncate">{status.repo_root}</p>
              <p className="font-mono text-[10px]" style={{ color: status.healthy ? '#00ff88' : '#ef4444' }}>
                {status.healthy ? 'ALL SYSTEMS NOMINAL' : 'DEGRADED'}
              </p>
            </div>
          )}
        </div>
      </CyberPanel>

      {/* TTS / Voice behaviour */}
      <CyberPanel>
        <ST label="VOICE BEHAVIOUR" />
        <Row label="VOICE OUTPUT" sub="Enable spoken AI responses">
          <Toggle checked={settings.voiceEnabled} onChange={() => saveSettings({ voiceEnabled: !settings.voiceEnabled })} />
        </Row>
        <Row label="VOLUME" sub="TTS output volume">
          <div className="flex items-center gap-2">
            <input type="range" min="0" max="1" step="0.05"
              value={settings.volume ?? 1}
              onChange={e => saveSettings({ volume: parseFloat(e.target.value) })}
              className="w-20 accent-[#ff2020]" />
            <span className="font-mono text-[10px] tabular-nums text-text-muted w-8">
              {Math.round((settings.volume ?? 1) * 100)}%
            </span>
          </div>
        </Row>

        {/* Behaviour mode */}
        <div className="pt-3">
          <p className="mb-2 font-mono text-[10px] text-text-muted">BEHAVIOUR MODE</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {MODE_OPTIONS.map(opt => (
              <button key={opt.id} onClick={() => saveSettings({ mode: opt.id })}
                className={cn(
                  'rounded border p-2 text-left transition-all',
                  settings.mode === opt.id
                    ? 'border-[#ff2020] bg-[rgba(255,32,32,0.08)]'
                    : 'border-[rgba(255,32,32,0.15)] hover:border-[rgba(255,32,32,0.3)]',
                )}>
                <p className="font-mono text-[10px] font-bold"
                  style={{ color: settings.mode === opt.id ? '#ff2020' : '#94a3b8' }}>
                  {opt.label}
                </p>
                <p className="mt-0.5 font-mono text-[9px] text-text-muted leading-tight">{opt.desc}</p>
              </button>
            ))}
          </div>
        </div>
      </CyberPanel>

      {/* Voice Identity */}
      <CyberPanel>
        <ST label="VOICE IDENTITY" />

        {/* Voice Mode */}
        <div className="pb-3">
          <p className="mb-2 font-mono text-[10px] text-text-muted">VOICE MODE</p>
          {voiceModes.length === 0 ? (
            <p className="font-mono text-[10px] text-text-muted italic">Backend offline</p>
          ) : (
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-5">
              {voiceModes.map(m => (
                <button key={m.name} onClick={() => handleSetMode(m.name)}
                  className={cn(
                    'rounded border p-2 text-left transition-all',
                    activeMode === m.name
                      ? 'border-[#ff2020] bg-[rgba(255,32,32,0.10)] shadow-[0_0_8px_rgba(255,32,32,0.2)]'
                      : 'border-[rgba(255,32,32,0.15)] hover:border-[rgba(255,32,32,0.35)]',
                  )}>
                  <p className="font-mono text-[10px] font-bold"
                    style={{ color: activeMode === m.name ? '#ff2020' : '#94a3b8' }}>
                    {m.name}
                  </p>
                  <p className="mt-0.5 font-mono text-[9px] text-text-muted leading-tight">{m.description}</p>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Voice Profile */}
        <div className="border-t border-[rgba(255,32,32,0.08)] pt-3 pb-3">
          <p className="mb-2 font-mono text-[10px] text-text-muted">VOICE PROFILE</p>
          {voiceProfiles.length === 0 ? (
            <p className="font-mono text-[10px] text-text-muted italic">Backend offline</p>
          ) : (
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
              {voiceProfiles.map(p => (
                <button key={p.id} onClick={() => handleSetProfile(p.id)}
                  className={cn(
                    'rounded border p-2 text-left transition-all',
                    activeProfile === p.id
                      ? 'border-[#ff2020] bg-[rgba(255,32,32,0.10)] shadow-[0_0_8px_rgba(255,32,32,0.2)]'
                      : 'border-[rgba(255,32,32,0.15)] hover:border-[rgba(255,32,32,0.35)]',
                  )}>
                  <p className="font-mono text-[10px] font-bold"
                    style={{ color: activeProfile === p.id ? '#ff2020' : '#94a3b8' }}>
                    {p.display_name}
                  </p>
                  <p className="mt-0.5 font-mono text-[9px] text-text-muted leading-tight">{p.description}</p>
                  <p className="mt-0.5 font-mono text-[9px]" style={{ color: 'rgba(255,32,32,0.5)' }}>{p.fx_preset}</p>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* FX toggle */}
        <Row label="CINEMATIC FX" sub="Reverb · EQ · stereo width">
          <Toggle checked={fxEnabled} onChange={handleToggleFx} />
        </Row>

        {/* Preview */}
        <div className="pt-3 flex items-center gap-3">
          <button onClick={handleIdentityPreview}
            className={cn(
              'flex items-center gap-2 rounded border px-3 py-1.5 font-mono text-[10px] uppercase transition-all',
              identityPreviewing
                ? 'border-[#ff2020] bg-[rgba(255,32,32,0.12)] text-[#ff2020]'
                : 'border-[rgba(255,32,32,0.25)] text-text-muted hover:border-[rgba(255,32,32,0.5)] hover:text-[#ff2020]',
            )}>
            {identityPreviewing
              ? <><Radio className="h-3.5 w-3.5 animate-pulse" /> Stop</>
              : <><Zap   className="h-3.5 w-3.5" /> Preview Voice</>}
          </button>
          <span className="font-mono text-[9px] text-text-muted">
            {activeProfile} · {activeMode} · fx {fxEnabled ? 'on' : 'off'}
          </span>
        </div>
      </CyberPanel>

      {/* OpenAI voice */}
      <CyberPanel>
        <ST label="OPENAI TTS VOICE" />
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {(['alloy','echo','fable','onyx','nova','shimmer'] as OpenAIVoice[]).map(v => (
            <button key={v} onClick={() => saveSettings({ voice: v })}
              className={cn(
                'rounded border py-2 font-mono text-[10px] uppercase transition-all',
                settings.voice === v
                  ? 'border-[#ff2020] bg-[rgba(255,32,32,0.1)] text-[#ff2020]'
                  : 'border-[rgba(255,32,32,0.15)] text-text-muted hover:border-[rgba(255,32,32,0.3)] hover:text-text-secondary',
              )}>
              {v}
            </button>
          ))}
        </div>
      </CyberPanel>

      {/* Kokoro voices */}
      {voiceGroups.length > 0 && (
        <CyberPanel>
          <ST label="KOKORO LOCAL TTS VOICES" />
          {previewErr && (
            <p className="mb-2 font-mono text-[10px] text-[#ef4444]">{previewErr}</p>
          )}
          <div className="space-y-2">
            {voiceGroups.map(grp => {
              const open = expandedGrp.has(grp.name)
              return (
                <div key={grp.name} className="rounded border border-[rgba(255,32,32,0.15)]">
                  <button onClick={() => {
                      setExpandedGrp(prev => {
                        const s = new Set(prev)
                        open ? s.delete(grp.name) : s.add(grp.name)
                        return s
                      })
                    }}
                    className="flex w-full items-center justify-between px-3 py-2 hover:bg-[rgba(255,32,32,0.04)]">
                    <span className="font-mono text-[10px] text-text-secondary">{grp.name}</span>
                    {open ? <ChevronDown className="h-3.5 w-3.5 text-text-muted" />
                           : <ChevronRight className="h-3.5 w-3.5 text-text-muted" />}
                  </button>
                  {open && (
                    <div className="grid grid-cols-1 gap-1 border-t border-[rgba(255,32,32,0.1)] p-2 sm:grid-cols-2">
                      {grp.voices.map(v => (
                        <div key={v.id}
                          className={cn(
                            'flex items-center justify-between gap-2 rounded border px-2 py-1.5 transition-all',
                            settings.voice === v.id
                              ? 'border-[#ff2020] bg-[rgba(255,32,32,0.08)]'
                              : 'border-[rgba(255,32,32,0.1)] hover:border-[rgba(255,32,32,0.25)]',
                          )}>
                          <button className="min-w-0 flex-1 text-left" onClick={() => saveSettings({ voice: v.id })}>
                            <p className="font-mono text-[10px]"
                              style={{ color: settings.voice === v.id ? '#ff2020' : '#94a3b8' }}>
                              {v.label}
                            </p>
                            <p className="font-mono text-[9px] text-text-muted truncate">{v.desc}</p>
                          </button>
                          <button onClick={() => previewVoice(v.id)} disabled={!!previewing}
                            className="flex-shrink-0 rounded border border-[rgba(255,32,32,0.2)] p-1 text-[#ff2020] disabled:opacity-40 hover:bg-[rgba(255,32,32,0.08)]">
                            {previewing === v.id
                              ? <Volume2 className="h-3.5 w-3.5 animate-pulse" />
                              : <Play    className="h-3.5 w-3.5" />}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </CyberPanel>
      )}

      {/* MCP Servers */}
      {status?.mcp_servers && status.mcp_servers.length > 0 && (
        <CyberPanel>
          <ST label="ACTIVE MCP SERVERS" />
          <div className="space-y-1.5">
            {status.mcp_servers.map(s => (
              <div key={s} className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-[#00ff88] shadow-[0_0_4px_rgba(0,255,136,0.8)]" />
                <span className="font-mono text-[11px] text-text-secondary">{s}</span>
              </div>
            ))}
          </div>
        </CyberPanel>
      )}

      {/* Env vars checklist */}
      <CyberPanel>
        <ST label="ENVIRONMENT CONFIGURATION" />
        <div className="space-y-2">
          {ENV_VARS.map(k => (
            <div key={k} className="flex items-center gap-2.5">
              <span className="font-mono text-[10px] w-36 flex-shrink-0 text-text-muted">{k}</span>
              <div className="flex-1 h-px bg-[rgba(255,32,32,0.08)]" />
              <span className="font-mono text-[10px] text-text-muted italic">set in backend/.env</span>
            </div>
          ))}
        </div>
      </CyberPanel>
    </div>
  )
}
