'use client'

import { useCallback, useEffect, useState } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

export type OpenAIVoice = 'nova' | 'alloy' | 'echo' | 'fable' | 'onyx' | 'shimmer'
export type BehaviorMode = 'friendly' | 'professional' | 'assistant' | 'boss' | 'chill' | 'work' | 'focus' | 'morning' | 'jarvis' | 'entertainment' | 'system' | 'chat'

export interface AssistantSettings {
  voice:        OpenAIVoice
  speed:        number        // 0.75 – 1.5
  mode:         BehaviorMode
  voiceEnabled: boolean       // whether to use TTS at all
  volume:       number        // 0.0 – 1.0
}

// ── Voice metadata ────────────────────────────────────────────────────────────

export const VOICE_OPTIONS: Array<{ id: OpenAIVoice; label: string; desc: string }> = [
  { id: 'nova',    label: 'Nova',    desc: 'Clear, friendly female voice (default)' },
  { id: 'alloy',   label: 'Alloy',   desc: 'Neutral, balanced voice' },
  { id: 'echo',    label: 'Echo',    desc: 'Warm male voice' },
  { id: 'fable',   label: 'Fable',   desc: 'Expressive British male' },
  { id: 'onyx',    label: 'Onyx',    desc: 'Deep, authoritative male' },
  { id: 'shimmer', label: 'Shimmer', desc: 'Soft, gentle female voice' },
]

function _timeOfDay(): string {
  const h = new Date().getHours()
  if (h < 12) return 'morning'
  if (h < 17) return 'afternoon'
  return 'evening'
}

export function buildGreeting(mode: BehaviorMode): string {
  const tod = _timeOfDay()
  if (mode === 'friendly') {
    return "Hey! I'm Xyron, your personal AI assistant. I can play music, read the news, open apps, answer questions, and a whole lot more. Just speak naturally!"
  }
  if (mode === 'professional') {
    return `Good ${tod}. I'm Xyron — ready to assist with voice commands, news briefings, web searches, media playback, and system controls. Speak whenever you're ready.`
  }
  if (mode === 'boss') {
    return `Good ${tod}, boss. I'm Xyron, ready and at your service. Just give the word.`
  }
  // assistant (default)
  return "Hey! I'm Xyron, ready to help. You can ask me to play videos, get the news, open apps, or answer any question — just speak naturally."
}

export const MODE_OPTIONS: Array<{ id: BehaviorMode; label: string; desc: string; greeting: string }> = [
  {
    id:       'friendly',
    label:    'Friendly',
    desc:     'Warm, conversational tone',
    greeting: "Hey! I'm Xyron — warm, helpful, and always listening. Say stop when you're done.",
  },
  {
    id:       'professional',
    label:    'Professional',
    desc:     'Concise, business-focused',
    greeting: "I'm Xyron. Ready for your commands. Say stop to end the session.",
  },
  {
    id:       'assistant',
    label:    'Assistant',
    desc:     'Balanced, helpful assistant',
    greeting: "Hi! I'm Xyron. Speak naturally and I'll help with anything you need.",
  },
  {
    id:       'boss',
    label:    'Boss Mode',
    desc:     'Calls you boss — respectful & direct',
    greeting: "Ready and at your service, boss. Just give the word.",
  },
]

// ── Constants ─────────────────────────────────────────────────────────────────

const STORAGE_KEY = 'xyron:assistant-settings'

const DEFAULTS: AssistantSettings = {
  voice:        'onyx',   // global default — overridden once a profile is selected
  speed:        1.0,
  mode:         'assistant',
  voiceEnabled: true,
  volume:       0.9,
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useAssistantSettings() {
  const [settings, setSettings] = useState<AssistantSettings>(DEFAULTS)

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<AssistantSettings>
        setSettings({ ...DEFAULTS, ...parsed })
      }
    } catch { /* corrupted storage — use defaults */ }
  }, [])

  const saveSettings = useCallback((patch: Partial<AssistantSettings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch }
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch { /* ok */ }
      return next
    })
  }, [])

  return { settings, saveSettings }
}

// ── Helper: read settings synchronously (for non-hook contexts) ───────────────

export function readAssistantSettings(): AssistantSettings {
  if (typeof window === 'undefined') return DEFAULTS
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<AssistantSettings>) }
  } catch { /* ok */ }
  return DEFAULTS
}
