
import { useCallback, useEffect, useRef, useState } from 'react'

export interface VoiceSettings {
  enabled: boolean
  volume: number
  rate: number       // speech rate: 0.5 – 2.0 (Web Speech API scale)
  autoPlay: boolean
}

const STORAGE_KEY = 'ai-operator-voice-settings'

function defaults(): VoiceSettings {
  return { enabled: true, volume: 0.9, rate: 1.0, autoPlay: true }
}

function loadSettings(): VoiceSettings {
  if (typeof window === 'undefined') return defaults()
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...defaults(), ...JSON.parse(raw) }
  } catch { /* ignore */ }
  return defaults()
}

// Check Web Speech API availability (works in all modern Chromium/browsers)
function hasSpeechSynthesis(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

export function useVoice() {
  const [settings, setSettings] = useState<VoiceSettings>(loadSettings)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null)

  const saveSettings = useCallback((next: Partial<VoiceSettings>) => {
    setSettings(prev => {
      const merged = { ...prev, ...next }
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(merged)) } catch { /* ignore */ }
      return merged
    })
  }, [])

  const stop = useCallback(() => {
    if (hasSpeechSynthesis()) window.speechSynthesis.cancel()
    setIsSpeaking(false)
  }, [])

  const speak = useCallback((text: string) => {
    if (!settings.enabled || !text?.trim()) return
    if (!hasSpeechSynthesis()) return

    // Cancel any in-progress speech
    window.speechSynthesis.cancel()

    const utterance = new SpeechSynthesisUtterance(text.trim())
    utterance.volume = settings.volume
    utterance.rate   = settings.rate
    utterance.pitch  = 1.0

    utterance.onstart = () => setIsSpeaking(true)
    utterance.onend   = () => setIsSpeaking(false)
    utterance.onerror = () => setIsSpeaking(false)

    utteranceRef.current = utterance
    setIsSpeaking(true)
    window.speechSynthesis.speak(utterance)
  }, [settings])

  // Cleanup on unmount
  useEffect(() => () => stop(), [stop])

  return { settings, saveSettings, speak, stop, isSpeaking, ttsAvailable: hasSpeechSynthesis() }
}
