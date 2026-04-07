'use client'

import { CheckCircle, GitBranch, Activity, Puzzle } from 'lucide-react'
import { StatCard } from '@/components/dashboard/StatCard'
import { ActivityFeed } from '@/components/dashboard/ActivityFeed'
import { IntegrationGrid } from '@/components/dashboard/IntegrationGrid'
import { QuickCommand } from '@/components/dashboard/QuickCommand'
import { PageTransition } from '@/components/layout/PageTransition'
import { useApprovals } from '@/hooks/useApprovals'
import { useWorkflows } from '@/hooks/useWorkflows'
import { useActivity } from '@/hooks/useActivity'
import { useIntegrations } from '@/hooks/useIntegrations'

export default function DashboardPage() {
  const { data: approvals, loading: approvalsLoading } = useApprovals('pending')
  const { data: workflows, loading: workflowsLoading } = useWorkflows('active')
  const { data: events, loading: eventsLoading } = useActivity(100, 1)
  const { data: integrations, loading: integrationsLoading } = useIntegrations()

  const connectedCount = integrations?.filter((i) => i.status === 'connected').length ?? null

  return (
    <PageTransition>
      <div className="space-y-6">
        {/* Stat cards */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard
            label="Pending Approvals"
            value={approvalsLoading ? null : (approvals?.length ?? 0)}
            icon={CheckCircle}
            href="/app/approvals"
            color="purple"
            loading={approvalsLoading}
          />
          <StatCard
            label="Active Workflows"
            value={workflowsLoading ? null : (workflows?.length ?? 0)}
            icon={GitBranch}
            href="/app/workflows"
            color="blue"
            loading={workflowsLoading}
          />
          <StatCard
            label="Events Today"
            value={eventsLoading ? null : (events?.length ?? 0)}
            icon={Activity}
            color="slate"
            loading={eventsLoading}
          />
          <StatCard
            label="Integrations Online"
            value={integrationsLoading ? null : connectedCount}
            icon={Puzzle}
            href="/app/integrations"
            color="green"
            loading={integrationsLoading}
          />
        </div>

        {/* Main content */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2 space-y-4">
            <QuickCommand />
          </div>
          <div>
            <ActivityFeed />
          </div>
        </div>

        {/* Integration mini-grid */}
        <IntegrationGrid />
      </div>
    </PageTransition>
  )
}
