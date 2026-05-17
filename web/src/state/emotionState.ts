export interface OrbVariant {
  primaryColor:   string
  glowIntensity:  number   // 0.35–1.0
  duration:       number   // breath animation seconds
}

export const ORB_VARIANTS: Record<string, OrbVariant> = {
  CALM:       { primaryColor: '#4fc3f7', glowIntensity: 0.35, duration: 4.5 },
  FOCUSED:    { primaryColor: '#00e5ff', glowIntensity: 0.45, duration: 3.0 },
  EXCITED:    { primaryColor: '#ff6f00', glowIntensity: 0.65, duration: 1.8 },
  HYPED:      { primaryColor: '#ff1744', glowIntensity: 1.0,  duration: 0.9 },
  PLAYFUL:    { primaryColor: '#ae52d4', glowIntensity: 0.5,  duration: 2.2 },
  DOMINANT:   { primaryColor: '#b71c1c', glowIntensity: 0.85, duration: 1.2 },
  ANALYTICAL: { primaryColor: '#0097a7', glowIntensity: 0.4,  duration: 3.5 },
  LOCKED_IN:  { primaryColor: '#00bcd4', glowIntensity: 0.55, duration: 2.8 },
  INTENSE:    { primaryColor: '#e53935', glowIntensity: 0.75, duration: 1.4 },
  LATE_NIGHT: { primaryColor: '#5c6bc0', glowIntensity: 0.35, duration: 5.0 },
  PROTECTIVE: { primaryColor: '#546e7a', glowIntensity: 0.38, duration: 4.0 },
}
