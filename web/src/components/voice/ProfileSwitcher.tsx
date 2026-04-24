'use client'

import { Briefcase, Coffee, Zap, Smile, UserCircle2, Star } from 'lucide-react'
import type { BehaviorMode } from '@/hooks/useAssistantSettings'

export interface VoiceProfile {
  id:       BehaviorMode | 'work' | 'chill' | 'focus'
  label:    string
  desc:     string
  voice:    string
  icon:     React.ElementType
  command:  string
}

export const PROFILES: VoiceProfile[] = [
  { id: 'assistant',    label: 'Default',      desc: 'Balanced and helpful',   voice: 'nova',    icon: UserCircle2, command: 'switch to assistant mode' },
  { id: 'work',         label: 'Work',         desc: 'Focused and efficient',  voice: 'onyx',    icon: Briefcase,   command: 'switch to work mode' },
  { id: 'chill',        label: 'Chill',        desc: 'Relaxed and casual',     voice: 'shimmer', icon: Coffee,      command: 'switch to chill mode' },
  { id: 'focus',        label: 'Focus',        desc: 'Ultra-minimal replies',  voice: 'echo',    icon: Zap,         command: 'switch to focus mode' },
  { id: 'friendly',     label: 'Friendly',     desc: 'Warm and upbeat',        voice: 'nova',    icon: Smile,       command: 'switch to friendly mode' },
  { id: 'boss',         label: 'Boss Mode',    desc: 'Calls you boss',         voice: 'onyx',    icon: Star,        command: 'switch to boss mode' },
]

import { useAssistantSettings } from '@/hooks/useAssistantSettings'

export function ProfileSwitcher() {
  const { settings, saveSettings } = useAssistantSettings()

  const handleSelect = (p: VoiceProfile) => {
    saveSettings({
      mode:  p.id as BehaviorMode,
      voice: p.voice as any,
    })
    if (p.id === 'chill' && typeof window !== 'undefined') {
      window.open('https://www.youtube.com', '_blank', 'noopener')
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <UserCircle2 className="h-4 w-4 text-accent-primary" />
        <span className="text-sm font-medium text-text-primary">Voice Profile</span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {PROFILES.map(p => {
          const Icon    = p.icon
          const active  = settings.mode === p.id
          return (
            <button
              key={p.id}
              onClick={() => handleSelect(p)}
              className={[
                'flex flex-col items-start rounded-xl border p-3 text-left transition-all',
                active
                  ? 'border-accent-primary/40 bg-accent-primary/10'
                  : 'border-surface-border bg-surface-secondary/30 hover:border-accent-primary/20 hover:bg-accent-primary/5',
              ].join(' ')}
            >
              <div className="flex w-full items-center justify-between mb-1">
                <Icon className={`h-4 w-4 ${active ? 'text-accent-primary' : 'text-text-muted'}`} />
                {active && <span className="h-1.5 w-1.5 rounded-full bg-accent-primary" />}
              </div>
              <p className={`text-xs font-medium ${active ? 'text-accent-primary' : 'text-text-primary'}`}>{p.label}</p>
              <p className="text-xs text-text-muted mt-0.5 leading-tight">{p.desc}</p>
              <p className="text-xs text-text-muted mt-1 font-mono truncate w-full opacity-50">{p.voice}</p>
            </button>
          )
        })}
      </div>

      <p className="text-xs text-text-muted text-center">
        Say <span className="font-mono text-text-secondary">&quot;switch to work mode&quot;</span> by voice too
      </p>
    </div>
  )
}
