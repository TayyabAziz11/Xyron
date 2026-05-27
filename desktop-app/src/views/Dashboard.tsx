import { memo, useCallback, useMemo, useState } from 'react'
import {
  Cpu, MemoryStick, CheckSquare, HeartPulse, Clock, AlertTriangle, WifiOff,
} from 'lucide-react'
import '@desktop/styles/xyron-theme.css'
import { safeArray, safeString, safeDate, safeNumber } from '@desktop/lib/dashboardSafeData'
import { SafePanel }            from '@desktop/components/common/SafePanel'
import { useDashboardStatus }   from '@desktop/hooks/useDashboardStatus'
import { useSystemMonitor }     from '@desktop/hooks/useSystemMonitor'
import { useCommands }          from '@/hooks/useCommands'
import { HeaderGreeting }       from '@desktop/components/dashboard/HeaderGreeting'
import { WeatherTimeWidget }    from '@desktop/components/dashboard/WeatherTimeWidget'
import { DashStatCard }         from '@desktop/components/dashboard/DashStatCard'
import { SystemOverview }       from '@desktop/components/dashboard/SystemOverview'
import { ProcessPipeline }      from '@desktop/components/dashboard/ProcessPipeline'
import { LiveFeed }             from '@desktop/components/dashboard/LiveFeed'
import { TasksAutomation }      from '@desktop/components/dashboard/TasksAutomation'
import { NowPlaying }           from '@desktop/components/dashboard/NowPlaying'
import { VoiceCommandBar }      from '@desktop/components/dashboard/VoiceCommandBar'
import { TakeoverButton }       from '@desktop/components/dashboard/TakeoverButton'
import { TierBadge }           from '@desktop/components/dashboard/TierBadge'
import { UpgradeCard }         from '@desktop/components/dashboard/UpgradeCard'
import { XyronStatusCard }      from '@desktop/components/dashboard/XyronStatusCard'

const Sep = () => (
  <div className="h-px w-full flex-shrink-0"
    style={{ background: 'linear-gradient(90deg, transparent, rgba(255,35,45,0.2), transparent)' }} />
)

const Panel = memo(function Panel({ children, className = '' }: {
  children: React.ReactNode; className?: string
}) {
  return <div className={`x-panel p-3 ${className}`}>{children}</div>
})

// Convert commands array → ActivityItem shape, safe against any field shape
function commandsToActivity(commands: unknown) {
  return safeArray<Record<string, unknown>>(commands).map(c => ({
    id:          safeString(c.id ?? c.command_id, crypto.randomUUID()),
    type:        safeString(c.agent ?? c.type, 'command'),
    description: safeString(c.text ?? c.description ?? c.command, 'Unknown command'),
    timestamp:   safeDate(c.created_at ?? c.timestamp),
    success:     c.status === 'completed' ? true : c.status === 'failed' ? false : undefined,
  }))
}

