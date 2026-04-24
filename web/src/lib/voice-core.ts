'use client'

/**
 * voice-core.ts — Platform-agnostic voice session logic.
 *
 * Shared between web and (conceptually) desktop. All functions here are pure
 * utilities or classes — no React, no hooks, no DOM globals assumed in type
 * signatures. Callers pass refs and callbacks explicitly.
 */

// ── Types ─────────────────────────────────────────────────────────────────────

export type SessionState =
  | 'idle' | 'greeting' | 'listening' | 'transcribing'
  | 'processing' | 'speaking' | 'stopped'

export interface ConvMessage {
  id:        string
  role:      'user' | 'assistant' | 'system'
  text:      string
  timestamp: Date
  status:    'processing' | 'done' | 'error'
  commandId?: string
}

export type HistoryEntry = { role: 'user' | 'assistant'; text: string }

// ── VAD configuration ─────────────────────────────────────────────────────────

export const VAD = {
  calibrationFrames:   25,
  thresholdMultiplier: 4.5,
  minThreshold:        0.040,
  maxThreshold:        0.150,
  exitMultiplier:      1.05,      // exit when RMS barely above noise floor (fixes "still listening" in noisy rooms)
  resumeHysteresis:    1.30,
  smoothingAlpha:      0.55,      // balanced smoothing
  speechMinFrames:     3,
  silenceAfterMs:      750,       // 750ms of silence = natural end of speech
  noSpeechTimeoutMs:   6_000,
  maxTotalMs:          30_000,
  tickIntervalMs:      30,        // use setInterval not rAF (rAF throttles in background tabs)
} as const

// ── Stop phrases ──────────────────────────────────────────────────────────────

const STOP_PHRASES = new Set([
  'stop', 'ok stop', 'stop now', 'ok stop now', 'stop listening',
  'end session', 'exit', 'exit conversation',
  'bye', 'goodbye', "that's all", 'thats all',
  'close', 'quit', 'end', 'cancel', 'never mind', 'nevermind',
])

export function isStopPhrase(text: string): boolean {
  // Strip trailing punctuation that STT may add (e.g. "stop." → "stop")
  const lower = text.toLowerCase().trim().replace(/[.!?,;:]+$/, '').trim()
  return (
    STOP_PHRASES.has(lower) ||
    [...STOP_PHRASES].some((p) => lower.startsWith(p + ' ') || lower.endsWith(' ' + p))
  )
}

// ── Identity / capability patterns ───────────────────────────────────────────

type IdentityEntry = {
  pattern:      RegExp
  response:     string | (() => string)
  bossResponse?: string | (() => string)
}

