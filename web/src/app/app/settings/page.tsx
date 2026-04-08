'use client'

import { Mic } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { StatusDot } from '@/components/ui/StatusDot'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { PageTransition } from '@/components/layout/PageTransition'
import { useApi } from '@/hooks/useApi'
import { useVoice } from '@/hooks/useVoice'
import { api } from '@/lib/api'
import { CheckCircle, XCircle, Volume2, VolumeX } from 'lucide-react'

const ENV_VARS = [
  'OPENAI_API_KEY',
  'REPO_ROOT',
  'API_PORT',
  'CORS_ORIGINS',
]

export default function SettingsPage() {
  const { data: health, loading: healthLoading } = useApi(() => api.health.ping())
  const { data: status, loading: statusLoading } = useApi(() => api.health.status())
  const { settings, saveSettings, speak, isSpeaking } = useVoice()

  return (
    <PageTransition>
      <div className="max-w-2xl mx-auto space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>System Status</CardTitle>
            {!healthLoading && (
              <StatusDot status={health?.status === 'ok' ? 'online' : 'offline'} />
            )}
          </CardHeader>
          <CardContent className="pt-0 space-y-3">
            {healthLoading || statusLoading ? (
              <div className="flex justify-center py-8">
                <LoadingSpinner />
              </div>
            ) : status ? (
              <dl className="space-y-2">
                {[
                  ['Python', status.python_version],
                  ['Repo root', status.repo_root],
                  ['MCP servers', status.mcp_servers.join(', ') || 'none'],
                  ['API healthy', status.healthy ? 'Yes' : 'No'],
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

        {status && (
          <Card>
            <CardHeader>
              <CardTitle>Directory Checks</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <dl className="space-y-2">
                {[
                  ['Logs dir', status.logs_dir_exists],
                  ['Pending approvals dir', status.pending_approval_dir_exists],
                  ['Secrets dir', status.secrets_dir_exists],
                  ['Skills dir', status.skills_dir_exists],
                ].map(([label, ok]) => (
                  <div key={label as string} className="flex items-center justify-between py-1.5 border-b border-surface-border last:border-0">
                    <dt className="text-xs text-text-muted">{label}</dt>
                    <dd className="flex items-center gap-1">
                      {ok ? (
                        <CheckCircle className="h-3.5 w-3.5 text-status-success" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 text-status-error" />
                      )}
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

        {/* Voice Settings */}
        <Card>
          <CardHeader>
            <CardTitle>Voice Settings</CardTitle>
          </CardHeader>
          <CardContent className="pt-0 space-y-4">
            {/* Enable toggle */}
            <div className="flex items-center justify-between py-1.5 border-b border-surface-border">
              <div>
                <p className="text-sm text-text-secondary">Voice responses</p>
                <p className="text-xs text-text-muted">Speak results aloud when commands complete</p>
              </div>
              <button
                onClick={() => saveSettings({ enabled: !settings.enabled })}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                  settings.enabled ? 'bg-brand' : 'bg-surface-border'
                }`}
                role="switch"
                aria-checked={settings.enabled}
              >
                <span
                  className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                    settings.enabled ? 'translate-x-4' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            {/* Auto-play toggle */}
            <div className="flex items-center justify-between py-1.5 border-b border-surface-border">
              <div>
                <p className="text-sm text-text-secondary">Auto-play responses</p>
                <p className="text-xs text-text-muted">Automatically speak when a command completes</p>
              </div>
              <button
                onClick={() => saveSettings({ autoPlay: !settings.autoPlay })}
                disabled={!settings.enabled}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                  settings.autoPlay && settings.enabled ? 'bg-brand' : 'bg-surface-border'
                } disabled:opacity-40`}
                role="switch"
                aria-checked={settings.autoPlay}
              >
                <span
                  className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                    settings.autoPlay && settings.enabled ? 'translate-x-4' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            {/* Speech rate slider */}
            <div className="py-1.5 border-b border-surface-border space-y-2">
              <div className="flex justify-between">
                <p className="text-sm text-text-secondary">Speech speed</p>
                <span className="text-xs text-text-muted font-mono">{settings.rate} WPM</span>
              </div>
              <input
                type="range"
                min={80}
                max={250}
                step={5}
                value={settings.rate}
                disabled={!settings.enabled}
                onChange={e => saveSettings({ rate: Number(e.target.value) })}
                className="w-full h-1.5 rounded-full accent-brand disabled:opacity-40 cursor-pointer"
              />
              <div className="flex justify-between text-xs text-text-muted">
                <span>Slow</span>
                <span>Fast</span>
              </div>
            </div>

            {/* Volume slider */}
            <div className="py-1.5 border-b border-surface-border space-y-2">
              <div className="flex justify-between">
                <p className="text-sm text-text-secondary">Volume</p>
                <span className="text-xs text-text-muted font-mono">{Math.round(settings.volume * 100)}%</span>
              </div>
              <div className="flex items-center gap-2">
                <VolumeX className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={settings.volume}
                  disabled={!settings.enabled}
                  onChange={e => saveSettings({ volume: Number(e.target.value) })}
                  className="flex-1 h-1.5 rounded-full accent-brand disabled:opacity-40 cursor-pointer"
                />
                <Volume2 className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
              </div>
            </div>

            {/* Test button */}
            <div className="flex items-center justify-between pt-1">
              <div>
                <p className="text-sm text-text-secondary">Test voice</p>
                <p className="text-xs text-text-muted">
                  Engine: pyttsx3 / espeak-ng
                </p>
              </div>
              <button
                onClick={() => speak('Hello, I am AI Operator. Your voice assistant is ready.')}
                disabled={!settings.enabled || isSpeaking}
                className="px-3 py-1.5 rounded-lg bg-brand/10 border border-brand/25 text-xs text-brand-light
                  hover:bg-brand/15 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {isSpeaking ? 'Speaking…' : 'Play sample'}
              </button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Voice Commands</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="flex items-center gap-3 rounded-lg bg-surface-overlay border border-surface-border px-4 py-3">
              <Mic className="h-4 w-4 text-text-muted" />
              <div>
                <p className="text-sm text-text-secondary font-medium">Push-to-talk</p>
                <p className="text-xs text-text-muted">Coming in the next version</p>
              </div>
              <span className="ml-auto text-xs text-text-muted bg-surface-border px-2 py-0.5 rounded-full">
                Soon
              </span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Environment Variables</CardTitle>
          </CardHeader>
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

        <Card>
          <CardHeader>
            <CardTitle>About AI Operator</CardTitle>
          </CardHeader>
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