export default function Dashboard() {
  const dash    = useDashboardStatus()
  const monitor = useSystemMonitor()
  const { submit } = useCommands()
  const [micActive, setMicActive] = useState(false)

  const handleSubmit = useCallback((text: string) => {
    submit(text).catch(console.error)
  }, [submit])

  const activityItems  = useMemo(() => commandsToActivity(dash.commands), [dash.commands])
  const safeCommands   = safeArray<{ status: string }>(dash.commands)
  const safeApprovals  = safeArray(dash.approvals)

  // Real-time monitor takes priority over REST-polled metrics
  const metrics   = monitor.metrics ?? dash.metrics
  const brain     = dash.brain
  const cognition = dash.cognition

  const activeProcCount = safeNumber(brain?.active_agents, 0)
  const tasksCompleted  = safeCommands.filter(c => c.status === 'completed').length
  const memoryItems     = safeNumber(brain?.memory_items, 0)
  const healthScore     = safeNumber(brain?.safety_score, dash.online ? 88 : 0)
  const uptimeStr       = safeString(metrics?.uptime_str, '—')

  const pipelineStage = useMemo(() => {
    const att = cognition?.attention
    if (att === 'LISTENING')  return 'INPUT'
    if (att === 'PROCESSING') return 'NLP'
    if (att === 'FOCUSED')    return 'PLANNER'
    if (att === 'SPEAKING')   return 'FEEDBACK'
    return undefined
  }, [cognition?.attention])

  const isProcessing = !!pipelineStage && pipelineStage !== 'FEEDBACK'

  // Memory & Context panel — safe string operations
  const contextSummary = safeString(cognition?.context_summary)
  const turnCount      = safeNumber(cognition?.turn_count, 0)

  return (
    <div className="flex flex-col h-full overflow-hidden"
      style={{ background: 'linear-gradient(180deg, #050608 0%, #08090d 100%)' }}>

      {/* ── Offline banner ───────────────────────────────────────────────────── */}
      {!dash.online && (
        <div className="flex-shrink-0 flex items-center justify-center gap-2 py-1"
          style={{ background: 'rgba(255,31,45,0.1)', borderBottom: '1px solid rgba(255,31,45,0.25)' }}>
          <WifiOff size={10} style={{ color: '#ff1f2d' }} />
          <span className="font-mono text-[9px] tracking-widest" style={{ color: 'rgba(255,31,45,0.8)' }}>
            BACKEND OFFLINE — displaying last known state
          </span>
        </div>
      )}

      {/* ── Top bar ─────────────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 flex items-center justify-between px-5 py-2.5"
        style={{ borderBottom: '1px solid rgba(255,35,45,0.15)', background: 'rgba(5,6,8,0.98)' }}>
        <HeaderGreeting online={dash.online} />
        <div className="flex items-center gap-4">
          <TierBadge />
          <WeatherTimeWidget />
        </div>
      </div>

      {/* ── Stats row ───────────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 grid grid-cols-5 gap-2 px-5 py-2">
        <DashStatCard icon={Cpu}         label="Active Processes" value={activeProcCount}    color="#ff1f2d" />
        <DashStatCard icon={CheckSquare} label="Tasks Completed"  value={tasksCompleted}     color="#00ff88" />
        <DashStatCard icon={MemoryStick} label="Memory Items"     value={memoryItems}        color="#00e5ff" />
        <DashStatCard icon={HeartPulse}  label="System Health"
          value={`${healthScore}%`}
          color={healthScore > 70 ? '#00ff88' : '#ff8c00'}
          glow={healthScore > 90} />
        <DashStatCard icon={Clock}       label="Uptime"           value={uptimeStr}          color="#a855f7" />
      </div>

      <Sep />

      {/* ── Main 3-column body ──────────────────────────────────────────────── */}
      <div className="flex-1 flex gap-2 px-4 py-2 overflow-hidden min-h-0">

        {/* Left column */}
        <div className="flex flex-col gap-2 w-[185px] flex-shrink-0 overflow-y-auto">
          <Panel>
            <SafePanel label="Xyron Status">
              <XyronStatusCard brain={brain} cognition={cognition} online={dash.online} />
            </SafePanel>
          </Panel>
          <Panel>
            <SafePanel label="Takeover">
              <TakeoverButton />
            </SafePanel>
          </Panel>
          {safeApprovals.length > 0 && (
            <Panel>
              <div className="flex items-center gap-1.5">
                <AlertTriangle size={11} style={{ color: '#ff8c00' }} />
                <span className="font-mono text-[10px] text-[#ff8c00] tracking-widest">
                  {safeApprovals.length} PENDING APPROVAL{safeApprovals.length > 1 ? 'S' : ''}
                </span>
              </div>
            </Panel>
          )}
        </div>

        {/* Centre column */}
        <div className="flex-1 flex flex-col gap-2 overflow-hidden min-w-0">

          <Panel className="flex-shrink-0">
            <SafePanel label="System Overview">
              <SystemOverview metrics={metrics} history={monitor.history} healthScore={healthScore} />
            </SafePanel>
          </Panel>

          <Panel className="flex-shrink-0">
            <SafePanel label="Process Pipeline">
              <ProcessPipeline activeStage={pipelineStage} processing={isProcessing} />
            </SafePanel>
          </Panel>

          {/* Bottom 3-up row */}
          <div className="flex-1 grid grid-cols-3 gap-2 min-h-0 overflow-hidden">

            {/* Neural Activity */}
            <Panel className="flex flex-col overflow-hidden">
              <SafePanel label="Neural Activity">
                <span className="x-section-label mb-2">Neural Activity</span>
                <div className="flex flex-col justify-center gap-1.5 mt-2">
                  {[
                    { label: 'Language', v: 82, c: '#ff1f2d' },
                    { label: 'Memory',   v: 67, c: '#a855f7' },
                    { label: 'Planning', v: 44, c: '#ff8c00' },
                    { label: 'Emotion',  v: 71, c: '#00e5ff' },
                  ].map(row => (
                    <div key={row.label} className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-[#9ca3af] w-14 text-right tracking-widest uppercase flex-shrink-0">
                        {row.label}
                      </span>
                      <div className="flex-1 h-1.5 rounded-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
                        <div className="h-full rounded-full x-bar-fill"
                          style={{ width: `${row.v}%`, background: row.c }} />
                      </div>
                      <span className="font-mono text-[10px] w-6 text-right flex-shrink-0 font-semibold" style={{ color: row.c }}>{row.v}</span>
                    </div>
                  ))}
                </div>
              </SafePanel>
            </Panel>

            {/* Memory & Context */}
            <Panel className="flex flex-col overflow-hidden">
              <SafePanel label="Memory & Context">
                <span className="x-section-label mb-2">Memory & Context</span>
                <div className="flex flex-col justify-center gap-1.5 mt-2">
                  <div className="flex justify-between items-center">
                    <span className="font-mono text-[10px] text-[#9ca3af]">Short-term</span>
                    <span className="font-mono text-[11px] font-semibold text-[#00e5ff]">{memoryItems} items</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="font-mono text-[10px] text-[#9ca3af]">Long-term</span>
                    <span className="font-mono text-[11px] font-semibold text-[#a855f7]">Persistent</span>
                  </div>
                  {contextSummary && (
                    <div className="mt-1 p-1.5 rounded text-[9px] font-mono text-[#9ca3af] leading-relaxed"
                      style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
                      {contextSummary.slice(0, 100)}
                      {contextSummary.length > 100 ? '…' : ''}
                    </div>
                  )}
                  <div className="flex justify-between items-center">
                    <span className="font-mono text-[10px] text-[#6b7280]">Turns</span>
                    <span className="font-mono text-[10px] text-[#9ca3af]">{turnCount}</span>
                  </div>
                </div>
              </SafePanel>
            </Panel>

            {/* Learning Analytics */}
            <Panel className="flex flex-col overflow-hidden">
              <SafePanel label="Learning Analytics">
                <span className="x-section-label mb-2">Learning Analytics</span>
                <div className="flex flex-col justify-center gap-1.5 mt-2">
                  {[
                    { label: 'Accuracy',   v: 94, c: '#00ff88' },
                    { label: 'Response',   v: 87, c: '#00e5ff' },
                    { label: 'Adaptation', v: 78, c: '#a855f7' },
                  ].map(row => (
                    <div key={row.label} className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-[#9ca3af] w-16 tracking-widest uppercase flex-shrink-0">
                        {row.label}
                      </span>
                      <div className="flex-1 h-1.5 rounded-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
                        <div className="h-full rounded-full x-bar-fill"
                          style={{ width: `${row.v}%`, background: row.c }} />
                      </div>
                      <span className="font-mono text-[10px] w-6 text-right flex-shrink-0 font-semibold" style={{ color: row.c }}>{row.v}</span>
                    </div>
                  ))}
                  <div className="mt-1 flex justify-between">
                    <span className="font-mono text-[10px] text-[#6b7280]">Processed</span>
                    <span className="font-mono text-[10px] text-[#9ca3af]">{safeCommands.length}</span>
                  </div>
                </div>
              </SafePanel>
            </Panel>
          </div>
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-2 w-[195px] flex-shrink-0 overflow-y-auto">
          <Panel className="flex-1">
            <SafePanel label="Live Feed">
              <LiveFeed items={activityItems} />
            </SafePanel>
          </Panel>
          <Panel>
            <SafePanel label="Tasks">
              <TasksAutomation commands={dash.commands} />
            </SafePanel>
          </Panel>
          <Panel>
            <SafePanel label="Now Playing">
              <NowPlaying />
            </SafePanel>
          </Panel>
          <UpgradeCard
            feature="autonomousAgents"
            label="Autonomous Agents"
            description="Multi-step AI agents that work in the background"
          />
        </div>
      </div>

      {/* ── Voice/command bar ────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 px-4 pb-2 pt-1">
        <SafePanel label="Voice Bar">
          <VoiceCommandBar
            onSubmit={handleSubmit}
            listening={micActive}
            onMicToggle={() => setMicActive(m => !m)}
          />
        </SafePanel>
      </div>
    </div>
  )
}
