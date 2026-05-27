import { memo } from 'react'
import { CheckCircle2, Clock, XCircle, Loader2, ChevronRight } from 'lucide-react'
import { safeArray, safeString } from '@desktop/lib/dashboardSafeData'
import type { CommandItem } from '@desktop/services/dashboardApi'

function StatusIcon({ status }: { status: string }) {
  if (status === 'completed') return <CheckCircle2 size={11} style={{ color: '#00ff88' }} />
  if (status === 'failed')    return <XCircle size={11} style={{ color: '#ff1f2d' }} />
  if (status === 'running')   return <Loader2 size={11} style={{ color: '#ff8c00' }} className="animate-spin" />
  return <Clock size={11} style={{ color: '#6b7280' }} />
}

function statusColor(status: string): string {
  if (status === 'completed') return '#00ff88'
  if (status === 'failed')    return '#ff1f2d'
  if (status === 'running')   return '#ff8c00'
  return '#6b7280'
}

interface Props {
  commands: CommandItem[] | unknown
}

export const TasksAutomation = memo(function TasksAutomation({ commands }: Props) {
  const safe   = safeArray<CommandItem>(commands)
  const recent = safe.slice(0, 8)

  return (
    <div className="flex flex-col gap-2 h-full">
      <div className="flex items-center justify-between">
        <span className="x-section-label">Tasks & Automation</span>
        <span className="font-mono text-[8px] text-[#6b7280]">{safe.length} total</span>
      </div>

      <div className="flex-1 overflow-y-auto flex flex-col gap-1 pr-1" style={{ maxHeight: 200 }}>
        {recent.length === 0 && (
          <div className="font-mono text-[9px] text-[#374151] py-4 text-center">No recent commands yet.</div>
        )}
        {recent.map((cmd, idx) => {
          const id     = safeString(cmd?.id, String(idx))
          const text   = safeString(cmd?.text ?? (cmd as unknown as Record<string,unknown>)?.description, '—')
          const status = safeString(cmd?.status, 'queued')
          const agent  = cmd?.agent ? safeString(cmd.agent) : null

          return (
            <div key={id}
              className="flex items-start gap-2 rounded px-2 py-1.5 group cursor-default"
              style={{
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid rgba(255,255,255,0.04)',
                transition: 'border-color 0.15s',
              }}>
              <StatusIcon status={status} />
              <div className="flex-1 min-w-0">
                <div className="font-mono text-[9px] text-[#9ca3af] truncate">{text}</div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="font-mono text-[7px] tracking-widest uppercase"
                    style={{ color: statusColor(status) }}>{status}</span>
                  {agent && (
                    <span className="font-mono text-[7px] text-[#374151]">via {agent}</span>
                  )}
                </div>
              </div>
              <ChevronRight size={9} className="text-[#374151] opacity-0 group-hover:opacity-100 flex-shrink-0 mt-0.5" />
            </div>
          )
        })}
      </div>
    </div>
  )
})
