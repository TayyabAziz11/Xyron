import { useCallback, useRef, useState } from 'react'
import { readAssistantSettings, buildGreeting } from './useAssistantSettings'

// ── Types ─────────────────────────────────────────────────────────────────────

export type SessionState =
  | 'idle' | 'greeting' | 'listening' | 'transcribing' | 'processing' | 'speaking' | 'stopped'

export interface ConvMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  text: string
  timestamp: Date
  status: 'processing' | 'done' | 'error'
}

// ── VAD config ────────────────────────────────────────────────────────────────

const VAD = {
  calibrationFrames:   30,     // ~500ms calibration window
  thresholdMultiplier: 2.5,    // lower → easier speech detection
  minThreshold:        0.045,  // raised to reduce false-positive triggers from ambient noise
  maxThreshold:        0.110,  // lower cap → stops wrong calibration from blocking speech
  exitMultiplier:      1.0,    // exit 'speaking' when signal hits noise floor
  resumeHysteresis:    3.5,    // higher → harder for background noise to cancel silence timer
  smoothingAlpha:      0.80,   // faster decay after speech ends
  speechMinFrames:     5,      // require 5 frames (~83ms) to reduce STT false positives
  silenceAfterMs:      600,    // 600ms of silence = natural end of utterance
  noSpeechTimeoutMs:   7000,   // 7s to start speaking
  maxTotalMs:          30_000,
} as const

// ── Stop phrases ──────────────────────────────────────────────────────────────

const STOP_PHRASES = new Set([
  'stop', 'ok stop', 'stop now', 'ok stop now', 'stop listening',
  'end session', 'exit', 'exit conversation',
  'bye', 'goodbye', "that's all", 'thats all',
  'close', 'quit', 'end', 'cancel', 'never mind', 'nevermind',
])

function isStopPhrase(text: string): boolean {
  const lower = text.toLowerCase().trim()
  return (
    STOP_PHRASES.has(lower) ||
    [...STOP_PHRASES].some((p) => lower.startsWith(p + ' ') || lower.endsWith(' ' + p))
  )
}

// ── Identity / time responses ─────────────────────────────────────────────────

