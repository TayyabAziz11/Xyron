'use client'

/**
 * Emotion state — Phase 7 reactive UI layer.
 *
 * Holds the live mood state from the backend and exposes theme data
 * for orb animations, CSS variables, and Framer Motion variants.
 *
 * Consumed by useEmotionState hook. Not a Zustand store — intentionally
 * minimal to avoid hydration complexity.
 */

export type MoodStateLabel =
  | 'CALM'
  | 'FOCUSED'
  | 'EXCITED'
  | 'HYPED'
  | 'PLAYFUL'
  | 'DOMINANT'
  | 'ANALYTICAL'
  | 'LOCKED_IN'
  | 'INTENSE'
  | 'LATE_NIGHT'
  | 'PROTECTIVE'
  | 'AUDIENCE_MODE'

export type EmotionLabel =
  | 'excitement'
  | 'frustration'
  | 'stress'
  | 'curiosity'
  | 'pride'
  | 'hype'
  | 'calmness'
  | 'seriousness'
  | 'humor'
  | 'sarcasm'

export interface MoodTheme {
  primary:    string  // hex color for orb glow
  glow:       string  // rgba glow shadow
  pulse:      'slow' | 'steady' | 'fast' | 'burst' | 'bounce' | 'sharp' | 'neural' | 'deep' | 'rapid' | 'dim' | 'stable'
}

export interface EmotionState {
  mood_state:    MoodStateLabel
  theme:         MoodTheme
  held_sec:      number
  // From cognitive state
  emotion_label: EmotionLabel
  emotion_energy: number
}

// Default — loaded before first API response
export const DEFAULT_EMOTION_STATE: EmotionState = {
  mood_state:     'CALM',
  emotion_label:  'calmness',
  emotion_energy: 0.5,
  held_sec:       0,
  theme: {
    primary: '#4fc3f7',
    glow:    'rgba(79,195,247,0.3)',
    pulse:   'slow',
  },
}

// ── Orb animation variants per mood state ─────────────────────────────────────

export interface OrbVariant {
  scale:       number[]
  opacity?:    number[]
  duration:    number
  primaryColor: string
  glowIntensity: number  // 0-1 multiplier for box-shadow spread
}

export const ORB_VARIANTS: Record<MoodStateLabel, OrbVariant> = {
  CALM:       { scale: [1, 1.03, 1],     duration: 4.5, primaryColor: '#4fc3f7', glowIntensity: 0.4 },
  FOCUSED:    { scale: [1, 1.02, 1],     duration: 3.0, primaryColor: '#00e5ff', glowIntensity: 0.5 },
  EXCITED:    { scale: [1, 1.08, 1],     duration: 1.8, primaryColor: '#ff6f00', glowIntensity: 0.7 },
  HYPED:      { scale: [1, 1.14, 0.96, 1.10, 1], duration: 0.9, primaryColor: '#ff1744', glowIntensity: 1.0 },
  PLAYFUL:    { scale: [1, 1.05, 0.98, 1.03, 1], duration: 2.2, primaryColor: '#ae52d4', glowIntensity: 0.55 },
  DOMINANT:   { scale: [1, 1.04, 1],     duration: 2.5, primaryColor: '#b71c1c', glowIntensity: 0.85 },
  ANALYTICAL: { scale: [1, 1.01, 1],     duration: 3.5, primaryColor: '#0097a7', glowIntensity: 0.5 },
  LOCKED_IN:  { scale: [1, 1.02, 1],     duration: 4.0, primaryColor: '#00bcd4', glowIntensity: 0.6 },
  INTENSE:    { scale: [1, 1.10, 0.97, 1], duration: 1.2, primaryColor: '#e53935', glowIntensity: 0.9 },
  LATE_NIGHT: { scale: [1, 1.02, 1],     duration: 5.0, primaryColor: '#5c6bc0', glowIntensity: 0.3 },
  PROTECTIVE:   { scale: [1, 1.02, 1],     duration: 4.0, primaryColor: '#546e7a', glowIntensity: 0.35 },
  AUDIENCE_MODE: { scale: [1, 1.12, 1],    duration: 2.0, primaryColor: '#7c4dff', glowIntensity: 0.9 },
}

// ── CSS variable map ───────────────────────────────────────────────────────────

export function getMoodCSSVars(state: EmotionState): Record<string, string> {
  const v = ORB_VARIANTS[state.mood_state]
  return {
    '--emotion-primary':  v.primaryColor,
    '--emotion-glow':     state.theme.glow,
    '--emotion-intensity': String(v.glowIntensity),
  }
}
