'use client'

import { useIntegrations } from '@/hooks/useIntegrations'
import { api } from '@/lib/api'
import type { Integration } from '@/lib/types'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/lib/utils'

// ── Shared UI ──────────────────────────────────────────────────────────────

function CyberPanel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`cyber-panel p-4 ${className}`}>{children}</div>
}

function SectionTitle({ label, live = false }: { label: string; live?: boolean }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      {live && (
        <span className="relative flex h-2 w-2 flex-shrink-0">
          <span className="absolute inline-flex h-2 w-2 animate-ping rounded-full bg-[#ff2020] opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-[#ff2020]" />
        </span>
      )}
      <span className="font-mono text-[10px] font-semibold tracking-[0.2em] text-[#ff2020]">{label}</span>
      <div className="flex-1 h-px bg-[rgba(255,32,32,0.2)]" />
    </div>
  )
}

// ── Helpers ────────────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<
  Integration['status'],
  { label: string; color: string; bg: string; border: string; dot: string }
> = {
  connected:      { label: 'CONNECTED',      color: '#00ff88', bg: 'rgba(0,255,136,0.08)',  border: 'rgba(0,255,136,0.35)',  dot: '#00ff88' },
  disconnected:   { label: 'DISCONNECTED',   color: '#475569', bg: 'rgba(71,85,105,0.08)',  border: 'rgba(71,85,105,0.3)',   dot: '#475569' },
  not_configured: { label: 'NOT CONFIGURED', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.35)', dot: '#f59e0b' },
  error:          { label: 'ERROR',          color: '#ef4444', bg: 'rgba(239,68,68,0.08)',  border: 'rgba(239,68,68,0.35)',  dot: '#ef4444' },
}

// ── Stat Counter ───────────────────────────────────────────────────────────

function StatCounter({
  count,
  label,
  color,
  border,
  bg,
}: {
  count: number
  label: string
  color: string
  border: string
  bg: string
}) {
  return (
    <div
      className="flex flex-col items-center gap-1 px-5 py-3 rounded"
      style={{ background: bg, border: `1px solid ${border}` }}
    >
      <span className="font-mono text-xl font-bold" style={{ color }}>{count}</span>
      <span className="font-mono text-[9px] tracking-[0.2em] text-text-muted">{label}</span>
    </div>
  )
}

// ── Integration Card ───────────────────────────────────────────────────────

