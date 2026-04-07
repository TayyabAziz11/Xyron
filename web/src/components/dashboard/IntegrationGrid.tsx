'use client'

import Link from 'next/link'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { StatusDot } from '@/components/ui/StatusDot'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { useIntegrations } from '@/hooks/useIntegrations'
import type { IntegrationStatus } from '@/lib/types'

function statusToDot(status: IntegrationStatus): 'online' | 'offline' | 'warning' {
  if (status === 'connected') return 'online'
  if (status === 'error') return 'warning'
  return 'offline'
}

export function IntegrationGrid() {
  const { data: integrations, loading } = useIntegrations()

  return (
    <Card>
      <CardHeader>
        <CardTitle>Integrations</CardTitle>
        <Link href="/integrations" className="text-xs text-brand-light hover:underline">
          Configure
        </Link>
      </CardHeader>
      <CardContent className="pt-0">
        {loading && (
          <div className="flex justify-center py-6">
            <LoadingSpinner />
          </div>
        )}
        {!loading && integrations && (
          <div className="grid grid-cols-2 gap-2">
            {integrations.map((integration) => (
              <div
                key={integration.id}
                className="flex items-center gap-2.5 rounded-lg bg-surface-overlay border border-surface-border px-3 py-2.5"
              >
                <StatusDot status={statusToDot(integration.status as IntegrationStatus)} />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-text-secondary truncate">
                    {integration.name}
                  </p>
                  <p className="text-xs text-text-muted truncate capitalize">
                    {integration.status.replace('_', ' ')}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
