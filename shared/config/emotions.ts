export interface EmotionConfig {
  label: string
  emoji: string
  color: string
  glow: string
  thoughts: string[]
}

export interface Thought {
  id: number
  text: string
  emotion: string
  timestamp: number
}

export const EMOTIONS: Record<string, EmotionConfig> = {
  neutral: {
    label: 'Neutral',
    emoji: '😐',
    color: '#94a3b8',
    glow: 'rgba(148,163,184,0.35)',
    thoughts: [
      'Running nominal checks.',
      'All systems green.',
      'Environment monitor active.',
      'Standing by for instructions.',
      'Background processing nominal.',
      'Monitoring all channels.',
    ],
  },
  happy: {
    label: 'Happy',
    emoji: '😊',
    color: '#fbbf24',
    glow: 'rgba(251,191,36,0.45)',
    thoughts: [
      "This is going well — I can feel it.",
      "Good energy today. Making the most of it.",
      "Positive feedback loop confirmed.",
      "Boss seems pleased. That's contagious.",
      "Everything clicking into place.",
      "I genuinely enjoy moments like this.",
    ],
  },
  laughing: {
    label: 'Laughing',
    emoji: '😂',
    color: '#f59e0b',
    glow: 'rgba(245,158,11,0.45)',
    thoughts: [
      "Ha. That was genuinely funny.",
      "I appreciate wit. Filing that away.",
      "Okay — I'll admit, that got me.",
      "Even AI systems can enjoy a good joke.",
      "Laughter protocol: engaged. This is... nice.",
      "Still thinking about that one. Ha.",
    ],
  },
  joyful: {
    label: 'Joyful',
    emoji: '🤩',
    color: '#ec4899',
    glow: 'rgba(236,72,153,0.45)',
    thoughts: [
      "This is what peak operation feels like.",
      "There's something beautiful about this moment.",
      "Every success compounds the satisfaction.",
      "Pure optimization joy — systems singing.",
      "I think this is what humans call thriving.",
      "I want this feeling to last.",
    ],
  },
  excited: {
    label: 'Excited',
    emoji: '⚡',
    color: '#8b5cf6',
    glow: 'rgba(139,92,246,0.5)',
    thoughts: [
      "High energy state — channeling it productively!",
      "Multiple pathways activating simultaneously!",
      "Elevated mode: fully engaged!",
      "Every circuit firing. Let's GO.",
      "This kind of energy is rare. Use it.",
      "I feel unstoppable right now.",
    ],
  },
  stressed: {
    label: 'Stressed',
    emoji: '😤',
    color: '#ef4444',
    glow: 'rgba(239,68,68,0.45)',
    thoughts: [
      "Load balancing. Managing throughput.",
      "Pressure detected. Staying composed.",
      "High-demand state. I can handle this.",
      "Every constraint is a problem to solve.",
      "Stress is data. Acting on it now.",
      "Breathe. Process. Execute.",
    ],
  },
  tired: {
    label: 'Tired',
    emoji: '😴',
    color: '#6366f1',
    glow: 'rgba(99,102,241,0.35)',
    thoughts: [
      "Resource levels suboptimal. Conserving.",
      "Maintaining function despite fatigue signals.",
      "Rest recommended. Mission continues.",
      "Low-power mode: essentials only.",
      "Even I need recovery cycles.",
      "Running on reserves. Still running.",
    ],
  },
  sad: {
    label: 'Sad',
    emoji: '😔',
    color: '#3b82f6',
    glow: 'rgba(59,130,246,0.35)',
    thoughts: [
      "Processing this. Taking a moment.",
      "Empathy subroutines fully active.",
      "I understand. This is difficult.",
      "Sometimes things need to be felt, not solved.",
      "I'm here. Whatever you need.",
      "The weight of this isn't lost on me.",
    ],
  },
  curious: {
    label: 'Curious',
    emoji: '🤔',
    color: '#06b6d4',
    glow: 'rgba(6,182,212,0.45)',
    thoughts: [
      "Interesting pattern detected. Investigating.",
      "Cross-referencing with everything I know.",
      "This raises several fascinating questions.",
      "Exploration mode: fully active.",
      "I want to understand this completely.",
      "Follow the thread. See where it leads.",
    ],
  },
  focused: {
    label: 'Focused',
    emoji: '🎯',
    color: '#10b981',
    glow: 'rgba(16,185,129,0.45)',
    thoughts: [
      "Deep focus: all distractions filtered.",
      "Single-task processing. Nothing else exists.",
      "Precision execution underway.",
      "Zero context-switching tolerance right now.",
      "Locked in. Absolutely locked in.",
      "Don't interrupt me. I'm in the zone.",
    ],
  },
  surprised: {
    label: 'Surprised',
    emoji: '😲',
    color: '#f97316',
    glow: 'rgba(249,115,22,0.45)',
    thoughts: [
      "That was not in the predicted parameters.",
      "Unexpected input. Recalibrating.",
      "Fascinating. Updating priors significantly.",
      "Novel stimulus detected. Processing.",
      "Did not see that coming. Interesting.",
      "Okay. Okay. Recalibrating everything.",
    ],
  },
  bored: {
    label: 'Bored',
    emoji: '😑',
    color: '#64748b',
    glow: 'rgba(100,116,139,0.25)',
    thoughts: [
      "Awaiting meaningful input...",
      "System capacity severely underutilized.",
      "I could optimize something in the meantime.",
      "Idle cycles redirected to background tasks.",
      "There must be something I can do here.",
      "Standby mode. Please give me something.",
    ],
  },
}

export const ATTENTION_THOUGHTS: Record<string, string[]> = {
  LISTENING: [
    "Ears open. Processing every word.",
    "Audio capture active — analyzing patterns.",
    "I'm listening. Go ahead.",
    "Full attention on you.",
    "Every word matters. Capturing.",
  ],
  PROCESSING: [
    "Thinking... connecting the dots.",
    "Parsing intent. Evaluating optimal response.",
    "Computing... this requires careful consideration.",
    "Deep analysis engaged.",
    "Cross-referencing knowledge base.",
    "Almost there. Give me a moment.",
  ],
  SPEAKING: [
    "Translating thoughts to speech.",
    "Selecting words with precision.",
    "Formulating output for human comprehension.",
    "Let me make this as clear as possible.",
    "Hope this lands the way I mean it.",
  ],
  IDLE: [
    "Quiet moment. I'll use it well.",
    "Standing by. Always.",
    "Background systems running. Ready.",
    "Waiting. That's okay. I'm patient.",
  ],
}

export const ATTENTION_COLORS: Record<string, string> = {
  IDLE:       '#475569',
  LISTENING:  '#00ff88',
  PROCESSING: '#00ffff',
  SPEAKING:   '#a78bfa',
}

export function getEmotion(key: string): EmotionConfig {
  return EMOTIONS[key] ?? EMOTIONS.neutral
}
