'use client'

import { Mic, CheckCircle, XCircle } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { StatusDot } from '@/components/ui/StatusDot'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { useApi } from '@/hooks/useApi'
import { api } from '@/lib/api'

const ENV_VARS = [
  'OPENAI_API_KEY',
  'REPO_ROOT',
  'API_PORT',
  'CORS_ORIGINS',
]

export default function SettingsPage() {
  const { data: health, loading: healthLoading } = useApi(() => api.health.ping())
  const { data: status, loading: statusLoading } = useApi(() => api.health.status())

  return (
    <div className="max-w-2xl mx-auto space-y-6 animate-fade-in">
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

      {/* Directory checks */}
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

      {/* Voice settings */}
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

      {/* Environment */}
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

      {/* About */}
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
              OpenAPI ↗
            </a>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
