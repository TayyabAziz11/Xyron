'use client'
import { useState } from 'react'
import { CheckCircle, XCircle, Clock, Loader2, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Command } from '@/lib/types'
import { VoicePlayer } from '@/components/ui/VoicePlayer'

interface CommandResultProps {
  command: Command
  className?: string
  // Voice props — optional so the component works without voice
  isSpeaking?: boolean
  voiceEnabled?: boolean
  onVoiceToggle?: () => void
  onSpeak?: () => void
}

const statusConfig = {
  queued: { icon: Clock, label: 'Queued', color: 'text-text-muted' },
  running: { icon: Loader2, label: 'Running', color: 'text-status-info' },
  completed: { icon: CheckCircle, label: 'Completed', color: 'text-status-success' },
  failed: { icon: XCircle, label: 'Failed', color: 'text-status-error' },
}

export function CommandResult({
  command,
  className,
  isSpeaking,
  voiceEnabled,
  onVoiceToggle,
  onSpeak,
}: CommandResultProps) {
  const config = statusConfig[command.status] ?? statusConfig.queued
  const Icon = config.icon
  const [rawExpanded, setRawExpanded] = useState(false)

  const hasVoice = onVoiceToggle !== undefined

  return (
    <div
      className={cn(
        'rounded-xl border border-surface-border bg-surface-raised p-5 space-y-3',
        className,
      )}
    >
      {/* Status + Voice controls row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Icon className={cn('h-4 w-4', config.color, command.status === 'running' && 'animate-spin')} />
          <span className={cn('text-sm font-medium', config.color)}>{config.label}</span>
          {command.intent && (
            <span className="text-xs text-text-muted">
              · {command.intent.agent} / {command.intent.skill.replace(/_/g, ' ')}
            </span>
          )}
        </div>
        {hasVoice && (
          <VoicePlayer
            isSpeaking={isSpeaking ?? false}
            voiceEnabled={voiceEnabled ?? true}
            onToggle={onVoiceToggle!}
            onSpeak={onSpeak}
          />
        )}
      </div>

      {/* Original command */}
      <div>
        <p className="text-xs text-text-muted mb-1">Command</p>
        <p className="text-sm text-text-secondary">{command.text}</p>
      </div>

      {/* Assistant response — spoken-friendly, shown prominently */}
      {command.assistant_response && (
        <div className="rounded-lg bg-brand/5 border border-brand/20 p-4">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm text-text-primary leading-relaxed">{command.assistant_response}</p>
            {onSpeak && command.status === 'completed' && (
              <button
                onClick={onSpeak}
                title="Play voice response"
                className="flex-shrink-0 p-1 rounded text-brand-light hover:bg-brand/10 transition-colors"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="currentColor">
                  <path d="M3 3.5a.5.5 0 01.757-.429l9 4.5a.5.5 0 010 .858l-9 4.5A.5.5 0 013 12.5v-9z"/>
                </svg>
              </button>
            )}
          </div>
        </div>
      )}

      {/* Raw result — collapsible */}
      {command.result && (
        <div>
          <button
            onClick={() => setRawExpanded(v => !v)}
            className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-secondary transition-colors mb-1"
          >
            {rawExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            Raw result
          </button>
          {rawExpanded && (
            <div className="rounded-lg bg-surface-overlay border border-surface-border p-3">
              <pre className="text-xs text-text-secondary whitespace-pre-wrap font-sans">
                {command.result}
              </pre>
            </div>
          )}
        </div>
      )}

      {command.error && (
        <div className="rounded-lg bg-status-error/5 border border-status-error/20 p-3">
          <p className="text-xs text-status-error">{command.error}</p>
        </div>
      )}
    </div>
  )
}
