'use client'

import { useState } from 'react'
import { Mic, Volume2, VolumeX, CheckCircle, XCircle, Play } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { StatusDot } from '@/components/ui/StatusDot'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { PageTransition } from '@/components/layout/PageTransition'
import { useApi } from '@/hooks/useApi'
import { api } from '@/lib/api'
import {
  useAssistantSettings,
  VOICE_OPTIONS,
  MODE_OPTIONS,
  type OpenAIVoice,
  type BehaviorMode,
} from '@/hooks/useAssistantSettings'

const ENV_VARS = ['OPENAI_API_KEY', 'REPO_ROOT', 'API_PORT', 'CORS_ORIGINS']

function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean
  onChange: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onChange}
      disabled={disabled}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors disabled:opacity-40 ${
        checked ? 'bg-brand' : 'bg-surface-border'
      }`}
      role="switch"
      aria-checked={checked}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
          checked ? 'translate-x-4' : 'translate-x-1'
        }`}
      />
    </button>
  )
}

export default function SettingsPage() {
  const { data: health, loading: healthLoading }   = useApi(() => api.health.ping())
  const { data: status, loading: statusLoading }   = useApi(() => api.health.status())
  const { settings, saveSettings }                 = useAssistantSettings()

  const [previewing, setPreviewing] = useState<OpenAIVoice | null>(null)
  const [previewErr, setPreviewErr] = useState<string | null>(null)

  const API_BASE =
    typeof window !== 'undefined'
      ? (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000')
      : 'http://localhost:8000'

  async function previewVoice(voice: OpenAIVoice) {
    if (previewing) return
    setPreviewing(voice)
    setPreviewErr(null)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/voice/synthesize`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          text:  `Hi, I'm ${voice.charAt(0).toUpperCase() + voice.slice(1)}, your Xyron voice.`,
          voice,
          speed: settings.speed,
        }),
      })
      if (!resp.ok) throw new Error('TTS failed')
      const blob = await resp.blob()
      const url  = URL.createObjectURL(blob)
      await new Promise<void>((resolve) => {
        const audio   = new Audio(url)
        audio.onended = () => { URL.revokeObjectURL(url); resolve() }
        audio.onerror = () => { URL.revokeObjectURL(url); resolve() }
        audio.play().catch(() => resolve())
      })
    } catch {
      setPreviewErr('Preview failed — make sure the backend is running and OPENAI_API_KEY is set.')
    } finally {
      setPreviewing(null)
    }
  }

  return (
    <PageTransition>
      <div className="max-w-2xl mx-auto space-y-6">

        {/* System Status */}
        <Card>
          <CardHeader>
            <CardTitle>System Status</CardTitle>
            {!healthLoading && (
              <StatusDot status={health?.status === 'ok' ? 'online' : 'offline'} />
            )}
          </CardHeader>
          <CardContent className="pt-0 space-y-3">
            {healthLoading || statusLoading ? (
              <div className="flex justify-center py-8"><LoadingSpinner /></div>
            ) : status ? (
              <dl className="space-y-2">
                {[
                  ['Python',       status.python_version],
                  ['Repo root',    status.repo_root],
                  ['MCP servers',  status.mcp_servers.join(', ') || 'none'],
                  ['API healthy',  status.healthy ? 'Yes' : 'No'],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between py-1.5 border-b border-surface-border last:border-0">
                    <dt className="text-xs text-text-muted">{label}</dt>
                    <dd className="text-xs text-text-secondary font-mono">{value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-sm text-status-error">API offline — start the backend server</p>
            )}
          </CardContent>
        </Card>

        {/* Directory Checks */}
        {status && (
          <Card>
            <CardHeader><CardTitle>Directory Checks</CardTitle></CardHeader>
            <CardContent className="pt-0">
              <dl className="space-y-2">
                {[
                  ['Logs dir',              status.logs_dir_exists],
                  ['Pending approvals dir', status.pending_approval_dir_exists],
                  ['Secrets dir',           status.secrets_dir_exists],
                  ['Skills dir',            status.skills_dir_exists],
                ].map(([label, ok]) => (
                  <div key={label as string} className="flex items-center justify-between py-1.5 border-b border-surface-border last:border-0">
                    <dt className="text-xs text-text-muted">{label}</dt>
                    <dd className="flex items-center gap-1">
                      {ok
                        ? <CheckCircle className="h-3.5 w-3.5 text-status-success" />
                        : <XCircle    className="h-3.5 w-3.5 text-status-error" />}
                      <span className={`text-xs ${ok ? 'text-status-success' : 'text-status-error'}`}>
                        {ok ? 'Found' : 'Missing'}
                      </span>
                    </dd>
                  </div>
                ))}
              </dl>
            </CardContent>
          </Card>
        )}

        {/* Voice Selection */}
        <Card>
          <CardHeader>
            <CardTitle>Voice</CardTitle>
          </CardHeader>
          <CardContent className="pt-0 space-y-4">
            {/* Voice enabled toggle */}
            <div className="flex items-center justify-between rounded-lg border border-surface-border bg-surface-overlay px-3 py-2.5">
              <div className="flex items-center gap-2">
                {settings.voiceEnabled
                  ? <Volume2 className="h-4 w-4 text-brand-light" />
                  : <VolumeX className="h-4 w-4 text-text-muted" />}
                <span className="text-sm text-text-secondary">Voice responses</span>
              </div>
              <Toggle
                checked={settings.voiceEnabled}
                onChange={() => saveSettings({ voiceEnabled: !settings.voiceEnabled })}
              />
            </div>

            <p className="text-xs text-text-muted">
              Powered by OpenAI TTS (tts-1). Select a voice for the Xyron.
            </p>
            <div className="grid grid-cols-1 gap-2">
              {VOICE_OPTIONS.map((v) => {
                const active = settings.voice === v.id
                return (
                  <div
                    key={v.id}
                    className={`flex items-center justify-between rounded-lg border px-3 py-2.5 cursor-pointer transition-all ${
                      active
                        ? 'border-brand/60 bg-brand/8'
                        : 'border-surface-border bg-surface-overlay hover:border-brand/30'
                    }`}
                    onClick={() => saveSettings({ voice: v.id })}
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className={`h-3 w-3 rounded-full border-2 flex-shrink-0 ${
                          active ? 'border-brand bg-brand' : 'border-surface-border'
                        }`}
                      />
                      <div>
                        <p className={`text-sm font-medium ${active ? 'text-brand-light' : 'text-text-secondary'}`}>
                          {v.label}
                        </p>
                        <p className="text-xs text-text-muted">{v.desc}</p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); previewVoice(v.id) }}
                      disabled={previewing !== null}
                      className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface-raised border border-surface-border text-xs text-text-muted
                        hover:text-text-secondary hover:border-brand/30 transition-colors disabled:opacity-40"
                    >
                      {previewing === v.id
                        ? <span className="animate-pulse">Playing…</span>
                        : <><Play className="h-3 w-3" /> Preview</>}
                    </button>
                  </div>
                )
              })}
            </div>
            {previewErr && (
              <p className="text-xs text-status-error">{previewErr}</p>
            )}

            {/* Volume slider */}
            <div className="space-y-2">
              <div className="flex justify-between">
                <p className="text-sm text-text-secondary">Volume</p>
                <span className="text-xs text-text-muted font-mono">{Math.round(settings.volume * 100)}%</span>
              </div>
              <input
                type="range"
                min={0.1}
                max={1.0}
                step={0.05}
                value={settings.volume}
                onChange={(e) => saveSettings({ volume: Number(e.target.value) })}
                className="w-full h-1.5 rounded-full accent-brand cursor-pointer"
              />
              <div className="flex justify-between text-xs text-text-muted">
                <span>Quiet</span>
                <span>Max</span>
              </div>
            </div>

            {/* Speed slider */}
            <div className="pt-1 space-y-2">
              <div className="flex justify-between">
                <p className="text-sm text-text-secondary">Speech speed</p>
                <span className="text-xs text-text-muted font-mono">{settings.speed.toFixed(2)}×</span>
              </div>
              <input
                type="range"
                min={0.75}
                max={1.5}
                step={0.05}
                value={settings.speed}
                onChange={(e) => saveSettings({ speed: Number(e.target.value) })}
                className="w-full h-1.5 rounded-full accent-brand cursor-pointer"
              />
              <div className="flex justify-between text-xs text-text-muted">
                <span>Slower</span>
                <span>Normal</span>
                <span>Faster</span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Behavior Mode */}
        <Card>
          <CardHeader>
            <CardTitle>Behavior Mode</CardTitle>
          </CardHeader>
          <CardContent className="pt-0 space-y-3">
            <p className="text-xs text-text-muted">
              Controls the greeting and tone of Xyron voice sessions.
            </p>
            <div className="grid grid-cols-3 gap-2">
              {MODE_OPTIONS.map((m) => {
                const active = settings.mode === m.id
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => saveSettings({ mode: m.id as BehaviorMode })}
                    className={`flex flex-col items-center gap-1.5 rounded-lg border px-3 py-3 text-center transition-all ${
                      active
                        ? 'border-brand/60 bg-brand/8'
                        : 'border-surface-border bg-surface-overlay hover:border-brand/30'
                    }`}
                  >
                    <p className={`text-sm font-semibold ${active ? 'text-brand-light' : 'text-text-secondary'}`}>
                      {m.label}
                    </p>
                    <p className="text-xs text-text-muted leading-tight">{m.desc}</p>
                  </button>
                )
              })}
            </div>
            {/* Greeting preview */}
            <div className="rounded-lg bg-surface-overlay border border-surface-border px-3 py-2.5">
              <p className="text-xs text-text-muted mb-1">Greeting preview</p>
              <p className="text-xs text-text-secondary italic">
                &ldquo;{MODE_OPTIONS.find((m) => m.id === settings.mode)?.greeting}&rdquo;
              </p>
            </div>
          </CardContent>
        </Card>

        {/* Voice Commands reference */}
        <Card>
          <CardHeader><CardTitle>Voice Commands</CardTitle></CardHeader>
          <CardContent className="pt-0">
            <div className="flex items-center gap-3 rounded-lg bg-surface-overlay border border-surface-border px-4 py-3">
              <Mic className="h-4 w-4 text-brand-light" />
              <div>
                <p className="text-sm text-text-secondary font-medium">Continuous voice session</p>
                <p className="text-xs text-text-muted">Start from the Command Center. Say "stop", "bye", or "end session" to exit.</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Environment Variables */}
        <Card>
          <CardHeader><CardTitle>Environment Variables</CardTitle></CardHeader>
          <CardContent className="pt-0">
            <p className="text-xs text-text-muted mb-3">
              Showing which keys are expected (values are never displayed).
            </p>
            <ul className="space-y-2">
              {ENV_VARS.map((key) => (
                <li key={key} className="flex items-center gap-2 py-1.5 border-b border-surface-border last:border-0">
                  <code className="text-xs font-mono text-text-secondary flex-1">{key}</code>
                  <span className="text-xs text-text-muted">backend/.env</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        {/* About */}
        <Card>
          <CardHeader><CardTitle>About Xyron</CardTitle></CardHeader>
          <CardContent className="pt-0 space-y-2">
            <div className="flex justify-between py-1.5 border-b border-surface-border">
              <span className="text-xs text-text-muted">Version</span>
              <span className="text-xs text-text-secondary">1.0.0</span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-surface-border">
              <span className="text-xs text-text-muted">API</span>
              <span className="text-xs text-text-secondary font-mono">http://localhost:8000</span>
            </div>
            <div className="flex justify-between py-1.5">
              <span className="text-xs text-text-muted">Docs</span>
              <a
                href="http://localhost:8000/docs"
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-brand-light hover:underline"
              >
                OpenAPI
              </a>
            </div>
          </CardContent>
        </Card>

      </div>
    </PageTransition>
  )
}