function IntegrationCard({ integration }: { integration: Integration }) {
  const cfg = STATUS_CONFIG[integration.status]

  return (
    <CyberPanel className="flex flex-col gap-3">
      {/* Header: name + status dot */}
      <div className="flex items-start justify-between gap-2">
        <span className="font-sans font-semibold text-sm text-text-primary leading-tight flex-1 min-w-0">
          {integration.name}
        </span>
        <span
          className="h-2.5 w-2.5 rounded-full flex-shrink-0 mt-0.5"
          style={{ background: cfg.dot, boxShadow: `0 0 6px ${cfg.dot}` }}
        />
      </div>

      {/* Status badge */}
      <div>
        <span
          className="font-mono text-[9px] font-semibold tracking-[0.15em] px-2 py-0.5 rounded"
          style={{ color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.border}` }}
        >
          {cfg.label}
        </span>
      </div>

      {/* Description */}
      <p className="font-mono text-[10px] text-text-secondary leading-relaxed flex-1">
        {integration.description}
      </p>

      {/* Footer metadata */}
      <div className="flex flex-col gap-1 pt-1 border-t border-[rgba(255,32,32,0.1)]">
        {integration.last_sync && (
          <span className="font-mono text-[9px] text-text-muted">
            LAST SYNC: <span className="text-text-secondary">{formatRelativeTime(integration.last_sync)}</span>
          </span>
        )}
        {integration.mcp_server && (
          <span className="font-mono text-[9px] text-[#00ffff]">
            MCP: <span className="text-[#00e5ff]">{integration.mcp_server}</span>
          </span>
        )}
      </div>
    </CyberPanel>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function IntegrationsPage() {
  const { data: integrations, loading, error } = useIntegrations()

  const connectedCount     = integrations?.filter((i) => i.status === 'connected').length ?? 0
  const disconnectedCount  = integrations?.filter((i) => i.status === 'disconnected').length ?? 0
  const errorCount         = integrations?.filter((i) => i.status === 'error').length ?? 0
  const notConfiguredCount = integrations?.filter((i) => i.status === 'not_configured').length ?? 0

  return (
    <div className="space-y-6">
      {/* Header */}
      <SectionTitle label="CONNECTED SERVICES & INTEGRATIONS" />

      {/* Stats row */}
      {!loading && integrations && integrations.length > 0 && (
        <div className="flex flex-wrap gap-3">
          <StatCounter
            count={connectedCount}
            label="CONNECTED"
            color="#00ff88"
            border="rgba(0,255,136,0.3)"
            bg="rgba(0,255,136,0.06)"
          />
          <StatCounter
            count={disconnectedCount}
            label="DISCONNECTED"
            color="#475569"
            border="rgba(71,85,105,0.3)"
            bg="rgba(71,85,105,0.06)"
          />
          {notConfiguredCount > 0 && (
            <StatCounter
              count={notConfiguredCount}
              label="NOT CONFIGURED"
              color="#f59e0b"
              border="rgba(245,158,11,0.3)"
              bg="rgba(245,158,11,0.06)"
            />
          )}
          {errorCount > 0 && (
            <StatCounter
              count={errorCount}
              label="ERRORS"
              color="#ef4444"
              border="rgba(239,68,68,0.3)"
              bg="rgba(239,68,68,0.06)"
            />
          )}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center gap-3 py-12 justify-center">
          <span className="h-4 w-4 rounded-full border-2 border-[#ff2020] border-t-transparent animate-spin" />
          <span className="font-mono text-xs text-[#ff2020] tracking-[0.2em]">SCANNING INTEGRATIONS...</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <CyberPanel>
          <span className="font-mono text-xs text-[#ef4444]">
            ERROR: Failed to load integrations — {error.message}
          </span>
        </CyberPanel>
      )}

      {/* Empty */}
      {!loading && !error && (!integrations || integrations.length === 0) && (
        <div className="flex flex-col items-center gap-3 py-16">
          <span className="font-mono text-sm tracking-[0.25em] text-text-muted">NO INTEGRATIONS CONFIGURED</span>
          <span className="font-mono text-[10px] tracking-[0.3em] text-[#f59e0b]">◆ NO CREDENTIALS DETECTED ◆</span>
        </div>
      )}

      {/* Integration grid */}
      {!loading && integrations && integrations.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {integrations.map((integration) => (
            <IntegrationCard key={integration.id} integration={integration} />
          ))}
        </div>
      )}

      {/* Credentials reference */}
      <CyberPanel>
        <SectionTitle label="CREDENTIAL FILE REFERENCE" />
        <p className="font-mono text-[10px] text-text-secondary mb-3">
          Place credential files in{' '}
          <code className="text-[#00ffff] bg-[rgba(0,255,255,0.06)] px-1.5 py-0.5 rounded">.secrets/</code>
          {' '}at the repo root to activate integrations.
        </p>
        <ul className="space-y-1.5">
          {([
            ['Gmail',     'gmail_token.json'],
            ['LinkedIn',  'linkedin_credentials.json'],
            ['WhatsApp',  'whatsapp_credentials.json'],
            ['Odoo',      'odoo_credentials.json'],
            ['Instagram', 'instagram_credentials.json'],
            ['OpenAI',    'ai_credentials.json'],
          ] as [string, string][]).map(([name, file]) => (
            <li key={name} className="flex items-center gap-3 text-[9px] font-mono">
              <span className="text-text-secondary tracking-wide w-20">{name}</span>
              <code className="text-[#00ffff] bg-[rgba(0,255,255,0.04)] border border-[rgba(0,255,255,0.15)] px-2 py-0.5 rounded">
                .secrets/{file}
              </code>
            </li>
          ))}
        </ul>
      </CyberPanel>
    </div>
  )
}