const IDENTITY: Array<{ pattern: RegExp; response: string | (() => string) }> = [
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
  },
  {
    pattern: /\b(who (built|created|developed|made) you|who('s| is) your (developer|creator|builder|maker))\b/i,
    response: "I was built by Tayyab Aziz — he created me as a voice-first AI assistant to help you get things done hands-free.",
  },
  {
    pattern: /\b(what('s| is) your name|who are you)\b/i,
    response: "I'm Xyron, your voice assistant. Built by Tayyab Aziz to help you manage your day. Just say what you need!",
  },
  {
    pattern: /\b(what (can|do) you do|what are your (capabilities|features|skills)|how can you help|what('?re| are) you (capable|able) of)\b/i,
    response: "Quite a lot, actually! I can open any app or setting, play YouTube, check live prices, read the news, answer questions, and handle voice commands for your whole system. Just ask.",
  },
  {
    pattern: /\b(what('?s| is)( the| today'?s?)? (date|day)|what day is (it )?today|today'?s? date|current (date|day)|tell me (the )?(date|day)|day (and|or) date)\b/i,
    response: () => {
      const now  = new Date()
      const day  = now.toLocaleDateString('en-US', { weekday: 'long' })
      const date = now.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })
      return `Today is ${day}, ${date}.`
    },
  },
  {
    pattern: /\b(what('?s| is) the time|what time is it|current time)\b/i,
    response: () => {
      const now  = new Date()
      return `It's ${now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })}.`
    },
  },
  {
    pattern: /\b(are you (real|human|alive|conscious)|do you (think|feel|have feelings)|are you smart)\b/i,
    response: "I'm an AI, but I'm pretty capable! I think clearly, learn from our conversation, and I'm always here when you need me.",
  },
  {
    pattern: /\b(thank(s| you)|cheers|appreciate it|that('?s| is) (great|awesome|helpful|perfect|nice))\b/i,
    response: () => {
      const replies = ["Happy to help!", "Anytime!", "Of course!", "That's what I'm here for!"]
      return replies[Math.floor(Math.random() * replies.length)]
    },
  },
  {
    // Catch "what are you doing/thinking/working on/up to" before gSearch fires
    pattern: /\bwhat(?:'s|\s+is|\s+are)\s+you\s+(?:doing|up\s+to|working\s+on|thinking|up\s+to\s+now|saying)\b/i,
    response: () => {
      const replies = [
        "Just waiting for your next command! What do you need?",
        "Ready and listening — what can I do for you?",
        "Standing by! What's on your mind?",
      ]
      return replies[Math.floor(Math.random() * replies.length)]
    },
  },
  {
    pattern: /\b(what'?s\s+(?:going\s+on|up|new|happening)|anything\s+new|sup)\b/i,
    response: () => {
      const replies = [
        "All good on my end! What do you need?",
        "Ready when you are. What's up?",
        "Just here and ready to help!",
      ]
      return replies[Math.floor(Math.random() * replies.length)]
    },
  },
]

function checkIdentity(text: string, mode?: string): string | null {
  for (const entry of IDENTITY) {
    if (entry.pattern.test(text)) {
      const pick = (mode === 'boss' && (entry as any).bossResponse) ? (entry as any).bossResponse : entry.response
      return typeof pick === 'function' ? pick() : pick
    }
  }
  return null
}

// ── System action detection ───────────────────────────────────────────────────

interface ParsedEntities {
  name?:   string
  path?:   string
  app?:    string
  query?:  string
  target?: string
}

interface SystemAction {
  response:      string
  intent?:       string          // e.g. "create_folder", "open_app", "open_url"
  entities?:     ParsedEntities  // structured entities extracted via parseEntities()
  url?: string
  app?: string             // backend open-app key (e.g. 'vscode', 'settings')
  path?: string            // directory/drive path (e.g. 'E:\\', 'C:\\')
  systemCommand?: string   // legacy: Windows shell command via Electron IPC
  getSystemInfo?: boolean  // query real system specs via IPC
  youtubeQuery?: string
  newsQuery?: string
  newsQueryTopic?: boolean
  priceQuery?: string
  createFolder?: { name?: string; path?: string }  // multi-turn folder creation
  openLastFolder?: boolean                          // open last created folder
  // ── Desktop automation ──
  desktopType?: string                              // text to type via SendKeys
  desktopHotkey?: string                            // key combo e.g. "ctrl+c"
  desktopScroll?: { direction: 'up' | 'down'; amount: number }
  runWorkflow?: { name: string; variables: Record<string, string> }
  workMode?: boolean  // "time to work" → open VS Code + GitHub
}

// Map of spoken app names → Windows commands (executed via cmd.exe /c on WSL2)
// Maps spoken app names → backend /api/v1/system/open-app keys
const SYSTEM_APP_NAMES: Record<string, string> = {
  // Settings
  'settings':             'settings',
  'windows settings':     'settings',
  'system settings':      'settings',
  'setting':              'settings',
  'display settings':     'displaysettings',
  'display setting':      'displaysettings',
  'screen settings':      'displaysettings',
  'bluetooth settings':   'bluetoothsettings',
  'wifi settings':        'networksettings',
  'wi-fi settings':       'networksettings',
  'network settings':     'networksettings',
  'privacy settings':     'privacysettings',
  'update settings':      'updatesettings',
  'windows update':       'updatesettings',
  'sound settings':       'soundsettings',
  'audio settings':       'soundsettings',
  // File system
  'file explorer':        'explorer',
  'explorer':             'explorer',
  'files':                'explorer',
  'my computer':          'explorer',
  'this pc':              'explorer',
  // Dev tools
  'vs code':              'vscode',
  'vscode':               'vscode',
  'visual studio code':   'vscode',
  'virtual studio code':  'vscode',
  'virtual studio':       'vscode',
  'vs studio':            'vscode',
  'code':                 'code',
  // Browsers
  'chrome':               'chrome',
  'google chrome':        'chrome',
  'firefox':              'firefox',
  'edge':                 'edge',
  'microsoft edge':       'edge',
  // Built-in apps
  'calculator':           'calculator',
  'calc':                 'calculator',
  'notepad':              'notepad',
  'task manager':         'taskmanager',
  'taskmgr':              'taskmanager',
  'paint':                'paint',
  'ms paint':             'paint',
  'terminal':             'terminal',
  'windows terminal':     'terminal',
  'cmd':                  'cmd',
  'command prompt':       'cmd',
  'powershell':           'powershell',
  // Communication
  'discord':              'discord',
  'slack':                'slack',
  'teams':                'teams',
  'microsoft teams':      'teams',
  'zoom':                 'zoom',
  // Media
  'spotify':              'spotify',
  // Office
  'word':                 'word',
  'excel':                'excel',
  'powerpoint':           'powerpoint',
}

const URL_MAP: Record<string, string> = {
  youtube: 'https://youtube.com', gmail: 'https://mail.google.com',
  google: 'https://google.com',   github: 'https://github.com',
  twitter: 'https://twitter.com', linkedin: 'https://linkedin.com',
  netflix: 'https://netflix.com', spotify: 'https://open.spotify.com',
  reddit: 'https://reddit.com',   amazon: 'https://amazon.com',
}

function extractTopicKeywords(raw: string): string {
  return raw
    .replace(/[,.]?\s*(is\s+it\s+(true|right|correct)|can\s+you\s+(confirm|tell\s+me)|right\?|is\s+that\s+right)\s*\??/gi, '')
    .replace(/^(the|a|an|some|any|about|regarding|on|for)\s+/gi, '')
    .replace(/\b(what('?s|is)|where|when|how|who|why|i\s+heard|they\s+say|i\s+think|is\s+it|was\s+it)\b.*/gi, '')
    .replace(/[,?!;]+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function cleanYoutubeQuery(raw: string): string {
  return raw
    .replace(/\b(on youtube|for me|please|right now|anything like that)\b/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function resolveLocation(raw: string): string {
  const r = raw.toLowerCase().trim()
  if (/^[a-e]\s*(?:drive|disk|:)?$/.test(r)) return r[0].toUpperCase() + ':\\'
  if (/^desktop$/.test(r)) return 'Desktop'
  if (/^documents?$/.test(r)) return 'Documents'
  if (/^downloads?$/.test(r)) return 'Downloads'
  if (/^pictures?$/.test(r)) return 'Pictures'
  if (/^music$/.test(r)) return 'Music'
  return raw.trim()
}

function parseEntities(text: string): ParsedEntities {
  const t = text.toLowerCase().trim()
    .replace(/[.!?,;:]+$/, '')
    .replace(/^(?:please|can you|could you|just|go ahead and)\s+/i, '')
  const nameM =
    t.match(/\bname\s+it\s+(.+?)(?:\s+(?:and|in|on|at|inside)\s+|\s*$)/i)
    ?? t.match(/\b(?:named?|called?)\s+(.+?)(?:\s+(?:and|in|on|at|inside)\s+|\s*$)/i)
  const rawName = nameM?.[1]?.replace(/\b(?:and|with)\b.*/i, '').trim()
  const name = _validName(rawName) ? rawName : undefined
  const pathM =
    t.match(/\b(?:in|on|inside|at)\s+(?:the\s+)?([a-e]\s*(?:drive|disk|:)?)\b/i)
    ?? t.match(/\b(?:in|on|inside|at)\s+(?:the\s+)?(desktop|documents?|downloads?|pictures?|music)\b/i)
  const path = pathM ? resolveLocation(pathM[1]) : undefined
  const appM = t.match(/\b(?:open|launch|start|run)\s+(?:the\s+)?(\w[\w\s]{1,30}?)(?:\s+(?:app|application|program))?\s*$/i)
  const app = appM?.[1]?.trim().toLowerCase()
  const queryM = t.match(/\b(?:search(?:\s+for)?|find|look\s+up)\s+(.+)/i)
  const query = queryM?.[1]?.trim()
  return { name, path, app, query }
}

function _failsafe(intent: string, result: { success?: boolean; spoken?: string } | null, fallback: string): string {
  if (!result || !result.success) {
    console.warn('[XYRON] Tool execution failed:', intent, result)
    return result?.spoken || fallback
  }
  return result.spoken || fallback
}

function _validName(s: string | undefined, minLen = 2): boolean {
  if (!s) return false
  const c = s.trim()
  return c.length >= minLen && !/^(a|an|the|it|that|this|one|folder|file|thing|some|any)$/i.test(c)
}

function detectSystemAction(text: string): SystemAction | null {
  // Strip noise words and trailing punctuation before any matching
  const t = text.toLowerCase().trim()
    .replace(/[.!?,;:]+$/, '')
    .replace(/^(?:hey\s+)?(?:xyron|assistant|computer)\s*[,:]?\s*/i, '')
    .replace(/^(?:please|can you|could you|would you|i want you to|go ahead and|just)\s+/i, '')
    .replace(/\s+(?:please|for me|now|right now|quickly)\s*$/i, '')

  // SYSTEM SPECS — check before anything else so it never falls through to GPT
  const isSpecsQuery =
    /\b(what('?s| is)( my)?|tell me( about)?( my)?|show me( my)?|check( my)?)\s+(system\s+)?(specs?|specifications?|info(rmation)?|hardware|config(uration)?)\b/i.test(t)
    || /\b(my\s+)?(pc|computer|laptop|machine)\s+(specs?|info|specifications?|hardware)\b/i.test(t)
    || /\bwhat (pc|computer|laptop|hardware) (do|am) i (have|running|using)\b/i.test(t)
    || /\bwhat('?s| is) my (cpu|processor|ram|memory|os|operating system|windows(\s+version)?|gpu|graphics card)\b/i.test(t)
    || /\bwhat version of windows (am i|do i have|is (this|installed|running))\b/i.test(t)
    || /\bhow much (ram|memory) (do i have|is installed)\b/i.test(t)
  if (isSpecsQuery) {
    return { response: "Sure, give me just a second while I check your system.", getSystemInfo: true }
  }

  // ── "Do it again" / "repeat" — resolved via lastActionRef in the hook ────
  if (/^(?:do\s+it\s+again|repeat\s+(?:that|last|it)|again|same\s+(?:thing|action)|one\s+more\s+time)$/i.test(t))
    return { response: 'Repeating that.', intent: 'repeat_last' }

  // ── "Time to work" wake flow ─────────────────────────────────────────────
  if (/\b(?:time\s+to\s+work|it'?s?\s+work\s+time|wake\s+up.{0,15}work|work\s+time|let'?s?\s+(?:get\s+to\s+work|work|grind|build|code|go)|fire\s+up|ready\s+to\s+(?:work|code|build|grind)|hey\s+buddy.{0,20}work|start\s+(?:work|coding|building)|work\s+mode|code\s+time|coding\s+time)\b/i.test(t)) {
    const greetings = [
      "Alright, I'm up. VS Code and your GitHub are ready. Let's get to work.",
      "Rise and grind. Opening VS Code and GitHub — you've got code to ship.",
      "Let's go. VS Code is up, GitHub is open. Build something great today.",
      "Work mode activated. VS Code and GitHub, ready to go. Let's build.",
      "On it. VS Code and your GitHub profile are open. Time to make things happen.",
      "All set. Tell me what we're building today and I'll help you structure it.",
      "Everything's set. What project are we focusing on? I can help you plan the next steps.",
    ]
    return {
      response: greetings[Math.floor(Math.random() * greetings.length)],
      intent: 'work_mode',
      workMode: true,
    }
  }

  // ── PRIORITY: App / settings / URL open — must resolve BEFORE search/news/YouTube ──
  // so "open settings" never falls through to Google search or news routing.
  const openMatch =
    t.match(/\b(?:open|launch|start|run|pull\s+up|bring\s+up)\s+(?:(?:up|out)\s+)?(?:the\s+)?([^.!?;]+)/i)
    ?? t.match(/(?:can you|please|could you|would you|i want you to)\s+(?:open|launch|start)\s+(?:the\s+)?([^.!?;]+)/i)
  if (openMatch) {
    const raw = openMatch[1].trim()
    const target = raw.toLowerCase()
      .replace(/\s*(?:for me|please|now|app|application)\s*$/i, '')
      .replace(/[.!?,;:]+$/, '')
      .trim()
    const appKey =
      SYSTEM_APP_NAMES[target]
      ?? SYSTEM_APP_NAMES[target + 's']
      ?? SYSTEM_APP_NAMES[target.replace(/s$/, '')]
    if (appKey) return { response: `Opening ${raw}…`, app: appKey, intent: 'open_app' }
    const driveM = target.match(/^(?:the\s+)?([a-z])\s*(?:drive|disk|:)?$/)
    if (driveM) {
      const letter = driveM[1].toUpperCase()
      return { response: `Opening ${letter} drive…`, path: `${letter}:\\`, intent: 'open_directory' }
    }
    const url = URL_MAP[target]
    if (url) return { response: `Opening ${target}…`, url }
  }

  const playMatch = t.match(/\bplay\s+(.{2,80}?)(?:\s+on\s+(?:youtube|it))?\s*$/)
  if (playMatch) {
    const raw = playMatch[1].replace(/^(me\s+|us\s+|some\s+|any\s+|a\s+|random\s+)+/i, '')
    const q = cleanYoutubeQuery(raw)
    if (/^(music|song|songs|video|videos|something|anything)$/.test(q)) {
      return { response: 'Sure, finding some music for you.', youtubeQuery: 'trending music today' }
    }
    if (q && q.length > 1 && !/(open|search|find|go\s+to|launch|news|google)/i.test(q)) {
      return { response: 'On it, one sec!', youtubeQuery: q }
    }
  }

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

  const topicNews = t.match(/\b(?:tell\s+me\s+(?:more\s+)?about|what(?:'?s|\s+is)\s+(?:the\s+)?(?:latest\s+on|update\s+on|situation\s+with|happening\s+with)|(?:any\s+)?update(?:s)?\s+on|news\s+(?:on|about|regarding)|more\s+(?:on|about)|did\s+\w+|is\s+it\s+true|i\s+heard)\s+(.{3,120})/i)
  if (topicNews) {
    const topic = extractTopicKeywords(topicNews[topicNews.length - 1].trim())
    if (topic && topic.length > 2 && !/(play|open|launch|youtube|google search)/i.test(topic)) {
      return { response: 'Checking the latest news on that…', newsQuery: topic, newsQueryTopic: true }
    }
  }

  const priceMatch =
    t.match(/\b(?:price|cost|value|worth|rate|exchange\s+rate)\s+(?:of\s+)?(.+)/i)
    ?? t.match(/\b(?:how\s+much\s+(?:is|does|are|cost))\s+(.+)/i)
    ?? t.match(/\b(.+?)\s+(?:price|stock\s+price|share\s+price|market\s+cap|rate)\b/i)
  if (priceMatch) {
    const asset = priceMatch[1].replace(/\??$/, '').trim()
    if (asset && asset.length > 1 && !/(play|youtube|open|launch)/i.test(asset)) {
      return { response: `Give me a sec, I'll grab the latest ${asset} price.`, priceQuery: asset }
    }
  }

  const ytSearch =
    t.match(/(?:search(?:\s+on)?\s+youtube|youtube\s+search)\s+(?:for\s+)?(.+)/i)
    ?? t.match(/(?:look up|find)\s+(.+?)\s+on\s+youtube/i)
  if (ytSearch) {
    const q = ytSearch[1].trim()
    return { response: `Searching YouTube for "${q}"`, url: `https://youtube.com/search?q=${encodeURIComponent(q)}` }
  }

  const gSearch =
    t.match(/(?:search\s+(?:google|web|online|the\s+web|for)|google\s+search)\s+(?:for\s+)?(.+)/i)
    ?? t.match(/^(?:google|search)\s+(.+)/i)
    ?? t.match(/\b(?:look\s+up|find\s+(?:info(?:rmation)?\s+(?:on|about)|out\s+about)?|search\s+for)\s+(.+)/i)
    ?? t.match(/\b(?:what\s+is|what\s+are|who\s+is|how\s+(?:do\s+(?:i|you)|to|does)|why\s+is|where\s+is)\s+(.+)/i)
  if (gSearch) {
    const q = gSearch[1].replace(/\??$/, '').trim()
    if (q && q.length > 1 && !/(play|youtube|open\s+\w+|launch|news)/i.test(q)) {
      return { response: `Searching Google for "${q}"`, url: `https://google.com/search?q=${encodeURIComponent(q)}` }
    }
  }

  // OPEN LAST CREATED FOLDER
  const isOpenLastFolder =
    /\b(?:open|show)\s+(?:the\s+)?folder\s+(?:you\s+)?(?:just\s+)?created\b/i.test(t)
    || /\b(?:open|show)\s+(?:this|that)\s+folder\b/i.test(t)
    || /\b(?:open|show)\s+it\b/i.test(t)
  if (isOpenLastFolder) {
    return { response: 'Opening it now.', openLastFolder: true }
  }

  // CREATE FOLDER — extract inline name and/or path, ask for anything missing
  const isFolderCreate = /\b(?:create|make|add|new)\s+(?:a\s+)?(?:new\s+)?folder\b/i.test(t)
  if (isFolderCreate) {
    // Name: "named X", "called X", "name it X", "call it X"
    const nameM =
      t.match(/\bname\s+it\s+(.+?)(?:\s+(?:in|on|inside|at)\s+|\s*$)/i)
      ?? t.match(/\b(?:named?|called?)\s+(.+?)(?:\s+(?:in|on|inside|at)\s+|\s*$)/i)
    // Path: "in E drive", "in E:", "in desktop", "on desktop", "in C:"
    const pathM =
      t.match(/\b(?:in|on|inside|at)\s+(?:the\s+)?([a-z]\s*(?:drive|disk|:)?)\b/i)
      ?? t.match(/\b(?:in|on|inside|at)\s+(?:the\s+)?(desktop|documents?|downloads?|pictures?|music)\b/i)
    const rawName = nameM ? nameM[1].replace(/\b(?:and|with)\b.*/i, '').trim() : undefined
    const name = _validName(rawName) ? rawName : undefined
    // Default to Desktop when name is known but location isn't specified
    const path = pathM ? pathM[1].trim() : (name ? 'Desktop' : undefined)
    return {
      response: name && pathM
        ? 'Sure, creating the folder now.'
        : name
          ? `Got it — creating "${name}" on your Desktop.`
          : path
            ? `Sure! What should I name the folder?`
            : 'What should I name it, and where should I create it?',
      createFolder: { name, path },
    }
  }

  // ── DESKTOP TYPE ("type hello world", "write some text", "enter password") ──
  const typeMatch = t.match(/^(?:type|write|enter|input)\s+(?:(?:the|this)\s+(?:text|word|phrase|number)\s+)?["']?(.+?)["']?$/)
  if (typeMatch) {
    const txt = typeMatch[1].trim()
    if (txt && txt.length > 0 && !/^(folder|file|app|application|url|website)$/.test(txt)) {
      return { response: 'Done, typed it.', desktopType: txt }
    }
  }

  // ── DESKTOP HOTKEY ("press ctrl+c", "copy", "paste", "undo", "save") ──
  const isHotkey =
    /^(?:press(?:\s+(?:the\s+)?)?(?:ctrl|control|alt|shift)\s*[+\-]\s*\w+)$/i.test(t)
    || /^(?:ctrl|control|alt|shift)\s*[+\-]\s*\w+$/i.test(t)
    || /^(?:press\s+)?(enter|escape|esc|tab|backspace|delete|home|end|f(?:1[0-2]|[1-9]))$/i.test(t)
    || /^(copy|paste|cut|undo|redo|save|select\s+all|new\s+tab|close\s+tab|refresh)(?:\s+(?:that|it|now))?$/i.test(t)
  if (isHotkey) {
    const hotkeyMap: Record<string, string> = {
      'copy': 'ctrl+c', 'paste': 'ctrl+v', 'cut': 'ctrl+x',
      'undo': 'ctrl+z', 'redo': 'ctrl+y', 'save': 'ctrl+s',
      'select all': 'ctrl+a', 'new tab': 'ctrl+t', 'close tab': 'ctrl+w',
      'refresh': 'f5', 'enter': 'enter', 'escape': 'escape', 'esc': 'escape', 'tab': 'tab',
    }
    const cleaned = t.replace(/^press\s+(?:the\s+)?/, '').replace(/\s+(that|it|now)$/, '')
    const keys = hotkeyMap[cleaned] ?? cleaned
    return { response: `Done.`, desktopHotkey: keys }
  }

  // ── SCROLL ("scroll down", "scroll up 5 times") ──
  const scrollM = t.match(/\bscroll\s+(up|down)(?:\s+(\d+)(?:\s+times?)?)?/)
  if (scrollM) {
    const dir = scrollM[1] as 'up' | 'down'
    const amt = scrollM[2] ? parseInt(scrollM[2]) : 3
    return { response: `Scrolling ${dir}.`, desktopScroll: { direction: dir, amount: amt } }
  }

  return null
}

// ── Markdown stripping ────────────────────────────────────────────────────────

function stripMarkdown(raw: string): string {
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
  t = t.replace(/\s{2,}/g, ' ').trim()
  return t
}

function cleanForSpeech(raw: string): string {
  let t = stripMarkdown(raw)
  if (t.length > 300) {
    const cut = t.lastIndexOf('.', 300)
    t = cut > 80 ? t.slice(0, cut + 1) : t.slice(0, 300)
  }
  return t.trim()
}

// ── Drive letter normalization (phonetic Whisper corrections) ─────────────────

function normalizeDriveLetters(text: string): string {
  const phonetics: Array<[RegExp, string]> = [
    [/\bsee\s+drive\b/gi,   'C drive'],
    [/\bsea\s+drive\b/gi,   'C drive'],
    [/\bsi\s+drive\b/gi,    'C drive'],
    [/\bdee\s+drive\b/gi,   'D drive'],
    [/\bde\s+drive\b/gi,    'D drive'],
    [/\bee\s+drive\b/gi,    'E drive'],
    [/\bhe\s+drive\b/gi,    'E drive'],
    [/\bie\s+drive\b/gi,    'E drive'],
    [/\bef\s+drive\b/gi,    'F drive'],
    [/\bfe\s+drive\b/gi,    'F drive'],
  ]
  let t = text
  for (const [re, replacement] of phonetics) t = t.replace(re, replacement)
  return t
}

// ── Conversational input detector — bypasses all tool routing ─────────────────

function isConversationalInput(text: string): boolean {
  const t = text.toLowerCase().trim().replace(/[!?.]+$/, '')
  const patterns = [
    /^(haha+|lol|lmao|lmfao|rofl|omg|wow|nice|cool|awesome|great|amazing|interesting|i see|got it|makes sense)$/,
    /^(hi|hello|hey|sup|yo|what'?s up)$/,
    /^(ok|okay|sure|fine|alright|yep|yeah|nope|maybe)$/,
    /^(you'?re|you are)\s+(funny|smart|dumb|stupid|silly|cool|amazing|great|awesome)/,
    /^(that'?s|thats)\s+(funny|great|cool|awesome|silly|hilarious|interesting)/,
    /\btell me (a )?joke\b/,
    /\bsay something (funny|interesting|random|cool)\b/,
    /\b(make me laugh|entertain me|tell me something)\b/,
  ]
  return patterns.some((p) => p.test(t))
}

// ── Browser TTS fallback ──────────────────────────────────────────────────────

function speakBrowser(text: string): Promise<void> {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) { resolve(); return }
    window.speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(text)
    u.rate = 1.05; u.pitch = 1.0; u.volume = 0.9
    u.onend = () => resolve()
    u.onerror = () => resolve()
    const speak = () => window.speechSynthesis.speak(u)
    if (window.speechSynthesis.getVoices().length > 0) speak()
    else { window.speechSynthesis.addEventListener('voiceschanged', speak, { once: true }); setTimeout(speak, 300) }
  })
}

// ── System info formatter ─────────────────────────────────────────────────────

function formatSystemInfo(raw: string): string {
  // raw format: "OS Caption|BuildNumber|CPU Name|Cores|Threads|RAM GB"
  const [os, build, cpu, cores, threads, ram] = raw.split('|').map((s) => s.trim())
  const parts: string[] = []
  if (os)             parts.push(os)
  if (build)          parts.push(`Build ${build}`)
  if (cpu)            parts.push(cpu)
  if (cores && threads) parts.push(`${cores} cores, ${threads} threads`)
  if (ram)            parts.push(`${ram} RAM`)
  return parts.length
    ? `Here are your system specs: ${parts.join('. ')}.`
    : "I couldn't read your system specs right now."
}

// ── Open URL via Electron IPC ─────────────────────────────────────────────────

function openUrl(url: string): void {
  if (window.electronAPI) window.electronAPI.openUrl(url)
  else window.open(url, '_blank', 'noopener')
}

// ── Launch system app via Electron IPC ───────────────────────────────────────

function launchApp(command: string): void {
  if (window.electronAPI) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window.electronAPI as any).launchApp(command)
  }
}

// ── Audio Queue — sequential playback with eager TTS fetch ────────────────────

interface QueueEntry {
  index: number
  ttsPromise: Promise<string | null>  // resolves to blob URL or null
  played: boolean
}

class AudioQueue {
  private entries: QueueEntry[] = []
  private nextPlay = 0
  private aborted = false
  private volume: number
  private onEmpty: () => void
  private currentAudio: HTMLAudioElement | null = null

  constructor(opts: { volume: number; onEmpty: () => void }) {
    this.volume  = opts.volume
    this.onEmpty = opts.onEmpty
  }

  push(index: number, ttsPromise: Promise<string | null>): void {
    if (this.aborted) return
    this.entries.push({ index, ttsPromise, played: false })
    this.entries.sort((a, b) => a.index - b.index)
    this._tryPlay()
  }

  private _tryPlay(): void {
    if (this.aborted) return
    const entry = this.entries.find((e) => e.index === this.nextPlay && !e.played)
    if (!entry) return

    entry.played = true
    this.nextPlay++

    entry.ttsPromise.then((url) => {
      if (this.aborted || !url) { this._advance(); return }
      const audio = new Audio(url)
      audio.volume = this.volume
      this.currentAudio = audio
      const done = () => { URL.revokeObjectURL(url); this.currentAudio = null; this._advance() }
      audio.onended = done
      audio.onerror = done
      audio.play().catch(done)
    })
  }

  private _advance(): void {
    if (this.aborted) return
    if (this.entries.every((e) => e.played)) {
      this.onEmpty()
      return
    }
    this._tryPlay()
  }

  abort(): void {
    this.aborted = true
    if (this.currentAudio) { this.currentAudio.pause(); this.currentAudio = null }
  }

  markDone(): void {
    // Called when SSE stream ends — triggers onEmpty if all pushed entries are played
    setTimeout(() => { if (!this.aborted) this._advance() }, 50)
  }
}

// ── TTS fetch helpers ─────────────────────────────────────────────────────────

async function _fetchTtsUrl(
  text: string,
  voice: string,
  speed: number,
  signal: AbortSignal,
): Promise<string | null> {
  try {
    const resp = await fetch(`${API_BASE}/api/v1/voice/synthesize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice, speed }),
      signal,
    })
    if (!resp.ok) return null
    const blob = await resp.blob()
    return URL.createObjectURL(blob)
  } catch {
    return null
  }
}

/** Electron uses Chromium — MP3 in MediaSource is always supported. */
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
 * Streaming TTS via MediaSource — audio starts as the first bytes arrive
 * from OpenAI, before the full MP3 is generated. Falls back to blob on error.
 */
function _fetchTtsUrlStream(
  text: string,
  voice: string,
  speed: number,
  signal: AbortSignal,
): Promise<string | null> {
  if (!_USE_MSE) return _fetchTtsUrl(text, voice, speed, signal)

  return new Promise<string | null>((resolve) => {
    const ms  = new MediaSource()
    const url = URL.createObjectURL(ms)
    let resolved = false

    const _fail = () => {
      if (!resolved) {
        resolved = true
        URL.revokeObjectURL(url)
        // Fall back to standard blob approach
        _fetchTtsUrl(text, voice, speed, signal).then(resolve)
      }
    }

    ms.addEventListener('sourceopen', async () => {
      let sb: SourceBuffer
      try { sb = ms.addSourceBuffer('audio/mpeg') } catch { _fail(); return }

      const _append = (chunk: Uint8Array) =>
        new Promise<void>((res, rej) => {
          if (signal.aborted) { rej(new Error('aborted')); return }
          const go = () => {
            try { sb.appendBuffer(chunk) } catch (e) { rej(e); return }
            sb.addEventListener('updateend', res, { once: true })
          }
          sb.updating
            ? sb.addEventListener('updateend', go, { once: true })
            : go()
        })

      try {
        const r = await fetch(`${API_BASE}/api/v1/voice/synthesize-stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, voice, speed }),
          signal,
        })
        if (!r.ok || !r.body) { _fail(); return }

        const reader = r.body.getReader()
        while (true) {
          const { done, value } = await reader.read()
          if (signal.aborted || done) {
            if (ms.readyState === 'open') ms.endOfStream()
            break
          }
          await _append(value)
          if (!resolved) { resolved = true; resolve(url) }
        }
      } catch {
        if (ms.readyState === 'open') { try { ms.endOfStream() } catch {} }
        _fail()
      }
    }, { once: true })

    setTimeout(() => { if (!resolved) _fail() }, 8_000)
  })
}

// ── SSE stream consumer ───────────────────────────────────────────────────────

interface StreamCallbacks {
  onChunk:     (text: string, index: number) => void
  onDone:      (fullText: string) => void
  onError:     (msg: string) => void
  onFollowUp?: (suggestion: string) => void
}

async function _streamAndSpeak(
  userText:         string,
  history:          Array<{ role: string; text: string }>,
  queue:            AudioQueue,
  signal:           AbortSignal,
  voice:            string,
  speed:            number,
  cbs:              StreamCallbacks,
  personalityMode?: string,
  language?:        string,
): Promise<void> {
  let resp: Response
  try {
    resp = await fetch(`${API_BASE}/api/v1/voice/respond-stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text:             userText,
        history,
        personality_mode: personalityMode ?? '',
        use_tools:        true,
        use_context:      true,
        language:         language ?? '',
      }),
      signal,
    })
  } catch (e) {
    if (!signal.aborted) cbs.onError(String(e))
    return
  }

  if (!resp.ok) { cbs.onError(`HTTP ${resp.status}`); return }

  const reader  = resp.body!.getReader()
  const decoder = new TextDecoder()
  let sseBuf   = ''
  let ttsAccum = ''   // accumulate chunks into sentence-level batches for natural TTS
  let ttsSeqIdx = 0  // sequential index for AudioQueue ordering

  // Flush accumulated text to TTS.
  // First chunk → fire immediately (no accumulation) using streaming MSE endpoint.
  // Subsequent  → batch to sentence boundary or 100 chars to avoid HTTP overhead.
  const flushTts = (force = false) => {
    const text = ttsAccum.trim()
    if (!text) return
    ttsAccum = ''
    const ttsPromise = _fetchTtsUrlStream(text, voice, speed, signal)
    queue.push(ttsSeqIdx++, ttsPromise)
  }

  while (true) {
    let done = false; let value: Uint8Array | undefined
    try {
      ;({ done, value } = await reader.read())
    } catch { break }
    if (done) break
    if (signal.aborted) break

    sseBuf += decoder.decode(value, { stream: true })
    const lines = sseBuf.split('\n')
    sseBuf = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const ev = JSON.parse(line.slice(6)) as {
          type: string; turn_id: string; index?: number; text?: string; full_text?: string; message?: string
        }
        if (ev.type === 'chunk' && ev.text && ev.index != null) {
          cbs.onChunk(ev.text, ev.index)           // UI: show text word-by-word
          ttsAccum += (ttsAccum ? ' ' : '') + ev.text
          if (ttsSeqIdx === 0) {
            // First chunk: fire immediately — no accumulation needed
            flushTts()
          } else {
            // Subsequent: batch to sentence boundary or 100 chars
            const hasBoundary = /[.!?](\s|$)/.test(ttsAccum)
            if (hasBoundary || ttsAccum.length >= 100) flushTts()
          }
        } else if (ev.type === 'done') {
          cbs.onDone(ev.full_text ?? '')
          flushTts(true)                           // force-flush any remaining text
          queue.markDone()
          return
        } else if (ev.type === 'error') {
          cbs.onError(ev.message ?? 'Stream error')
          return
        } else if (ev.type === 'follow_up') {
          cbs.onFollowUp?.((ev as { suggestion?: string }).suggestion ?? '')
        } else if (ev.type === 'action') {
          const act = ev as {
            action_url?: string; action_app?: string; action_path?: string; url?: string
          }
          if (act.action_url || act.url) {
            openUrl((act.action_url || act.url)!)
          } else if (act.action_path) {
            console.log('[TOOL] stream action: open_directory | path:', act.action_path)
            fetch(`${API_BASE}/api/v1/system/open-directory`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ path: act.action_path }),
            }).then(r => r.json()).then(res => console.log('[TOOL] open_directory result:', res)).catch(() => {})
          } else if (act.action_app) {
            console.log('[TOOL] stream action: open_app | app:', act.action_app)
            fetch(`${API_BASE}/api/v1/system/open-app`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ app: act.action_app }),
            }).then(r => r.json()).then(res => console.log('[TOOL] open_app result:', res)).catch(() => {})
          }
        }
      } catch { /* malformed event */ }
    }
  }

  flushTts(true)
  queue.markDone()
}

// ── Hook ──────────────────────────────────────────────────────────────────────

const API_BASE = 'http://localhost:8000'
const STOP_RESPONSE = "Okay, stopping now. Talk to you later."
const RETRY_MSG = "Didn't catch that. Listening again."

export function useVoiceSession() {
  const [sessionState, setSessionState] = useState<SessionState>('idle')
  const [messages,     setMessages]     = useState<ConvMessage[]>([])
  const [error,        setError]        = useState<string | null>(null)
  const [followUp,     setFollowUp]     = useState<string | null>(null)

  const activeRef      = useRef(false)
  const mediaRecRef    = useRef<MediaRecorder | null>(null)
  const chunksRef      = useRef<Blob[]>([])
  const audioCtxRef    = useRef<AudioContext | null>(null)
  const silenceTimRef  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const noSpeechTimRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const maxRecTimRef   = useRef<ReturnType<typeof setTimeout> | null>(null)
  const rafRef         = useRef<number | null>(null)

  // Streaming refs
  const taskCtrlRef  = useRef<AbortController | null>(null)
  const queueRef     = useRef<AudioQueue | null>(null)
  const isRunningRef = useRef(false)   // single-instance guard — true while listen cycle is active

  const historyRef = useRef<Array<{ role: string; text: string }>>([])
  const pendingFolderRef   = useRef<{ stage: 'name'; knownPath?: string } | { stage: 'path'; name: string } | null>(null)
  const lastCreatedFolderRef = useRef<{ name: string; path: string } | null>(null)
  const lastActionRef = useRef<{ intent: string; action: SystemAction; transcript: string } | null>(null)

  const addMsg = useCallback((role: ConvMessage['role'], text: string, status: ConvMessage['status'] = 'done'): string => {
    const id = crypto.randomUUID()
    setMessages((p) => {
      const next = [...p, { id, role, text, status, timestamp: new Date() }]
      historyRef.current = next
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role, text: m.text }))
      return next
    })
    return id
  }, [])

  const updMsg = useCallback((id: string, patch: Partial<ConvMessage>) => {
    setMessages((p) => {
      const next = p.map((m) => m.id === id ? { ...m, ...patch } : m)
      historyRef.current = next
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role, text: m.text }))
      return next
    })
  }, [])

  const stopMedia = useCallback(() => {
    if (rafRef.current)      { cancelAnimationFrame(rafRef.current); rafRef.current = null }
    if (silenceTimRef.current)  { clearTimeout(silenceTimRef.current);  silenceTimRef.current = null }
    if (noSpeechTimRef.current) { clearTimeout(noSpeechTimRef.current); noSpeechTimRef.current = null }
    if (maxRecTimRef.current)   { clearTimeout(maxRecTimRef.current);   maxRecTimRef.current = null }
    const mr = mediaRecRef.current
    if (mr && mr.state !== 'inactive') {
      try { mr.stop(); mr.stream?.getTracks().forEach((t) => t.stop()) } catch { /* ok */ }
    }
    if (audioCtxRef.current) { audioCtxRef.current.close().catch(() => {}); audioCtxRef.current = null }
  }, [])

  // ── Simple TTS for greetings / system responses ────────────────────────────
  const speakResponse = useCallback(async (raw: string): Promise<void> => {
    const text = stripMarkdown(raw)
    if (!text) return
    const { voice, speed, voiceEnabled, volume } = readAssistantSettings()
    if (!voiceEnabled) return
    try {
      const ctrl    = new AbortController()
      const timeout = setTimeout(() => ctrl.abort(), 30000)
      let resp: Response
      try {
        resp = await fetch(`${API_BASE}/api/v1/voice/synthesize`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, voice, speed }),
          signal: ctrl.signal,
        })
      } finally { clearTimeout(timeout) }
      if (resp!.ok) {
        const blob = await resp!.blob()
        const url  = URL.createObjectURL(blob)
        await new Promise<void>((resolve) => {
          const audio = new Audio(url)
          audio.volume = volume
          const done  = () => { URL.revokeObjectURL(url); resolve() }
          const safeMs = (Math.max(6, text.length / 8) / speed) * 1000 + 8000
          const safety = setTimeout(done, safeMs)
          audio.onended = () => { clearTimeout(safety); done() }
          audio.onerror = () => { clearTimeout(safety); done() }
          audio.play().catch(() => { clearTimeout(safety); done() })
        })
        return
      }
    } catch { /* fall through */ }
    await speakBrowser(cleanForSpeech(raw))
  }, [])

  // ── Auto-restart — THE ONLY path that starts a new listen cycle ───────────
  // Call after every cycle end. Guards prevent double-starts and speaking overlap.
  const loopRef = useRef<() => Promise<void>>(async () => {})

  const maybeRestartListening = useCallback(() => {
    if (!activeRef.current) {
      console.log('[AUTO LISTEN] blocked (session inactive)')
      return
    }
    if (isRunningRef.current) {
      console.log('[AUTO LISTEN] blocked (already running)')
      return
    }
    console.log('[AUTO LISTEN] triggered — restarting listen cycle')
    loopRef.current()
  }, [])

  // ── Conversation loop ──────────────────────────────────────────────────────
  loopRef.current = async function loop(): Promise<void> {
    if (!activeRef.current) return
    if (isRunningRef.current) { console.log('[AUTO LISTEN] blocked (loop guard)'); return }
    isRunningRef.current = true

    // Abort any in-flight AI task
    taskCtrlRef.current?.abort()
    queueRef.current?.abort()

    // LISTEN
    console.log('[MIC START ATTEMPT] state:', sessionState, 'isRunning:', isRunningRef.current, 'active:', activeRef.current)
    setSessionState('listening')
    chunksRef.current = []

    try {
      await new Promise<void>((resolve, reject) => {
        navigator.mediaDevices
          .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 } })
          .then((stream) => {
            const rec = new MediaRecorder(stream)
            rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
            rec.onstop = () => { stream.getTracks().forEach((t) => t.stop()); resolve() }
            rec.start()
            mediaRecRef.current = rec

            maxRecTimRef.current = setTimeout(() => {
              if (rec.state !== 'inactive') rec.stop()
            }, VAD.maxTotalMs)

            // VAD
            try {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              const AudioCtxCls = window.AudioContext ?? (window as any).webkitAudioContext
              if (!AudioCtxCls) return

              const ctx = new AudioCtxCls() as AudioContext
              audioCtxRef.current = ctx
              const analyser = ctx.createAnalyser()
              analyser.fftSize = 1024
              ctx.createMediaStreamSource(stream).connect(analyser)
              const buf = new Uint8Array(analyser.frequencyBinCount)

              const binWidth = ctx.sampleRate / analyser.fftSize
              const lo = Math.max(1, Math.round(300 / binWidth))
              const hi = Math.min(buf.length - 1, Math.round(3400 / binWidth))

              let smoothRms    = 0
              let noiseFloor   = 0
              let calibFrames  = 0
              let threshold    = VAD.minThreshold
              let speechCnt    = 0
              let speechFrames = 0  // total frames spent in 'speaking' phase
              type Phase = 'calibrating' | 'waiting' | 'speaking' | 'paused'
              let phase: Phase = 'calibrating'

              const stopRec = () => {
                if (rec.state !== 'inactive') rec.stop()
                ctx.close().catch(() => {})
              }

              const tick = () => {
                if (!activeRef.current || rec.state === 'inactive') return
                analyser.getByteFrequencyData(buf)
                let ss = 0
                for (let i = lo; i < hi; i++) ss += buf[i] * buf[i]
                const raw = Math.sqrt(ss / (hi - lo)) / 128
                smoothRms = VAD.smoothingAlpha * raw + (1 - VAD.smoothingAlpha) * smoothRms

                switch (phase) {
                  case 'calibrating':
                    noiseFloor = (noiseFloor * calibFrames + smoothRms) / (calibFrames + 1)
                    if (++calibFrames >= VAD.calibrationFrames) {
                      threshold = Math.max(VAD.minThreshold, Math.min(VAD.maxThreshold, noiseFloor * VAD.thresholdMultiplier))
                      phase = 'waiting'
                      noSpeechTimRef.current = setTimeout(stopRec, VAD.noSpeechTimeoutMs)
                    }
                    break
                  case 'waiting':
                    if (smoothRms > threshold) { if (++speechCnt >= VAD.speechMinFrames) { phase = 'speaking'; speechCnt = 0; clearTimeout(noSpeechTimRef.current!); noSpeechTimRef.current = null } }
                    else { speechCnt = 0 }
                    break
                  case 'speaking': {
                    speechFrames++
                    const exitLevel = Math.max(VAD.minThreshold * 0.5, noiseFloor * VAD.exitMultiplier)
                    if (smoothRms < exitLevel) {
                      phase = 'paused'
                      silenceTimRef.current = setTimeout(stopRec, VAD.silenceAfterMs)
                    }
                    break
                  }
                  case 'paused':
                    if (smoothRms > threshold * VAD.resumeHysteresis) {
                      phase = 'speaking'
                      clearTimeout(silenceTimRef.current!); silenceTimRef.current = null
                    }
                    break
                }
                rafRef.current = requestAnimationFrame(tick)
              }
              rafRef.current = requestAnimationFrame(tick)
            } catch { /* VAD unavailable */ }
          })
          .catch(reject)
      })
    } catch (err) {
      const denied = err instanceof DOMException && err.name === 'NotAllowedError'
      setError(denied ? 'Microphone access denied.' : 'Could not access microphone.')
      activeRef.current = false
      isRunningRef.current = false
      setSessionState('stopped')
      setTimeout(() => setSessionState('idle'), 2500)
      return
    }

    if (rafRef.current)      { cancelAnimationFrame(rafRef.current); rafRef.current = null }
    if (silenceTimRef.current)  { clearTimeout(silenceTimRef.current);  silenceTimRef.current = null }
    if (noSpeechTimRef.current) { clearTimeout(noSpeechTimRef.current); noSpeechTimRef.current = null }
    if (maxRecTimRef.current)   { clearTimeout(maxRecTimRef.current);   maxRecTimRef.current = null }
    if (!activeRef.current) return

    // TRANSCRIBE
    setSessionState('transcribing')
    if (!chunksRef.current.length) {
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
    let transcript = ''
    let detectedLang = 'en'
    try {
      const form = new FormData()
      form.append('audio', blob, 'recording.webm')
      const r = await fetch(`${API_BASE}/api/v1/voice/transcribe`, { method: 'POST', body: form })
      const data = await r.json()
      transcript = (data?.data?.text ?? '').trim()
      detectedLang = (data?.data?.language ?? 'en') as string
      // Discard noise artifacts: must contain at least one letter and be ≥3 chars
      if (transcript.length < 3 || /^[^a-zA-Z\u0600-\u06FF]+$/.test(transcript)) transcript = ''
    } catch { /* retry */ }

    if (!transcript) {
      addMsg('system', RETRY_MSG)
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // STOP CHECK
    if (isStopPhrase(transcript)) {
      addMsg('user', transcript)
      addMsg('assistant', STOP_RESPONSE)
      setSessionState('speaking')
      await speakResponse(STOP_RESPONSE)
      activeRef.current = false
      setSessionState('stopped')
      setTimeout(() => setSessionState('idle'), 1500)
      return
    }

    // Normalize phonetic drive letters ("see drive" → "C drive")
    const normalizedTranscript = normalizeDriveLetters(transcript)
    if (normalizedTranscript !== transcript) {
      console.log(`[NORM] "${transcript}" → "${normalizedTranscript}"`)
      // Use normalized version for all subsequent processing
      Object.defineProperty(arguments[0], 'transcript', { value: normalizedTranscript })
    }
    // Replace transcript with normalized version
    const tNorm = normalizeDriveLetters(transcript)
    if (tNorm !== transcript) {
      // Rebind for downstream use — reassign works on const in this scope
      ;(globalThis as any)._normTmp = tNorm
    }

    // CONVERSATIONAL BYPASS — skip all tool routing for casual chat
    if (isConversationalInput(transcript)) {
      console.log('[INTENT] conversation → GPT direct')
      addMsg('user', transcript)
      setSessionState('processing')
      const aId = addMsg('assistant', '', 'processing')
      const ctrl2 = new AbortController()
      taskCtrlRef.current = ctrl2
      const { voice, speed, volume, mode: pMode2 } = readAssistantSettings()
      let chunkText2 = ''
      const queue2 = new AudioQueue({
        volume,
        onEmpty: () => {
          if (!activeRef.current || ctrl2.signal.aborted) return
          isRunningRef.current = false
          setSessionState('idle')
          maybeRestartListening()
        },
      })
      queueRef.current = queue2
      await _streamAndSpeak(transcript, historyRef.current.slice(-6), queue2, ctrl2.signal, voice, speed, {
        onChunk: (text, _i) => {
          if (!chunkText2) setSessionState('speaking')
          chunkText2 += (chunkText2 ? ' ' : '') + text
          updMsg(aId, { text: chunkText2 })
        },
        onDone: (fullText) => { updMsg(aId, { text: fullText || chunkText2, status: 'done' }) },
        onError: (msg) => {
          if (ctrl2.signal.aborted) return
          updMsg(aId, { text: msg, status: 'error' })
          queue2.abort()
          isRunningRef.current = false
          setSessionState('idle')
          maybeRestartListening()
        },
      }, pMode2, detectedLang)
      return
    }

    // PENDING FOLDER FLOW — multi-turn name → path collection
    if (pendingFolderRef.current) {
      const pf = pendingFolderRef.current
      addMsg('user', transcript)

      if (pf.stage === 'name') {
        const rawName = transcript.replace(/[.!?,;:]+$/, '').trim()
        const name = rawName
          .replace(/^(?:name\s+it|call\s+it|it(?:'?s|\s+is)?|the\s+name\s+is?|name\s+is?)\s+/i, '')
          .trim()

        if (pf.knownPath) {
          // Path already known — create immediately without asking again
          pendingFolderRef.current = null
          const holdMsg = `Creating the "${name}" folder now.`
          const aId2 = addMsg('assistant', holdMsg)
          setSessionState('speaking')
          const [, result2] = await Promise.all([
            speakResponse(holdMsg),
            fetch(`${API_BASE}/api/v1/system/create-folder`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name, path: pf.knownPath }),
            }).then((r) => r.json()).catch(() => null),
          ])
          setSessionState('processing')
          let spoken2: string
          if (result2?.success) {
            const loc2: string = result2.path || pf.knownPath
            lastCreatedFolderRef.current = { name, path: loc2 }
            spoken2 = `Done! I created a folder named "${name}" in ${loc2}. You can say "open this folder" to open it.`
          } else {
            spoken2 = result2?.spoken || `Couldn't create the folder. Please try again.`
          }
          updMsg(aId2, { text: spoken2 })
          setSessionState('speaking')
          await speakResponse(spoken2)
          setSessionState('idle')
          isRunningRef.current = false
          maybeRestartListening()
          return
        }

        // Default to Desktop when no path is specified
        pendingFolderRef.current = null
        const holdMsgD = `Creating the "${name}" folder on your Desktop.`
        const aIdD = addMsg('assistant', holdMsgD)
        setSessionState('speaking')
        const [, resultD] = await Promise.all([
          speakResponse(holdMsgD),
          fetch(`${API_BASE}/api/v1/system/create-folder`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, path: 'Desktop' }),
          }).then((r) => r.json()).catch(() => null),
        ])
        setSessionState('processing')
        let spokenD: string
        if (resultD?.success) {
          const locD: string = resultD.path || 'Desktop'
          lastCreatedFolderRef.current = { name, path: locD }
          spokenD = resultD.spoken || `Done! I created the "${name}" folder on your Desktop. Say "open it" to open it.`
        } else {
          spokenD = resultD?.spoken || `Couldn't create the folder. Please try again.`
        }
        updMsg(aIdD, { text: spokenD })
        setSessionState('speaking')
        await speakResponse(spokenD)
        setSessionState('idle')
        isRunningRef.current = false
        maybeRestartListening()
        return
      }

      if (pf.stage === 'path') {
        const { name } = pf
        const rawPath = transcript.replace(/[.!?,;:]+$/, '').trim()
        const pathStr = rawPath.replace(/^(?:in|on|inside|at)\s+(?:the\s+)?/i, '').trim()
        pendingFolderRef.current = null
        const holdMsg = `Creating the "${name}" folder now.`
        const aId = addMsg('assistant', holdMsg)
        setSessionState('speaking')
        const [, result] = await Promise.all([
          speakResponse(holdMsg),
          fetch(`${API_BASE}/api/v1/system/create-folder`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, path: pathStr }),
          }).then(r => r.json()).catch(() => null),
        ])
        setSessionState('processing')
        let spoken: string
        if (result?.success) {
          const loc: string = result.path || pathStr
          lastCreatedFolderRef.current = { name, path: loc }
          spoken = _failsafe('create_folder', result, `Done! I created the "${name}" folder in ${loc}. Say "open it" to open it.`)
        } else {
          spoken = _failsafe('create_folder', result, `Couldn't create the folder. Please try again.`)
        }
        updMsg(aId, { text: spoken })
        setSessionState('speaking')
        await speakResponse(spoken)
        setSessionState('idle')
        isRunningRef.current = false
        maybeRestartListening()
        return
      }
    }

    // CONTEXT FOLLOW-UP — "now create in X drive" reuses last folder name
    const nowCreateM = /\b(?:now|then|also)\s+(?:create|make|add)\s+(?:(?:a|another|one|new)\s+)?(?:(?:new|same)\s+)?(?:folder|directory)?\s+(?:in|on|inside|at)\s+(?:the\s+)?([a-e])\s*(?:\s+drive|\s*:)/i.exec(transcript)
    if (nowCreateM && lastCreatedFolderRef.current) {
      const { name: inheritedName } = lastCreatedFolderRef.current
      const newPath = nowCreateM[1].toUpperCase() + ':\\'
      const holdMsg3 = `Creating "${inheritedName}" in ${nowCreateM[1].toUpperCase()} drive now.`
      addMsg('user', transcript)
      const aId3 = addMsg('assistant', holdMsg3)
      setSessionState('speaking')
      const [, res3] = await Promise.all([
        speakResponse(holdMsg3),
        fetch(`${API_BASE}/api/v1/system/create-folder`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: inheritedName, path: newPath }),
        }).then((r) => r.json()).catch(() => null),
      ])
      setSessionState('processing')
      const sp3 = res3?.success
        ? `Done! "${inheritedName}" created in ${newPath}.`
        : res3?.spoken || `Couldn't create it. Please try again.`
      if (res3?.success) lastCreatedFolderRef.current = { name: inheritedName, path: res3.path || newPath }
      updMsg(aId3, { text: sp3 })
      setSessionState('speaking')
      await speakResponse(sp3)
      setSessionState('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // IDENTITY CHECK
    const { mode: _identityMode } = readAssistantSettings()
    const identityReply = checkIdentity(transcript, _identityMode)
    if (identityReply) {
      addMsg('user', transcript)
      addMsg('assistant', identityReply)
      setSessionState('speaking')
      await speakResponse(identityReply)
      setSessionState('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // SYSTEM FAST-PATH
    const sysAction = detectSystemAction(transcript)
    if (sysAction) {
      addMsg('user', transcript)
      const aId = addMsg('assistant', sysAction.response)

      // Helper: plays holding phrase in the user's selected TTS voice while the
      // data fetch runs simultaneously. Both finish before we speak the result.
      const _holdAndFetch = async <T>(fetchPromise: Promise<T>): Promise<T> => {
        setSessionState('speaking')
        const [, result] = await Promise.all([
          speakResponse(sysAction.response),
          fetchPromise,
        ])
        setSessionState('processing')
        return result
      }

      const _finishAction = async (spoken: string) => {
        updMsg(aId, { text: spoken })
        setSessionState('speaking')
        await speakResponse(spoken)
        setSessionState('idle')
        isRunningRef.current = false
        maybeRestartListening()
      }

      if (sysAction.intent === 'repeat_last') {
        if (lastActionRef.current) {
          // Overwrite with stored action and fall through to normal dispatch
          Object.assign(sysAction, lastActionRef.current.action)
        } else {
          await _finishAction("I don't have a previous action to repeat.")
          return
        }
      }

      if (sysAction.workMode) {
        console.log('[TOOL] work_mode | env: desktop | launching vscode + github')
        lastActionRef.current = { intent: 'work_mode', action: sysAction, transcript }
        fetch(`${API_BASE}/api/v1/system/open-app`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ app: 'vscode' }),
        }).then(r => r.json()).then(res => console.log('[TOOL] work_mode vscode:', res)).catch(() => {})
        openUrl('https://github.com/TayyabAziz11')
        await _finishAction(sysAction.response)
        return
      }

      if (sysAction.newsQuery) {
        const newsUrl = `https://news.google.com/search?q=${encodeURIComponent(sysAction.newsQuery)}&hl=en`
        const dataFetch = fetch(`${API_BASE}/api/v1/system/news?q=${encodeURIComponent(sysAction.newsQuery)}&limit=5&topic=${sysAction.newsQueryTopic ?? false}`)
          .then(r => r.json()).catch(() => null)
        const data = await _holdAndFetch(dataFetch)
        const spoken = data?.success && data.spoken ? data.spoken : 'I had trouble fetching the news.'
        openUrl(newsUrl)
        await _finishAction(spoken)
        return
      }

      if (sysAction.priceQuery) {
        const googleUrl = `https://google.com/search?q=${encodeURIComponent(sysAction.priceQuery + ' price today')}`
        const dataFetch = fetch(`${API_BASE}/api/v1/system/price?q=${encodeURIComponent(sysAction.priceQuery)}`)
          .then(r => r.json()).catch(() => null)
        const data = await _holdAndFetch(dataFetch)
        const spoken = data?.success && data.spoken ? data.spoken : `I couldn't fetch the live price for ${sysAction.priceQuery} right now.`
        openUrl(googleUrl)
        await _finishAction(spoken)
        return
      }

      if (sysAction.youtubeQuery) {
        const dataFetch = fetch(`${API_BASE}/api/v1/system/youtube-play`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: sysAction.youtubeQuery }),
        }).then(r => r.json()).catch(() => null)
        const data = await _holdAndFetch(dataFetch)
        let videoUrl = `https://youtube.com/search?q=${encodeURIComponent(sysAction.youtubeQuery)}`
        let msg = 'Here you go!'
        if (data?.url) {
          videoUrl = data.url
          if (data.title) {
            const picks = [`Found it — ${data.title}.`, `Here it is, ${data.title}.`, `Playing ${data.title} now.`, `Got it! ${data.title}, coming right up.`]
            msg = picks[Math.floor(Math.random() * picks.length)]
          }
        }
        openUrl(videoUrl)
        await _finishAction(msg)
        return
      }

      if (sysAction.getSystemInfo) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const dataFetch = (window.electronAPI as any).getSystemInfo?.() ?? Promise.resolve(null)
        const raw = await _holdAndFetch(Promise.resolve(dataFetch).then(v => v).catch(() => null))
        const spoken = raw ? formatSystemInfo(raw) : "I couldn't read your system specs right now."
        await _finishAction(spoken)
        return
      }

      if (sysAction.createFolder != null) {
        const { name, path } = sysAction.createFolder
        if (name && path) {
          // Have both — create immediately
          const [, result] = await Promise.all([
            speakResponse(sysAction.response),
            fetch(`${API_BASE}/api/v1/system/create-folder`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name, path }),
            }).then(r => r.json()).catch(() => null),
          ])
          setSessionState('processing')
          let spoken: string
          if (result?.success) {
            const loc: string = result.path || path
            lastCreatedFolderRef.current = { name, path: loc }
            spoken = _failsafe('create_folder', result, `Done! I created the "${name}" folder in ${loc}. Say "open it" to open it.`)
          } else {
            spoken = _failsafe('create_folder', result, `Couldn't create the folder. Please try again.`)
          }
          await _finishAction(spoken)
        } else if (name) {
          // name known but no path extracted — shouldn't happen after defaulting to Desktop
          // but handle gracefully by asking
          pendingFolderRef.current = { stage: 'path', name }
          setSessionState('speaking')
          await speakResponse(sysAction.response)
          setSessionState('idle')
          isRunningRef.current = false
          maybeRestartListening()
        } else {
          // Need name — ask for name (and optionally location)
          pendingFolderRef.current = { stage: 'name', knownPath: path || undefined }
          setSessionState('speaking')
          await speakResponse(sysAction.response)
          setSessionState('idle')
          isRunningRef.current = false
          maybeRestartListening()
        }
        return
      }

      if (sysAction.openLastFolder) {
        if (lastCreatedFolderRef.current) {
          const { name, path } = lastCreatedFolderRef.current
          fetch(`${API_BASE}/api/v1/system/open-directory`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
          }).catch(() => {})
          await _finishAction(`Opening it now — the "${name}" folder.`)
        } else {
          await _finishAction("I don't have a recently created folder on record. Try saying 'create folder' first.")
        }
        return
      }

      // ── Desktop type ────────────────────────────────────────────────────────
      if (sysAction.desktopType) {
        ;(window.electronAPI as any).automateType?.(sysAction.desktopType)
        await _finishAction(sysAction.response)
        return
      }

      // ── Desktop hotkey ──────────────────────────────────────────────────────
      if (sysAction.desktopHotkey) {
        ;(window.electronAPI as any).automateHotkey?.(sysAction.desktopHotkey)
        await _finishAction(sysAction.response)
        return
      }

      // ── Scroll ──────────────────────────────────────────────────────────────
      if (sysAction.desktopScroll) {
        const { direction, amount } = sysAction.desktopScroll
        fetch(`${API_BASE}/api/v1/automation/scroll`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ direction, amount }),
        }).catch(() => null)
        await _finishAction(sysAction.response)
        return
      }

      if (sysAction.intent) lastActionRef.current = { intent: sysAction.intent, action: sysAction, transcript }

      // ── App open via backend (real execution, real success check) ─────────
      if (sysAction.app) {
        console.log('[TOOL] open_app | env: desktop | app:', sysAction.app)
        setSessionState('speaking')
        const [, result] = await Promise.all([
          speakResponse(sysAction.response),
          fetch(`${API_BASE}/api/v1/system/open-app`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ app: sysAction.app }),
          }).then(r => r.json()).catch(() => null),
        ])
        console.log('[TOOL] open_app result:', result)
        if (result && !result.success) {
          setSessionState('speaking')
          await speakResponse(`I couldn't open ${sysAction.app}. ${result.message ?? 'It may not be installed.'}`)
        }
        setSessionState('idle')
        isRunningRef.current = false
        maybeRestartListening()
        return
      }

      // ── Directory / drive open via backend ────────────────────────────────
      if (sysAction.path) {
        console.log('[TOOL] open_directory | env: desktop | path:', sysAction.path)
        setSessionState('speaking')
        const [, result] = await Promise.all([
          speakResponse(sysAction.response),
          fetch(`${API_BASE}/api/v1/system/open-directory`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: sysAction.path }),
          }).then(r => r.json()).catch(() => null),
        ])
        console.log('[TOOL] open_directory result:', result)
        if (result && !result.success) {
          setSessionState('speaking')
          await speakResponse(`I couldn't open that path. ${result.message ?? 'Check that the drive exists.'}`)
        }
        setSessionState('idle')
        isRunningRef.current = false
        maybeRestartListening()
        return
      }

      // ── Legacy systemCommand (fallback) ───────────────────────────────────
      if (sysAction.systemCommand) {
        console.log('[TOOL] systemCommand | env: desktop | cmd:', sysAction.systemCommand)
        launchApp(sysAction.systemCommand)
      } else if (sysAction.url) {
        openUrl(sysAction.url)
      }
      setSessionState('speaking')
      await speakResponse(sysAction.response)
      setSessionState('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // BACKEND AI — streaming
    addMsg('user', transcript)
    setSessionState('processing')
    const aId = addMsg('assistant', '', 'processing')

    const ctrl = new AbortController()
    taskCtrlRef.current = ctrl

    const { voice, speed, volume, mode: personalityMode } = readAssistantSettings()

    let chunkText = ''

    const queue = new AudioQueue({
      volume,
      onEmpty: () => {
        if (!activeRef.current || ctrl.signal.aborted) return
        isRunningRef.current = false
        setSessionState('idle')
        maybeRestartListening()
      },
    })
    queueRef.current = queue

    await _streamAndSpeak(
      transcript,
      historyRef.current.slice(-6),
      queue,
      ctrl.signal,
      voice,
      speed,
      {
        onChunk: (text, _index) => {
          if (!chunkText) setSessionState('speaking')
          chunkText += (chunkText ? ' ' : '') + text
          updMsg(aId, { text: chunkText })
        },
        onDone: (fullText) => {
          updMsg(aId, { text: fullText || chunkText, status: 'done' })
        },
        onError: (msg) => {
          if (ctrl.signal.aborted) return
          updMsg(aId, { text: msg, status: 'error' })
          queue.abort()
          isRunningRef.current = false
          setSessionState('idle')
          maybeRestartListening()
        },
        onFollowUp: (suggestion) => {
          if (suggestion) {
            setFollowUp(suggestion)
            setTimeout(() => setFollowUp(null), 8000)
          }
        },
      },
      personalityMode,
      detectedLang,
    )
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  const startSession = useCallback(async () => {
    if (sessionState !== 'idle') return
    setError(null)
    setMessages([])
    historyRef.current = []
    activeRef.current = true
    setSessionState('greeting')

    const { mode } = readAssistantSettings()
    const greeting = buildGreeting(mode)
    addMsg('assistant', greeting)

    try {
      await speakResponse(greeting)   // speakResponse already has a 30s internal timeout
    } catch { /* ok */ }

    // Short cooldown so the mic doesn't pick up the tail of the greeting audio
    await new Promise<void>((r) => setTimeout(r, 450))

    if (activeRef.current) maybeRestartListening()
  }, [sessionState, addMsg, speakResponse, maybeRestartListening])

  const stopSession = useCallback(() => {
    activeRef.current  = false
    isRunningRef.current = false
    taskCtrlRef.current?.abort()
    queueRef.current?.abort()
    stopMedia()
    setSessionState('idle')
    setMessages([])
    historyRef.current = []
  }, [stopMedia])

  const startWorkSession = useCallback(async () => {
    if (sessionState !== 'idle') return
    setError(null)
    setMessages([])
    historyRef.current = []
    activeRef.current  = true
    setSessionState('speaking')

    const greetings = [
      "Alright, I'm up. VS Code and your GitHub are ready. Let's get to work.",
      "Rise and grind. Opening VS Code and GitHub — you've got code to ship.",
      "Let's go. VS Code is up, GitHub is open. Build something great today.",
      "Work mode activated. VS Code and GitHub, ready to go. Let's build.",
      "On it. VS Code and your GitHub profile are open. Time to make things happen.",
    ]
    const spoken = greetings[Math.floor(Math.random() * greetings.length)]
    addMsg('assistant', spoken)

    console.log('[TOOL] startWorkSession | env: desktop | launching vscode + github')
    fetch(`${API_BASE}/api/v1/system/open-app`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app: 'vscode' }),
    }).then(r => r.json()).then(res => console.log('[TOOL] startWorkSession vscode:', res)).catch(() => {})
    openUrl('https://github.com/TayyabAziz11')

    try { await speakResponse(spoken) } catch { /* ok */ }

    await new Promise<void>((r) => setTimeout(r, 450))
    setSessionState('idle')
    if (activeRef.current) maybeRestartListening()
  }, [sessionState, addMsg, speakResponse, maybeRestartListening])

  const dismissFollowUp = useCallback(() => setFollowUp(null), [])

  return { sessionState, messages, error, startSession, startWorkSession, stopSession, followUp, dismissFollowUp }
}