const IDENTITY: IdentityEntry[] = [
  {
    pattern: /\b(how are you|how('s| is) it going|how are things|you good|you okay|you alright|how do you feel|how('?re| are) you doing)\b/i,
    response: () => {
      const replies = [
        "Doing great, thanks for asking! What can I help you with?",
        "All good on my end! What do you need?",
        "Feeling sharp and ready to help. What's up?",
        "Pretty good! What can I do for you?",
      ]
      return replies[Math.floor(Math.random() * replies.length)]
    },
    bossResponse: () => {
      const replies = [
        "Sharp and ready, boss! What do you need?",
        "All good, boss. What's the task?",
        "Ready to work, boss. What can I do for you?",
        "Feeling great, boss. What's next?",
      ]
      return replies[Math.floor(Math.random() * replies.length)]
    },
  },
  {
    pattern: /\b(who (built|created|developed|made) you|who('s| is) your (developer|creator|builder|maker))\b/i,
    response: "I was built by Tayyab Aziz — he created me as a voice-first AI assistant to help you get things done hands-free.",
    bossResponse: "I was built by Tayyab Aziz, boss — a voice-first AI assistant designed to handle whatever you need.",
  },
  {
    pattern: /\b(what('s| is) your name|who are you)\b/i,
    response: "I'm Xyron, your voice assistant. Built by Tayyab Aziz to help you manage your day. Just say what you need!",
    bossResponse: "I'm Xyron, boss — your personal voice assistant, built by Tayyab Aziz. At your service.",
  },
  {
    pattern: /\b(what (can|do) you do|what are your (capabilities|features|skills)|how can you help|what('?re| are) you (capable|able) of)\b/i,
    response: "Quite a lot, actually! I can open any app or setting, play YouTube, check live prices, read the news, answer questions, and handle voice commands for your whole system. Just ask.",
    bossResponse: "Quite a lot, boss. I can open any app, control your settings, play YouTube, check prices, pull up the news, answer questions — full system control, all by voice. Just say the word.",
  },
  {
    pattern: /\b(what('?s| is)( the| today'?s?)? (date|day)|what day is (it )?today|today'?s? date|current (date|day))\b/i,
    response: () => {
      const now = new Date()
      return `Today is ${now.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}.`
    },
    bossResponse: () => {
      const now = new Date()
      return `Today is ${now.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}, boss.`
    },
  },
  {
    pattern: /\b(what('?s| is) the time|what time is it|current time)\b/i,
    response: () => `It's ${new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })}.`,
    bossResponse: () => `It's ${new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })}, boss.`,
  },
  {
    pattern: /\b(are you (real|human|alive|conscious)|do you (think|feel|have feelings)|are you smart)\b/i,
    response: "I'm an AI, but I'm pretty capable! I think clearly, learn from our conversation, and I'm always here when you need me.",
    bossResponse: "I'm an AI, boss — but a capable one. I'm always here, always sharp, and fully focused on you.",
  },
  {
    pattern: /\b(thank(s| you)|cheers|appreciate it|that('?s| is) (great|awesome|helpful|perfect|nice))\b/i,
    response: () => {
      const replies = ["Happy to help!", "Anytime!", "Of course!", "That's what I'm here for!"]
      return replies[Math.floor(Math.random() * replies.length)]
    },
    bossResponse: () => {
      const replies = ["Of course, boss.", "Anytime, boss.", "That's what I'm here for, boss.", "Happy to serve, boss."]
      return replies[Math.floor(Math.random() * replies.length)]
    },
  },
]

export function checkIdentityResponse(text: string, mode?: string): string | null {
  for (const entry of IDENTITY) {
    if (entry.pattern.test(text)) {
      const pick = (mode === 'boss' && entry.bossResponse) ? entry.bossResponse : entry.response
      return typeof pick === 'function' ? pick() : pick
    }
  }
  return null
}

// ── System action detection ───────────────────────────────────────────────────

export interface ParsedEntities {
  name?:   string
  path?:   string
  app?:    string
  query?:  string
  target?: string
}

export interface SystemAction {
  response:        string
  intent?:         string         // e.g. "create_folder", "open_app", "open_url"
  entities?:       ParsedEntities // structured entities extracted from transcript
  url?:            string
  app?:            string
  path?:           string
  getSystemInfo?:  boolean
  youtubeQuery?:   string
  newsQuery?:      string
  newsQueryTopic?: boolean
  priceQuery?:     string
  rememberFact?:   string   // user said "remember that X" → store in backend
  queryMemory?:    boolean  // user said "what do you remember" → fetch + speak
  // ── New capabilities ────────────────────────────────────────────────────
  wikiTopic?:      string   // "what is X" / "who is Y" → Wikipedia lookup
  readClipboard?:  boolean  // "what's on my clipboard"
  writeClipboard?: string   // "copy this to clipboard: X"
  readScreen?:     boolean  // "what's on my screen" → vision
  typeText?:       string   // "type this for me: X"
  winMinimize?:    boolean  // "minimize this window"
  winMaximize?:    boolean  // "maximize this window"
  winClose?:       boolean  // "close this window"
  winSwitch?:      string   // "switch to Chrome"
  createReminder?: string   // "remind me to X in Y minutes" → natural language
  readGmail?:      boolean  // "read my emails" / "check inbox"
  readCalendar?:   boolean  // "what's on my calendar"
  createFolder?:   { name?: string; path?: string; subfolders?: string[] }  // multi-turn folder creation
  openLastFolder?: boolean                           // "open it" → last created folder
  desktopHotkey?:  string   // "press ctrl+c", "paste", "undo" → keyboard shortcut
  desktopScroll?:  { direction: 'up' | 'down'; amount: number }  // "scroll down 3 times"
  runWorkflow?:    string   // workflow trigger text → automation_workflow_service
  pendingConfirm?: {        // high-risk action awaiting yes/no voice confirmation
    tool:   string
    params: Record<string, unknown>
    prompt: string
  }
  workMode?:  boolean  // "time to work" → open VS Code + GitHub
  chillMode?: boolean  // "chill time" / "let's chill" → open YouTube + chill personality
}

const URL_MAP: Record<string, string> = {
  youtube: 'https://youtube.com', gmail:    'https://mail.google.com',
  google:  'https://google.com',  github:   'https://github.com',
  twitter: 'https://twitter.com', linkedin: 'https://linkedin.com',
  netflix: 'https://netflix.com', spotify:  'https://open.spotify.com',
  reddit:  'https://reddit.com',  amazon:   'https://amazon.com',
}

// Maps spoken app names → backend _APP_MAP keys
const SYSTEM_APP_NAMES: Record<string, string> = {
  'settings':            'settings',
  'setting':             'settings',
  'windows settings':    'settings',
  'system settings':     'settings',
  'system preferences':  'settings',
  'vs code':             'vscode',
  'vscode':              'vscode',
  'visual studio code':  'vscode',
  'virtual studio code': 'vscode',
  'virtual studio':      'vscode',
  'vs studio':           'vscode',
  'code':                'code',
  'chrome':              'chrome',
  'google chrome':       'chrome',
  'firefox':             'firefox',
  'spotify':             'spotify',
  'notepad':             'notepad',
  'calculator':          'calculator',
  'calc':                'calculator',
  'file explorer':       'explorer',
  'explorer':            'explorer',
  'files':               'explorer',
  'terminal':            'terminal',
  'windows terminal':    'terminal',
  'cmd':                 'cmd',
  'command prompt':      'cmd',
  'powershell':          'powershell',
  'power shell':         'powershell',
  'word':                'word',
  'microsoft word':      'word',
  'excel':               'excel',
  'microsoft excel':     'excel',
  'discord':             'discord',
  'zoom':                'zoom',
  'teams':               'teams',
  'microsoft teams':     'teams',
  'slack':               'slack',
  'paint':               'paint',
  'task manager':        'taskmanager',
  'taskmanager':         'taskmanager',
  // Windows Settings sub-pages
  'display settings':    'displaysettings',
  'screen settings':     'displaysettings',
  'screen resolution':   'displaysettings',
  'brightness settings': 'displaysettings',
  'sound settings':      'soundsettings',
  'audio settings':      'soundsettings',
  'speaker settings':    'soundsettings',
  'volume settings':     'soundsettings',
  'microphone settings': 'soundsettings',
  'bluetooth settings':  'bluetoothsettings',
  'wifi settings':       'networksettings',
  'network settings':    'networksettings',
  'internet settings':   'networksettings',
  'wallpaper settings':  'wallpapersettings',
  'background settings': 'wallpapersettings',
  'change wallpaper':    'wallpapersettings',
  'privacy settings':    'privacysettings',
  'windows update':      'updatesettings',
  'update settings':     'updatesettings',
  'apps settings':       'appssettings',
  'installed apps':      'appssettings',
}

function _cleanYtQuery(raw: string): string {
  return raw.replace(/\b(on youtube|for me|please|right now|anything like that)\b/gi, '').replace(/\s{2,}/g, ' ').trim()
}

function _extractTopicKw(raw: string): string {
  return raw
    .replace(/[,.]?\s*(is\s+it\s+(true|right|correct)|can\s+you\s+(confirm|tell\s+me)|right\?|is\s+that\s+right)\s*\??/gi, '')
    .replace(/^(the|a|an|some|any|about|regarding|on|for)\s+/gi, '')
    .replace(/\b(what('?s|is)|where|when|how|who|why|i\s+heard|they\s+say|i\s+think|is\s+it|was\s+it)\b.*/gi, '')
    .replace(/[,?!;]+/g, ' ').replace(/\s{2,}/g, ' ').trim()
}

function _validName(s: string | undefined, minLen = 2): boolean {
  if (!s) return false
  const c = s.trim()
  return c.length >= minLen && !/^(a|an|the|it|that|this|one|folder|file|thing|some|any)$/i.test(c)
}

export function resolveLocation(raw: string): string {
  const r = raw.toLowerCase().trim()
  if (/^[a-e]\s*(?:drive|disk|:)?$/.test(r)) return r[0].toUpperCase() + ':\\'
  if (/^desktop$/.test(r)) return 'Desktop'
  if (/^documents?$/.test(r)) return 'Documents'
  if (/^downloads?$/.test(r)) return 'Downloads'
  if (/^pictures?$/.test(r)) return 'Pictures'
  if (/^music$/.test(r)) return 'Music'
  return raw.trim()
}

export function parseEntities(text: string): ParsedEntities {
  const t = text.toLowerCase().trim()
    .replace(/[.!?,;:]+$/, '')
    .replace(/^(?:please|can you|could you|just|go ahead and)\s+/i, '')

  // Name: "name it X", "named X", "called X" — stop at location prepositions
  const nameM =
    t.match(/\bname\s+it\s+(.+?)(?:\s+(?:and|in|on|at|inside)\s+|\s*$)/i)
    ?? t.match(/\b(?:named?|called?)\s+(.+?)(?:\s+(?:and|in|on|at|inside)\s+|\s*$)/i)
  const rawName = nameM?.[1]?.replace(/\b(?:and|with)\b.*/i, '').trim()
  const name = _validName(rawName) ? rawName : undefined

  // Path: "in E drive", "on desktop", "in documents"
  const pathM =
    t.match(/\b(?:in|on|inside|at)\s+(?:the\s+)?([a-e]\s*(?:drive|disk|:)?)\b/i)
    ?? t.match(/\b(?:in|on|inside|at)\s+(?:the\s+)?(desktop|documents?|downloads?|pictures?|music)\b/i)
  const path = pathM ? resolveLocation(pathM[1]) : undefined

  // App: "open X", "launch X", "start X"
  const appM = t.match(/\b(?:open|launch|start|run)\s+(?:the\s+)?(\w[\w\s]{1,30}?)(?:\s+(?:app|application|program))?\s*$/i)
  const app = appM?.[1]?.trim().toLowerCase()

  // Query: "search for X", "find X", "look up X"
  const queryM = t.match(/\b(?:search(?:\s+for)?|find|look\s+up)\s+(.+)/i)
  const query = queryM?.[1]?.trim()

  return { name, path, app, query }
}

export function detectSystemAction(text: string): SystemAction | null {
  // Strip noise words + trailing punctuation before all matching
  const t = text.toLowerCase().trim()
    .replace(/[.!?,;:]+$/, '')
    .replace(/^(?:hey\s+)?(?:xyron|assistant|computer)\s*[,:]?\s*/i, '')
    .replace(/^(?:please|can you|could you|would you|i want you to|go ahead and|just)\s+/i, '')
    .replace(/\s+(?:please|for me|now|right now|quickly)\s*$/i, '')

  // ── "Remember that X" — store fact in backend memory ─────────────────────
  const rememberM = t.match(/^(?:please\s+)?(?:remember|note|save|keep\s+in\s+mind)\s+(?:that\s+|this:\s*)?(.{3,200})/i)
  if (rememberM) {
    const fact = rememberM[1].trim()
    return { response: "Got it — I'll remember that.", rememberFact: fact }
  }

  // ── "What do you remember / know about me?" ──────────────────────────────
  if (/\b(?:what(?:\s+do)?\s+you\s+(?:remember|know|recall|have\s+saved)|what(?:'?s|\s+is)\s+(?:saved|stored|in\s+your\s+memory)|what\s+(?:do\s+)?you\s+know\s+about\s+me|(?:tell\s+me\s+)?what\s+you\s+(?:remember|know|have\s+on\s+me)|(?:do\s+you\s+)?(?:remember|recall)\s+(?:me|my\s+name|who\s+i\s+am))\b/i.test(t)) {
    return { response: "Let me check what I have on you…", queryMemory: true }
  }

  // ── "Do it again" / "repeat" — resolved via lastActionRef in the hook ────
  if (/^(?:do\s+it\s+again|repeat\s+(?:that|last|it)|again|same\s+(?:thing|action)|one\s+more\s+time)$/i.test(t))
    return { response: 'Repeating that.', intent: 'repeat_last' }

  // ── "Time to work" wake flow ─────────────────────────────────────────────
  if (/\b(?:time\s+to\s+work|it'?s?\s+work\s+time|wake\s+up.{0,15}work|work\s+time|let'?s?\s+(?:get\s+to\s+work|work|grind|build|code|go)|fire\s+up|ready\s+to\s+(?:work|code|build|grind)|hey\s+buddy.{0,20}work|start\s+(?:work|coding|building)|work\s+mode|code\s+time|coding\s+time)\b/i.test(t)) {
    const greetings = [
      "All set! I've opened VS Code and your GitHub. What are you working on today? I can help you plan it out.",
      "Done — VS Code and GitHub are open. What project are we focusing on? Need a structure or a quick game plan?",
      "Work mode on. VS Code is up, GitHub is open. What are we building today — any blockers I should know about?",
      "All good, VS Code and GitHub are ready. What's on the agenda? I can suggest next steps or help you break it down.",
      "We're live. VS Code and your GitHub profile are open. What project are you jumping into? Need suggestions on where to start?",
      "All set. I've launched VS Code and GitHub for you. What are we shipping today — and do you want me to help structure the work?",
      "Ready to roll. VS Code and GitHub are open. Tell me what you're building and I'll help you hit the ground running.",
    ]
    return {
      response: greetings[Math.floor(Math.random() * greetings.length)],
      intent: 'work_mode',
      workMode: true,
    }
  }

  // ── Chill mode ───────────────────────────────────────────────────────────
  if (/\b(?:chill\s+(?:mode|time|out)|let'?s?\s+chill|relax\s+(?:mode|time)|vibe\s+(?:mode|time)|kick\s+back|chilling\s+time|chill\s+vibes?|i\s+want\s+to\s+chill|time\s+to\s+chill|chill\s+for\s+a\s+bit|i'?m\s+tired|wanna\s+chill|feeling\s+(?:tired|lazy|bored))\b/i.test(t)) {
    const responses = [
      "I've opened YouTube and Netflix for you! On YouTube, what do you wanna watch — trending videos, music, lo-fi, something funny? And on Netflix, got any movie or series in mind, or want me to recommend something?",
      "YouTube and Netflix are both open! Tell me what you're feeling on YouTube — trending music, rap, lo-fi, or anything else. And for Netflix, want a movie, a series, or should I suggest a few good ones?",
      "Done! YouTube and Netflix are ready for you. On YouTube I can play trending songs, lo-fi, music videos, whatever you like. On Netflix, just say the genre or mood and I'll recommend something great.",
      "I've got YouTube and Netflix open for you! Want me to play some trending music or videos on YouTube? And for Netflix — any genre in mind, or want me to pick a good movie or series for tonight?",
    ]
    return {
      response: responses[Math.floor(Math.random() * responses.length)],
      intent: 'chill_mode',
      chillMode: true,
    }
  }

  // System info / specs
  if (/\b(?:what(?:'?s| is)(?: my)? (?:pc|computer|system|machine|hardware|specs?)|my (?:pc|computer|system) (?:specs?|info(?:rmation)?|details?)|system (?:specs?|info(?:rmation)?|details?)|(?:tell|show)(?: me)?(?: my)? (?:pc|computer|system|hardware) (?:specs?|info(?:rmation)?|details?)|what (?:os|windows version|cpu|processor|ram|memory|storage)|how much (?:ram|memory|storage))\b/i.test(t))
    return { response: "Sure, give me just a second while I check your system.", getSystemInfo: true }

  // ── PRIORITY: Drive / app / settings open — BEFORE YouTube, news, search ─
  // Ensures "open settings", "open chrome", "open E drive" never fall through.
  const _driveM =
    t.match(/\b(?:open|show|go\s+to|navigate\s+to|bring\s+up)\s+(?:the\s+)?([a-z])\s*(?:drive|disk|:)/i)
    ?? t.match(/(?:can\s+you|could\s+you)\s+(?:open|show)\s+(?:the\s+)?([a-z])\s*(?:drive|disk|:)/i)
  if (_driveM) {
    const letter = _driveM[1].toUpperCase()
    return { response: `Opening ${letter} drive…`, path: `${letter}:\\` }
  }
  const _openM =
    t.match(/\b(?:open|launch|start|run|pull\s+up|bring\s+up)\s+(?:(?:up|out)\s+)?(?:the\s+)?([^.!?;]+)/i)
    ?? t.match(/(?:can\s+you|could\s+you|would\s+you|i\s+want\s+(?:you\s+)?to)\s+(?:open|launch|start)\s+(?:the\s+)?([^.!?;]+)/i)
    ?? t.match(/\b(?:go\s+to|navigate\s+to|take\s+me\s+to|show\s+me)\s+([^.!?;]+)/i)
  if (_openM) {
    const target = _openM[1].trim().toLowerCase()
      .replace(/^(?:up|out|the|a|an)\s+/, '')
      .replace(/\s*(?:for me|now|app|application)\s*$/i, '')
      .replace(/[.!?,;:]+$/, '')
      .trim()
    // System app names checked FIRST (settings, chrome, vscode, etc.)
    const appKey = SYSTEM_APP_NAMES[target]
    if (appKey) return { response: `Opening ${target}…`, app: appKey }
    // Then known URLs
    const url = URL_MAP[target]
    if (url) return { response: `Opening ${target}…`, url }
    // Raw URL
    if (/^https?:\/\//i.test(target)) return { response: `Opening that link…`, url: target }
  }

  // Play on YouTube
  const playM = t.match(/\bplay\s+(.{2,80}?)(?:\s+on\s+(?:youtube|it))?\s*$/)
  if (playM) {
    const raw = playM[1].replace(/^(me\s+|us\s+|some\s+|any\s+|a\s+|random\s+)+/i, '')
    const q = _cleanYtQuery(raw)
    if (/^(music|song|songs|video|videos|something|anything)$/.test(q))
      return { response: 'Sure, finding some music for you.', youtubeQuery: 'trending music today' }
    if (q && q.length > 1 && !/(open|search|find|go\s+to|launch|news|google)/i.test(q))
      return { response: 'On it, one sec!', youtubeQuery: q }
  }

  // General news
  const isGeneralNews =
    /\b(latest|today'?s?|current|breaking|trending|recent)\s+(news|headlines?|updates?)\b/i.test(t)
    || /\bwhat'?s\s+(happening|going\s+on)\b/i.test(t)
    || /\btell\s+me\s+(the\s+)?(latest|today'?s?)\s+news\b/i.test(t)
  if (isGeneralNews) {
    const topicM = t.match(/\bnews\s+(?:about|on|regarding)\s+(.+)/i)
    return {
      response: "On it — pulling up the latest news, one moment.",
      newsQuery: topicM ? topicM[1].trim() : 'latest news today',
      newsQueryTopic: false,
    }
  }

  // Topic news
  const topicNews = t.match(/\b(?:tell\s+me\s+(?:more\s+)?about|what(?:'?s|\s+is)\s+(?:the\s+)?(?:latest\s+on|update\s+on|situation\s+with|happening\s+with)|(?:any\s+)?update(?:s)?\s+on|news\s+(?:on|about|regarding)|more\s+(?:on|about)|did\s+\w+|is\s+it\s+true|i\s+heard)\s+(.{3,120})/i)
  if (topicNews) {
    const topic = _extractTopicKw(topicNews[topicNews.length - 1].trim())
    if (topic && topic.length > 2 && !/(play|open|launch|youtube|google search)/i.test(topic))
      return { response: 'Checking the latest news on that…', newsQuery: topic, newsQueryTopic: true }
  }

  // Price / crypto / stock
  const priceM =
    t.match(/\b(?:price|cost|value|worth|rate|exchange\s+rate)\s+(?:of\s+)?(.+)/i)
    ?? t.match(/\b(?:how\s+much\s+(?:is|does|are|cost))\s+(.+)/i)
    ?? t.match(/\b(.+?)\s+(?:price|stock\s+price|share\s+price|market\s+cap|rate)\b/i)
  if (priceM) {
    const asset = priceM[1].replace(/\??$/, '').trim()
    if (asset && asset.length > 1 && !/(play|youtube|open|launch)/i.test(asset))
      return { response: `Give me a sec, I'll grab the latest ${asset} price.`, priceQuery: asset }
  }

  // YouTube search
  const ytSearch =
    t.match(/(?:search(?:\s+on)?\s+youtube|youtube\s+search)\s+(?:for\s+)?(.+)/i)
    ?? t.match(/(?:look up|find)\s+(.+?)\s+on\s+youtube/i)
  if (ytSearch) {
    const q = ytSearch[1].trim()
    return { response: `Searching YouTube for "${q}"`, url: `https://youtube.com/search?q=${encodeURIComponent(q)}` }
  }

  // Google search — EXPLICIT search intent ONLY.
  // "what is X", "how do I", "who is X" no longer auto-open Google;
  // those fall through to the backend GPT for a spoken answer instead.
  const gSearch =
    t.match(/(?:search\s+(?:google|web|online|the\s+web|for)|google\s+search)\s+(?:for\s+)?(.+)/i)
    ?? t.match(/^(?:google|search)\s+(.+)/i)
    ?? t.match(/\b(?:look\s+up|find\s+(?:info(?:rmation)?\s+(?:on|about)|out\s+about)?|search\s+for)\s+(.+)/i)
  if (gSearch) {
    const q = gSearch[1].replace(/\??$/, '').trim()
    if (q && q.length > 1 && !/(play|youtube|open\s+\w+|launch|news)/i.test(q))
      return { response: `Searching Google for "${q}"`, url: `https://google.com/search?q=${encodeURIComponent(q)}` }
  }

  // ── Wikipedia quick facts ─────────────────────────────────────────────────
  // "what is X", "who is Y", "tell me about Z", "define X"
  // Guard: skip conversational / personal / dynamic queries
  const wikiExclude = /\b(?:weather|news|today|latest|current|price|stock|rate|bitcoin|crypto|my\s+name|your\s+name|you|doing|feel|think|want|time|date)\b/i
  const wikiM =
    t.match(/^what(?:'?s|\s+is)\s+(?:a\s+|an\s+|the\s+)?(.{3,60})\??$/i)
    ?? t.match(/^who(?:'?s|\s+is)\s+(.{3,60})\??$/i)
    ?? t.match(/^tell\s+me\s+about\s+(?:the\s+)?(.{3,60})\??$/i)
    ?? t.match(/^(?:define|explain)\s+(.{3,60})\??$/i)
  if (wikiM && !wikiExclude.test(t)) {
    const topic = wikiM[1].trim().replace(/[?!.,;]+$/, '').trim()
    if (topic.length > 1)
      return { response: `Looking that up for you…`, wikiTopic: topic }
  }

  // ── Clipboard ────────────────────────────────────────────────────────────
  if (/\b(?:what(?:'?s|\s+is)\s+(?:on\s+)?(?:my\s+)?clipboard|read\s+(?:my\s+)?clipboard|show\s+clipboard)\b/i.test(t))
    return { response: "Let me check your clipboard…", readClipboard: true }

  const clipWriteM = t.match(/^copy\s+(?:this\s+)?(?:to\s+(?:my\s+)?clipboard\s*:?\s*)?['""]?(.{3,200}?)['""]?\s*(?:to\s+clipboard)?$/i)
  if (clipWriteM)
    return { response: "Copied to clipboard.", writeClipboard: clipWriteM[1].trim() }

  // ── Screen reading ────────────────────────────────────────────────────────
  if (/\b(?:what(?:'?s|\s+is)\s+on\s+(?:my\s+)?screen|read\s+(?:my\s+)?screen|describe\s+(?:my\s+)?screen|take\s+a\s+screenshot|screenshot)\b/i.test(t))
    return { response: "Taking a screenshot and reading it for you…", readScreen: true }

  // ── Typing automation ─────────────────────────────────────────────────────
  const typeM = t.match(/^(?:type|write|type\s+this(?:\s+for\s+me)?|write\s+this)\s*[:\-]?\s*['""]?(.{2,300}?)['""]?\s*$/i)
  if (typeM)
    return { response: "Typing that for you now.", typeText: typeM[1].trim() }

  // ── "Open it" / open last created folder ─────────────────────────────────
  if (
    /\b(?:open|show)\s+(?:the\s+)?folder\s+(?:you\s+)?(?:just\s+)?created\b/i.test(t)
    || /\b(?:open|show)\s+(?:this|that)\s+folder\b/i.test(t)
    || /\b(?:open|show)\s+it\b/i.test(t)
  ) return { response: 'Opening it now.', openLastFolder: true }

  // ── Folder creation ────────────────────────────────────────────────────────
  if (/\b(?:create|make|add|new)\s+(?:a\s+)?(?:new\s+)?folder\b/i.test(t)) {
    const ents = parseEntities(t)
    const { name } = ents
    const path = ents.path ?? (name ? 'Desktop' : undefined)

    // Extract subfolder names from patterns like:
    // "with subfolders Frontend, Backend, and Database"
    // "with folders called X, Y, Z"
    // "inside it create X, Y, Z"
    let subfolders: string[] | undefined
    const subM = t.match(/\bwith\s+(?:sub)?folders?\s+(?:called\s+|named\s+)?(.+)/i)
      ?? t.match(/\binside\s+(?:it\s+)?(?:create|make|add)\s+(.+)/i)
      ?? t.match(/\bcontaining\s+(?:folders?\s+)?(.+)/i)
    if (subM) {
      subfolders = subM[1]
        .split(/,\s*|\s+and\s+/)
        .map(s => s.replace(/\band\b/i, '').replace(/[.!?]+$/, '').trim())
        .filter(s => s.length > 0 && s.length < 60)
      if (subfolders.length === 0) subfolders = undefined
    }

    const subNote = subfolders ? ` with ${subfolders.length} subfolder${subfolders.length !== 1 ? 's' : ''}` : ''
    return {
      intent: 'create_folder',
      entities: { name, path },
      response: name
        ? (ents.path ? `Sure, creating the folder${subNote} now.` : `Got it — creating "${name}"${subNote} on your Desktop.`)
        : 'What should I name it, and where should I create it?',
      createFolder: { name, path, subfolders },
    }
  }

  // ── Workflow triggers (delegated to backend automation service) ───────────
  if (
    /\bplay\s+.+\s+on\s+youtube\b/i.test(t) ||
    /\bsend\s+(?:an?\s+)?email\s+to\b/i.test(t) ||
    /\bdirections?\s+to\b/i.test(t) ||
    /\bsend\s+whatsapp\s+(?:message\s+)?to\b/i.test(t) ||
    /\bsearch\s+github\s+for\b/i.test(t)
  ) return { response: "On it!", runWorkflow: t }

  // ── Keyboard hotkeys ──────────────────────────────────────────────────────
  const _HOTKEY_PHRASES: Record<string, string> = {
    copy: 'ctrl+c', paste: 'ctrl+v', cut: 'ctrl+x', undo: 'ctrl+z', redo: 'ctrl+y',
    save: 'ctrl+s', 'select all': 'ctrl+a', find: 'ctrl+f', 'new tab': 'ctrl+t',
    'close tab': 'ctrl+w', refresh: 'ctrl+r', 'go back': 'alt+left', 'go forward': 'alt+right',
  }
  const hotkeyM = t.match(/^(?:press|hit|use|do|execute)?\s*(?:keyboard\s+)?(?:shortcut\s+)?(.{2,30?})\s*(?:shortcut|hotkey|key(?:s|board)?)?$/i)
  if (hotkeyM) {
    const phrase = hotkeyM[1].trim().toLowerCase()
    const mapped = _HOTKEY_PHRASES[phrase]
    if (mapped) return { response: `Pressing ${phrase}.`, desktopHotkey: mapped }
    if (/^(?:ctrl|alt|shift|win|cmd)\+/.test(phrase))
      return { response: `Pressing ${phrase}.`, desktopHotkey: phrase }
  }
  for (const [phrase, combo] of Object.entries(_HOTKEY_PHRASES)) {
    if (new RegExp(`\\b${phrase}\\b`, 'i').test(t) && /\b(?:press|hit|do|use|keyboard|shortcut)\b/i.test(t))
      return { response: `Pressing ${phrase}.`, desktopHotkey: combo }
  }

  // ── Scroll ────────────────────────────────────────────────────────────────
  const scrollM = t.match(/\bscroll\s+(up|down)(?:\s+(\d+)(?:\s+times?)?)?\b/i)
  if (scrollM)
    return {
      response: `Scrolling ${scrollM[1].toLowerCase()}.`,
      desktopScroll: { direction: scrollM[1].toLowerCase() as 'up' | 'down', amount: parseInt(scrollM[2] ?? '3') },
    }

  // ── Window control ────────────────────────────────────────────────────────
  if (/\b(?:minimize|minimise)\b/i.test(t))
    return { response: "Minimizing the window.", winMinimize: true }
  if (/\b(?:maximize|maximise|fullscreen|full\s+screen)\b/i.test(t))
    return { response: "Maximizing the window.", winMaximize: true }
  if (/\b(?:close|quit)\s+(?:this\s+)?(?:window|app|application|tab)\b/i.test(t))
    return { response: "Closing the window.", winClose: true }
  const switchM = t.match(/\b(?:switch\s+to|go\s+to|focus|bring\s+up)\s+(.{2,40})(?:\s+(?:please|now|window))?$/i)
  if (switchM && !/(open|launch|start)/i.test(switchM[1]))
    return { response: `Switching to ${switchM[1].trim()}.`, winSwitch: switchM[1].trim() }

  // ── Reminder creation ─────────────────────────────────────────────────────
  const reminderM =
    t.match(/\bremind\s+me\s+(?:to\s+|about\s+)?.+?\s+in\s+\d+\s*(?:second|minute|hour|min|sec|hr)s?\b/i)
    ?? t.match(/\bset\s+(?:a\s+)?reminder\b/i)
  if (reminderM)
    return { response: "Got it — setting your reminder.", createReminder: text }

  // ── Gmail ─────────────────────────────────────────────────────────────────
  if (/\b(?:read\s+(?:my\s+)?(?:emails?|inbox|messages?)|check\s+(?:my\s+)?(?:emails?|inbox)|any\s+(?:new\s+)?emails?|what(?:'?s|\s+is)\s+in\s+my\s+(?:inbox|emails?))\b/i.test(t))
    return { response: "Let me check your inbox…", readGmail: true }

  // ── Calendar ──────────────────────────────────────────────────────────────
  if (/\b(?:what(?:'?s|\s+is)\s+(?:on\s+)?(?:my\s+)?(?:calendar|schedule|agenda)|any\s+(?:meetings?|events?|appointments?)\s+(?:today|this\s+week)|(?:show|check)\s+(?:my\s+)?(?:calendar|schedule)|my\s+schedule)\b/i.test(t))
    return { response: "Checking your calendar…", readCalendar: true }

  return null
}

// ── Text cleaning ─────────────────────────────────────────────────────────────

export function stripMarkdown(raw: string): string {
  let t = raw
  t = t.replace(/```[\s\S]*?```/g, 'See the screen for details.')
  t = t.replace(/`[^`]+`/g, '')
  t = t.replace(/^#{1,6}\s+/gm, '')
  t = t.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
  t = t.replace(/https?:\/\/\S+/g, '')
  t = t.replace(/\*\*([^*]+)\*\*/g, '$1')
  t = t.replace(/\*([^*]+)\*/g, '$1')
  t = t.replace(/__([^_]+)__/g, '$1')
  t = t.replace(/^[-*•]\s+/gm, '')
  t = t.replace(/^\d+\.\s+/gm, '')
  t = t.replace(/\n+/g, '. ')
  return t.replace(/\s{2,}/g, ' ').trim()
}

export function cleanForSpeech(raw: string): string {
  let t = stripMarkdown(raw)
  if (t.length > 300) {
    const cut = t.lastIndexOf('.', 300)
    t = cut > 80 ? t.slice(0, cut + 1) : t.slice(0, 300)
  }
  return t.trim()
}

// ── Browser TTS fallback ──────────────────────────────────────────────────────

let _cachedVoice: SpeechSynthesisVoice | null | undefined = undefined

function _getBestVoice(): SpeechSynthesisVoice | null {
  if (typeof window === 'undefined' || !window.speechSynthesis) return null
  const voices = window.speechSynthesis.getVoices()
  if (!voices.length) return null
  const checks: Array<(v: SpeechSynthesisVoice) => boolean> = [
    (v) => /google/i.test(v.name) && v.lang.startsWith('en-US') && !v.localService,
    (v) => /samantha/i.test(v.name) && v.lang.startsWith('en'),
    (v) => /zira|david|mark/i.test(v.name) && v.lang.startsWith('en'),
    (v) => /google/i.test(v.name) && v.lang.startsWith('en'),
    (v) => v.lang.startsWith('en-US'),
    (v) => v.lang.startsWith('en'),
  ]
  for (const check of checks) { const m = voices.find(check); if (m) return m }
  return voices[0]
}

export function speakBrowser(text: string): Promise<void> {
  return new Promise((resolve) => {
    if (typeof window === 'undefined' || !window.speechSynthesis) { resolve(); return }
    window.speechSynthesis.cancel()
    const speak = () => {
      const u     = new SpeechSynthesisUtterance(text)
      const voice = _cachedVoice === undefined ? _getBestVoice() : _cachedVoice
      _cachedVoice = voice
      if (voice) u.voice = voice
      u.lang = voice?.lang ?? 'en-US'
      u.rate = 1.05; u.pitch = 1.0; u.volume = 0.90
      u.onend = () => resolve(); u.onerror = () => resolve()
      window.speechSynthesis.speak(u)
    }
    if (window.speechSynthesis.getVoices().length > 0) speak()
    else { window.speechSynthesis.addEventListener('voiceschanged', speak, { once: true }); setTimeout(speak, 300) }
  })
}

// ── API base URL ──────────────────────────────────────────────────────────────

export function getApiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000'
  return process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
}

// ── AudioQueue ────────────────────────────────────────────────────────────────
//
// Sequential audio playback with eager TTS fetch. Key design:
//   1. Caller enqueues (index, Promise<blobUrl>) as SSE chunks arrive.
//   2. Queue plays items in index order, waiting for each TTS fetch.
//   3. onEmpty fires ONLY after markDone() AND all queued items are played.
//      This prevents premature loop restart while SSE is still streaming.

export class AudioQueue {
  private q:       Array<{ idx: number; p: Promise<string> }> = []
  private playing  = false
  private aborted  = false
  private done     = false   // markDone() called (SSE stream complete)
  private cur:     HTMLAudioElement | null = null
  private curUrl:  string | null          = null
  private _onEmpty?: () => void
  private vol:     number

  constructor(opts: { onEmpty?: () => void; volume?: number } = {}) {
    this._onEmpty = opts.onEmpty
    this.vol      = opts.volume ?? 0.9
  }

  /** Enqueue chunk. urlPromise resolves to object URL or '' (skip). */
  enqueue(idx: number, urlPromise: Promise<string>): void {
    if (this.aborted) return
    let i = this.q.length
    while (i > 0 && this.q[i - 1].idx > idx) i--
    this.q.splice(i, 0, { idx, p: urlPromise })
    if (!this.playing) this._next()
  }

  /**
   * Signal that the SSE stream has ended (all chunks enqueued).
   * onEmpty will fire after this AND after all audio finishes.
   *
   * Guard: only the first call has effect. streamAndSpeak calls markDone()
   * in BOTH the 'done' event handler AND the finally block — the second call
   * must be a no-op to prevent double-scheduling of onEmpty.
   */
  markDone(): void {
    if (this.aborted) return
    if (this.done) return           // ← guard: already marked done, ignore second call
    this.done = true
    // If no items are playing / queued right now, fire immediately
    if (!this.playing && !this.q.length) {
      // Small tick so callers can finish their own cleanup first
      setTimeout(() => { if (!this.aborted) this._onEmpty?.() }, 0)
    }
  }

  abort(): void {
    this.aborted = true
    this.q       = []
    if (this.cur)    { this.cur.pause(); this.cur.src = ''; this.cur = null }
    if (this.curUrl) { URL.revokeObjectURL(this.curUrl); this.curUrl = null }
    this.playing = false
  }

  get isPlaying(): boolean { return this.playing }

  /** True if items are queued but not yet playing. */
  get hasPendingAudio(): boolean { return this.q.length > 0 }

  private async _next(): Promise<void> {
    if (this.aborted || !this.q.length) {
      this.playing = false
      // Only fire onEmpty after the stream is fully done
      if (!this.aborted && this.done) {
        console.log('[AUDIO] queue drained — firing onEmpty')
        this._onEmpty?.()
      }
      return
    }

    this.playing = true
    const chunkIdx = this.q.length   // how many left before this one
    console.log('[AUDIO] play start — chunks remaining in queue:', chunkIdx)
    const { p } = this.q.shift()!

    let url: string
    try {
      console.log('[AUDIO] next chunk ready — awaiting TTS blob')
      url = await p
    } catch {
      if (!this.aborted) this._next()
      else this.playing = false
      return
    }

    if (this.aborted || !url) {
      if (url) URL.revokeObjectURL(url)
      this.playing = false
      if (!this.aborted && this.done && !this.q.length) this._onEmpty?.()
      return
    }

    this.curUrl = url
    const a = new Audio(url)
    a.volume  = this.vol
    this.cur  = a

    await new Promise<void>((res) => {
      const done = () => {
        console.log('[AUDIO] play end — queue remaining:', this.q.length)
        URL.revokeObjectURL(url); this.curUrl = null; this.cur = null; res()
      }
      const t    = setTimeout(done, 25_000)   // safety timeout
      a.onended  = () => { clearTimeout(t); done() }
      a.onerror  = () => { clearTimeout(t); done() }
      a.play().catch(() => { clearTimeout(t); done() })
    })

    if (this.aborted) this.playing = false
    else this._next()
  }
}

// Interrupt monitor removed — caused race conditions (premature listening,
// concurrent loop instances). The voice pipeline uses a strict state machine:
// listening is triggered ONLY by explicit user action (mic click / wake word).

// ── TTS fetch helpers ─────────────────────────────────────────────────────────

export async function fetchTtsUrl(
  text:    string,
  voice:   string,
  speed:   number,
  signal:  AbortSignal,
  apiBase: string,
): Promise<string> {
  const r = await fetch(`${apiBase}/api/v1/voice/synthesize`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ text, voice, speed }),
    signal,
  })
  if (!r.ok) throw new Error(`TTS ${r.status}`)
  return URL.createObjectURL(await r.blob())
}

/** True if this browser supports MP3 in MediaSource (Chrome/Edge/Safari 17+). */
function _mseSupported(): boolean {
  try {
    return (
      typeof MediaSource !== 'undefined' &&
      typeof MediaSource.isTypeSupported === 'function' &&
      MediaSource.isTypeSupported('audio/mpeg')
    )
  } catch { return false }
}
const _USE_MSE = _mseSupported()

/**
 * Streaming TTS via MediaSource — audio starts playing as the first bytes
 * arrive from OpenAI, before the full MP3 is generated.
 *
 * Returns a blob: URL pointing to a MediaSource object.  The URL resolves as
 * soon as the first chunk of audio bytes is appended to the SourceBuffer so
 * the AudioQueue can hand it to <audio> and begin playback immediately.
 *
 * Falls back to the standard blob approach if MSE is unsupported.
 */
export function fetchTtsUrlStream(
  text:    string,
  voice:   string,
  speed:   number,
  signal:  AbortSignal,
  apiBase: string,
): Promise<string> {
  if (!_USE_MSE) return fetchTtsUrl(text, voice, speed, signal, apiBase)

  return new Promise<string>((resolve, reject) => {
    const ms  = new MediaSource()
    const url = URL.createObjectURL(ms)
    let resolved = false

    const _fail = (err: unknown) => {
      if (!resolved) { URL.revokeObjectURL(url); reject(err) }
    }

    ms.addEventListener('sourceopen', async () => {
      let sb: SourceBuffer
      try {
        sb = ms.addSourceBuffer('audio/mpeg')
      } catch (e) {
        _fail(e); return
      }

      const _append = (chunk: Uint8Array) =>
        new Promise<void>((res, rej) => {
          if (signal.aborted) { rej(new Error('aborted')); return }
          const go = () => {
            try { sb.appendBuffer(chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength) as ArrayBuffer) } catch (e) { rej(e); return }
            sb.addEventListener('updateend', () => res(), { once: true })
          }
          if (sb.updating) {
            sb.addEventListener('updateend', go, { once: true })
          } else {
            go()
          }
        })

      try {
        const r = await fetch(`${apiBase}/api/v1/voice/synthesize-stream`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ text, voice, speed }),
          signal,
        })
        if (!r.ok || !r.body) throw new Error(`TTS stream ${r.status}`)

        const reader = r.body.getReader()
        while (true) {
          const { done, value } = await reader.read()
          if (signal.aborted || done) {
            if (ms.readyState === 'open') ms.endOfStream()
            break
          }
          await _append(value)
          // Resolve as soon as the first chunk is buffered — <audio> can play now
          if (!resolved) { resolved = true; resolve(url) }
        }
      } catch (e) {
        if (ms.readyState === 'open') { try { ms.endOfStream() } catch {} }
        _fail(e)
      }
    }, { once: true })

    // Safety: if sourceopen never fires (e.g. detached document), fall back
    setTimeout(() => { if (!resolved) _fail(new Error('MSE sourceopen timeout')) }, 8_000)
  })
}

// ── SSE streaming consumer ────────────────────────────────────────────────────

export interface ConfirmPayload {
  tool:   string
  params: Record<string, unknown>
  prompt: string
}

export interface StreamCbs {
  onChunk:              (text: string, index: number) => void
  onDone:               (fullText: string, source?: string) => void
  onError:              (msg: string) => void
  onAction?:            (actionUrl: string | null, actionApp: string | null, actionPath: string | null) => void
  onConfirmRequired?:   (payload: ConfirmPayload) => void
  onFollowUp?:          (suggestion: string) => void
  onProfileSwitch?:     (profile: string, voice: string) => void
  onModeChange?:        (mode: string) => void
  onSystemConfirm?:     (action: string) => void
}

export async function streamAndSpeak(
  transcript:       string,
  history:          HistoryEntry[],
  queue:            AudioQueue,
  signal:           AbortSignal,
  voice:            string,
  speed:            number,
  apiBase:          string,
  cb:               StreamCbs,
  sessionId?:       string,
  personalityMode?: string,
): Promise<void> {
  let resp: Response
  try {
    resp = await fetch(`${apiBase}/api/v1/voice/respond-stream`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        text:             transcript,
        history,
        session_id:       sessionId ?? '',
        use_tools:        true,
        personality_mode: personalityMode ?? '',
      }),
      signal,
    })
  } catch {
    if (!signal.aborted) cb.onError('Network error.')
    return
  }

  if (!resp.ok || !resp.body) {
    cb.onError('AI stream unavailable.')
    queue.markDone()
    return
  }

  const reader = resp.body.getReader()
  const dec    = new TextDecoder()
  let carry    = ''

  // ── TTS accumulation buffer ───────────────────────────────────────────────
  // Strategy:
  //   • FIRST chunk  → fire TTS immediately, no accumulation.
  //     Uses streaming TTS (MediaSource) so audio starts playing while
  //     OpenAI is still generating bytes. First words heard in ~0.5s.
  //   • Subsequent   → batch to sentence boundary OR 100 chars.
  //     Avoids HTTP overhead from many tiny requests.
  // Display text (onChunk) still updates immediately for every SSE chunk.
  let ttsBuf = ''
  let ttsIdx = 0

  function _flushTts() {
    const text = ttsBuf.trim()
    ttsBuf = ''
    if (!text) return
    // First chunk: use streaming MediaSource endpoint for lowest latency.
    // Subsequent chunks: streaming still preferred; blob as fallback chain.
    queue.enqueue(
      ttsIdx++,
      fetchTtsUrlStream(text, voice, speed, signal, apiBase)
        .catch(() => fetchTtsUrl(text, voice, speed, signal, apiBase))
        .catch(() => ''),
    )
  }

  try {
    outer: while (true) {
      const { done, value } = await reader.read()
      if (done || signal.aborted) break
      carry += dec.decode(value, { stream: true })
      const lines = carry.split('\n')
      carry = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (!raw) continue
        let ev: {
          type: string; index?: number; text?: string
          full_text?: string; message?: string
          action_url?: string | null; action_app?: string | null; action_path?: string | null
          confirm_required?: boolean; confirm_tool?: string
          confirm_params?: Record<string, unknown>; confirm_prompt?: string
        }
        try { ev = JSON.parse(raw) } catch { continue }

        if (ev.type === 'action') {
          if (ev.confirm_required) {
            cb.onConfirmRequired?.({
              tool:   ev.confirm_tool   ?? '',
              params: ev.confirm_params ?? {},
              prompt: ev.confirm_prompt ?? 'Are you sure? Say yes to confirm or no to cancel.',
            })
          } else {
            // Tool executed server-side — handle side-effects in caller
            cb.onAction?.(ev.action_url ?? null, ev.action_app ?? null, ev.action_path ?? null)
          }
        } else if (ev.type === 'chunk' && ev.text) {  // eslint-disable-line
          const chunkTxt = ev.text!
          cb.onChunk(chunkTxt, ev.index ?? 0)   // immediate display update

          ttsBuf += (ttsBuf ? ' ' : '') + chunkTxt
          if (ttsIdx === 0) {
            // First chunk: fire TTS immediately — don't wait for accumulation.
            // Short text = fast OpenAI TTS + streaming bytes start in ~100ms.
            _flushTts()
          } else {
            // Subsequent: batch to sentence boundary or 100 chars to reduce
            // HTTP round-trips and prevent gaps between audio segments.
            const endsWithPunct = /[.!?](\s|$)/.test(ttsBuf)
            if (endsWithPunct || ttsBuf.length >= 100) _flushTts()
          }
        } else if (ev.type === 'follow_up') {
          cb.onFollowUp?.((ev as any).suggestion ?? '')
        } else if (ev.type === 'profile_switch') {
          cb.onProfileSwitch?.((ev as any).profile ?? '', (ev as any).voice ?? 'nova')
        } else if (ev.type === 'mode_change') {
          cb.onModeChange?.((ev as any).mode ?? '')
        } else if (ev.type === 'confirmation_required') {
          cb.onSystemConfirm?.((ev as any).action ?? '')
        } else if (ev.type === 'done') {
          _flushTts()                 // flush any remaining text
          cb.onDone(ev.full_text ?? '', (ev as any).source)
          queue.markDone()            // ← enables onEmpty once all audio drains
          return
        } else if (ev.type === 'error') {
          _flushTts()
          cb.onError(ev.message ?? 'Unknown error')
          queue.markDone()
          break outer
        }
      }
    }
  } finally {
    _flushTts()                       // flush anything left in buffer
    reader.cancel().catch(() => {})
    // Guarantee markDone even if stream ends without a 'done' event
    queue.markDone()
  }
}
