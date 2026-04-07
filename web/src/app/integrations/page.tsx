'use client'

import { Puzzle } from 'lucide-react'
import { IntegrationCard } from '@/components/integrations/IntegrationCard'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { useIntegrations } from '@/hooks/useIntegrations'

export default function IntegrationsPage() {
  const { data: integrations, loading, error } = useIntegrations()

  const connectedCount = integrations?.filter((i) => i.status === 'connected').length ?? 0
  const total = integrations?.length ?? 0

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Stats row */}
      {!loading && integrations && (
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-status-success/10 border border-status-success/20 px-3 py-1.5">
            <p className="text-sm font-medium text-status-success">
              {connectedCount} / {total} connected
            </p>
          </div>
        </div>
      )}

      {loading && (
        <div className="flex justify-center py-16">
          <LoadingSpinner size="lg" />
        </div>
      )}
      {error && (
        <p className="text-sm text-status-error">Failed to load integrations: {error.message}</p>
      )}
      {!loading && !error && (!integrations || integrations.length === 0) && (
        <EmptyState
          icon={Puzzle}
          title="No integrations found"
          description="Integration status is checked against .secrets/ credential files."
        />
      )}
      {!loading && integrations && integrations.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {integrations.map((integration) => (
            <IntegrationCard key={integration.id} integration={integration} />
          ))}
        </div>
      )}

      {/* Setup guide */}
      <div className="rounded-xl border border-surface-border bg-surface-raised p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-2">Adding credentials</h3>
        <p className="text-sm text-text-secondary mb-3">
          Integrations are activated by placing credential files in <code className="font-mono text-xs bg-surface-overlay px-1.5 py-0.5 rounded">.secrets/</code> at the repo root.
        </p>
        <ul className="space-y-1">
          {[
            ['Gmail', 'gmail_token.json'],
            ['LinkedIn', 'linkedin_credentials.json'],
            ['WhatsApp', 'whatsapp_credentials.json'],
            ['Odoo', 'odoo_credentials.json'],
            ['Instagram', 'instagram_credentials.json'],
            ['OpenAI', 'ai_credentials.json'],
          ].map(([name, file]) => (
            <li key={name} className="flex items-center gap-2 text-xs text-text-muted">
              <span className="text-text-secondary font-medium w-20">{name}</span>
              <code className="font-mono bg-surface-overlay px-1.5 py-0.5 rounded">.secrets/{file}</code>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
