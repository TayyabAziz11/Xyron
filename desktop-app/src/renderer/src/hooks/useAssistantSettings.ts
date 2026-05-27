import { useCallback, useEffect, useState } from 'react'

export type OpenAIVoice = 'nova' | 'alloy' | 'echo' | 'fable' | 'onyx' | 'shimmer'
export type BehaviorMode = 'friendly' | 'professional' | 'assistant' | 'boss' | 'work' | 'chill' | 'focus'

export interface AssistantSettings {
  voice:  OpenAIVoice
  speed:  number
  mode:   BehaviorMode
  voiceEnabled: boolean
  volume: number
}

export const VOICE_OPTIONS: Array<{ id: OpenAIVoice; label: string; desc: string }> = [
  { id: 'nova',    label: 'Nova',    desc: 'Clear, friendly female voice (default)' },
  { id: 'alloy',   label: 'Alloy',   desc: 'Neutral, balanced voice' },
  { id: 'echo',    label: 'Echo',    desc: 'Warm male voice' },
  { id: 'fable',   label: 'Fable',   desc: 'Expressive British male' },
  { id: 'onyx',    label: 'Onyx',    desc: 'Deep, authoritative male' },
  { id: 'shimmer', label: 'Shimmer', desc: 'Soft, gentle female voice' },
]

export const MODE_VOICE_MAP: Record<BehaviorMode, OpenAIVoice> = {
  assistant:    'nova',
  friendly:     'nova',
  chill:        'shimmer',
  work:         'onyx',
  focus:        'echo',
  professional: 'alloy',
  boss:         'onyx',
}

export const MODE_OPTIONS: Array<{ id: BehaviorMode; label: string; desc: string; emoji: string }> = [
  { id: 'assistant',    label: 'Assistant',    desc: 'Balanced, helpful assistant',          emoji: '🤖' },
  { id: 'friendly',     label: 'Friendly',     desc: 'Warm, conversational tone',            emoji: '😊' },
  { id: 'chill',        label: 'Chill',        desc: 'Relaxed, casual, easy-going',          emoji: '😎' },
  { id: 'work',         label: 'Work Mode',    desc: 'Focused, efficient, task-first',       emoji: '💼' },
  { id: 'focus',        label: 'Focus Mode',   desc: 'Ultra-minimal, one-sentence replies',  emoji: '🎯' },
  { id: 'professional', label: 'Professional', desc: 'Concise, formal, no filler',           emoji: '📋' },
  { id: 'boss',         label: 'Boss Mode',    desc: 'Calls you boss — respectful & direct', emoji: '👑' },
]

function _timeOfDay(): string {
  const h = new Date().getHours()
  if (h < 12) return 'morning'
  if (h < 17) return 'afternoon'
  return 'evening'
}

export function buildGreeting(mode: BehaviorMode, displayName?: string): string {
  const tod  = _timeOfDay()
  const name = displayName && displayName !== 'boss' ? displayName : null
  if (mode === 'friendly')     return `Hey${name ? `, ${name}` : ''}! I'm Xyron, your personal AI assistant. I can play music, read the news, open apps, answer questions, and a whole lot more. Just speak naturally!`
  if (mode === 'professional') return `Good ${tod}${name ? `, ${name}` : ''}. I'm Xyron — ready to assist. Speak whenever you're ready.`
  if (mode === 'boss')         return `Good ${tod}, ${name ?? 'boss'}. I'm Xyron, ready and at your service. Just give the word.`
  if (mode === 'work')         return `Good ${tod}${name ? `, ${name}` : ''}. Work mode active — I'll keep things focused and brief. What's first?`
  if (mode === 'chill')        return `Hey${name ? `, ${name}` : ''}, Xyron here. No rush — just say what you need and I've got you.`
  if (mode === 'focus')        return `Focus mode on. I'll keep it short. Go ahead.`
  return `Hey${name ? `, ${name}` : ''}! I'm Xyron, ready to help. You can ask me to play videos, get the news, open apps, or answer any question — just speak naturally.`
}

const STORAGE_KEY = 'xyron:assistant-settings-v2'

const DEFAULTS: AssistantSettings = {
  voice:        'nova',
  speed:        1.0,
  mode:         'assistant',
  voiceEnabled: true,
  volume:       0.9,
}

export function useAssistantSettings() {
  const [settings, setSettings] = useState<AssistantSettings>(DEFAULTS)

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) setSettings({ ...DEFAULTS, ...(JSON.parse(raw) as Partial<AssistantSettings>) })
    } catch { /* corrupted — use defaults */ }
  }, [])

  const saveSettings = useCallback((patch: Partial<AssistantSettings>) => {
    setSettings((prev) => {
      // Auto-update voice when mode changes (unless voice explicitly set in same patch)
      const next = patch.mode && !patch.voice
        ? { ...prev, ...patch, voice: MODE_VOICE_MAP[patch.mode] }
        : { ...prev, ...patch }
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch { /* ok */ }
      return next
    })
  }, [])

  return { settings, saveSettings }
}

export function readAssistantSettings(): AssistantSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<AssistantSettings>) }
  } catch { /* ok */ }
  return DEFAULTS
}
