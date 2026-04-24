import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Volume2, Mic, Zap, User, Info, Play, Check } from 'lucide-react'
import { useAssistantSettings, VOICE_OPTIONS, MODE_OPTIONS, MODE_VOICE_MAP, type OpenAIVoice } from '../hooks/useAssistantSettings'

function Toggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <motion.button
      onClick={onToggle}
      className={`relative w-11 h-6 rounded-full transition-colors duration-300 ${on ? 'bg-brand' : 'bg-border'}`}
      whileTap={{ scale: 0.95 }}
    >
      <motion.span
        layout
        transition={{ type: 'spring', stiffness: 500, damping: 35 }}
        className="absolute top-1 w-4 h-4 rounded-full bg-white shadow-md"
        style={{ left: on ? 'calc(100% - 20px)' : '4px' }}
      />
    </motion.button>
  )
}

function SectionHeader({ title, icon: Icon }: { title: string; icon: React.ElementType }) {
  return (
    <div className="flex items-center gap-2.5 mb-4">
      <div className="w-7 h-7 rounded-lg bg-brand/15 border border-brand/25 flex items-center justify-center">
        <Icon size={13} className="text-brand-light" />
      </div>
      <h2 className="text-[13px] font-semibold text-slate-200">{title}</h2>
    </div>
  )
}

export default function Settings() {
  const { settings, saveSettings } = useAssistantSettings()
  const [previewVoice, setPreviewVoice] = useState<OpenAIVoice | null>(null)
  const [saved, setSaved] = useState(false)

  const testVoice = async (voice: OpenAIVoice) => {
    setPreviewVoice(voice)
    const msgs: Record<OpenAIVoice, string> = {
      nova:    "Hey there! Nova here — how can I help?",
      alloy:   "Hi, I'm Alloy. Nice to meet you.",
      echo:    "Echo speaking. What can I do for you?",
      fable:   "Fable here — ready when you are.",
      onyx:    "Onyx at your service. Let's get to work.",
      shimmer: "Hi! Shimmer here. What do you need?",
    }
    try {
      const r = await fetch('http://localhost:8000/api/v1/voice/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: msgs[voice] ?? `I'm ${voice}.`, voice, speed: settings.speed }),
      })
      if (r.ok) {
        const blob = await r.blob()
        const url  = URL.createObjectURL(blob)
        const audio = new Audio(url)
        audio.volume = settings.volume
        audio.onended = () => { URL.revokeObjectURL(url); setPreviewVoice(null) }
        audio.onerror = () => { URL.revokeObjectURL(url); setPreviewVoice(null) }
        await audio.play()
      } else {
        setPreviewVoice(null)
      }
    } catch { setPreviewVoice(null) }
  }

  return (
    <div className="h-full flex flex-col bg-bg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-border/50 bg-surface/20 backdrop-blur-md flex-shrink-0">
        <div>
          <h1 className="text-[13px] font-semibold text-slate-200">Settings</h1>
          <p className="text-[10px] text-slate-600 mt-0.5">Customize your Xyron experience</p>
        </div>
        <AnimatePresence mode="wait">
          {saved ? (
            <motion.div
              key="saved"
              initial={{ opacity: 0, scale: 0.85 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.85 }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-ok/15 border border-ok/25 text-ok text-xs font-semibold"
            >
              <Check size={11} /> Saved!
            </motion.div>
          ) : (
            <motion.button
              key="btn"
              initial={{ opacity: 0, scale: 0.85 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.85 }}
              onClick={() => { setSaved(true); setTimeout(() => setSaved(false), 2000) }}
              whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
              className="px-3 py-1.5 rounded-lg bg-brand/15 border border-brand/30 text-brand-light text-xs font-semibold hover:bg-brand/25 transition-all"
            >
              Save
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">

        {/* ── Personality Mode ── */}
        <motion.section
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          className="glass-card p-4"
        >
          <SectionHeader title="Personality Mode" icon={User} />
          <div className="grid grid-cols-2 gap-2">
            {MODE_OPTIONS.map((m) => {
              const isSelected = settings.mode === m.id
              const autoVoice  = MODE_VOICE_MAP[m.id]
              return (
                <motion.button
                  key={m.id}
                  onClick={() => saveSettings({ mode: m.id })}
                  whileHover={{ y: -1 }} whileTap={{ scale: 0.97 }}
                  className={`flex flex-col gap-1.5 p-3 rounded-xl border text-left transition-all duration-200 ${
                    isSelected
                      ? 'bg-brand/12 border-brand/40 shadow-lg shadow-brand/8'
                      : 'bg-card/30 border-border/40 hover:border-border-hi hover:bg-card/50'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xl leading-none">{m.emoji}</span>
                    {isSelected && (
                      <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }}>
                        <Check size={11} className="text-brand-light" />
                      </motion.div>
                    )}
                  </div>
                  <div>
                    <div className={`text-xs font-semibold leading-tight ${isSelected ? 'text-slate-100' : 'text-slate-400'}`}>
                      {m.label}
                    </div>
                    <div className="text-[10px] text-slate-600 mt-0.5 leading-tight">{m.desc}</div>
                  </div>
                  <div className="text-[9px] text-slate-700 font-mono">
                    Voice: {autoVoice}
                  </div>
                </motion.button>
              )
            })}
          </div>
          <p className="text-[10px] text-slate-700 mt-2.5 text-center">
            Switching modes auto-updates the voice
          </p>
        </motion.section>

        {/* ── Voice Output ── */}
        <motion.section
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}
          className="glass-card p-4"
        >
          <SectionHeader title="Voice Output" icon={Volume2} />

          {/* Voice enabled toggle */}
          <div className="flex items-center justify-between py-2.5 border-b border-border/30 mb-4">
            <div>
              <p className="text-[13px] text-slate-300">Spoken replies</p>
              <p className="text-[10px] text-slate-600 mt-0.5">AI speaks responses via TTS</p>
            </div>
            <Toggle on={settings.voiceEnabled} onToggle={() => saveSettings({ voiceEnabled: !settings.voiceEnabled })} />
          </div>

          {/* Voice picker */}
          <p className="text-[10px] text-slate-500 uppercase tracking-widest font-medium mb-2.5">Voice Model</p>
          <div className="grid grid-cols-3 gap-1.5 mb-4">
            {VOICE_OPTIONS.map((v) => {
              const isSelected  = settings.voice === v.id
              const isPreviewing = previewVoice === v.id
              return (
                <motion.div
                  key={v.id}
                  whileHover={{ y: -1 }} whileTap={{ scale: 0.96 }}
                  className={`relative p-2.5 rounded-xl border cursor-pointer transition-all duration-200 ${
                    isSelected
                      ? 'bg-brand/12 border-brand/40'
                      : 'bg-card/30 border-border/40 hover:border-border-hi'
                  }`}
                  onClick={() => saveSettings({ voice: v.id })}
                >
                  <div className={`text-xs font-semibold mb-0.5 ${isSelected ? 'text-brand-light' : 'text-slate-400'}`}>
                    {v.label}
                  </div>
                  <div className="text-[9px] text-slate-600 leading-tight mb-1.5">{v.desc}</div>
                  <button
                    onClick={(e) => { e.stopPropagation(); testVoice(v.id) }}
                    className="flex items-center gap-1 text-[9px] text-slate-600 hover:text-brand-light transition-colors"
                  >
                    {isPreviewing ? (
                      <span className="flex gap-0.5 items-end">
                        {[0,1,2].map(i => (
                          <motion.span key={i} className="w-0.5 rounded-full bg-brand-light"
                            animate={{ height: ['4px','10px','4px'] }}
                            transition={{ duration: 0.5, repeat: Infinity, delay: i * 0.15 }}
                          />
                        ))}
                      </span>
                    ) : <Play size={8} />}
                    {isPreviewing ? 'Playing' : 'Preview'}
                  </button>
                  {isSelected && (
                    <div className="absolute top-2 right-2 w-1.5 h-1.5 rounded-full bg-brand-light" />
                  )}
                </motion.div>
              )
            })}
          </div>

          {/* Speed */}
          <div className="mb-3">
            <div className="flex items-center justify-between mb-2">
              <p className="text-[10px] text-slate-500 uppercase tracking-widest font-medium">Speed</p>
              <span className="text-[10px] font-mono text-brand-light bg-brand/10 px-2 py-0.5 rounded-full">
                {settings.speed.toFixed(2)}×
              </span>
            </div>
            <input type="range" min={0.75} max={1.5} step={0.05} value={settings.speed}
              style={{ '--range-progress': `${((settings.speed - 0.75) / 0.75) * 100}%` } as React.CSSProperties}
              onChange={(e) => saveSettings({ speed: parseFloat(e.target.value) })}
              className="w-full"
            />
            <div className="flex justify-between text-[9px] text-slate-700 mt-1">
              <span>0.75×</span><span>1.0×</span><span>1.5×</span>
            </div>
          </div>

          {/* Volume */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-[10px] text-slate-500 uppercase tracking-widest font-medium">Volume</p>
              <span className="text-[10px] font-mono text-brand-light bg-brand/10 px-2 py-0.5 rounded-full">
                {Math.round(settings.volume * 100)}%
              </span>
            </div>
            <input type="range" min={0} max={1} step={0.05} value={settings.volume}
              style={{ '--range-progress': `${settings.volume * 100}%` } as React.CSSProperties}
              onChange={(e) => saveSettings({ volume: parseFloat(e.target.value) })}
              className="w-full"
            />
          </div>
        </motion.section>

        {/* ── Microphone ── */}
        <motion.section
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}
          className="glass-card p-4"
        >
          <SectionHeader title="Microphone" icon={Mic} />
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: 'Echo Cancel',    val: '✓ On',   color: 'text-ok' },
              { label: 'Noise Suppress', val: '✓ On',   color: 'text-ok' },
              { label: 'Sample Rate',    val: '16 kHz', color: 'text-brand-light' },
            ].map(({ label, val, color }) => (
              <div key={label} className="bg-card/50 border border-border/40 rounded-xl p-2.5 text-center">
                <div className={`text-xs font-semibold ${color}`}>{val}</div>
                <div className="text-slate-600 text-[10px] mt-0.5">{label}</div>
              </div>
            ))}
          </div>
        </motion.section>

        {/* ── Backend ── */}
        <motion.section
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}
          className="glass-card p-4"
        >
          <SectionHeader title="Backend Connection" icon={Zap} />
          <div className="space-y-0">
            {[
              { label: 'API URL',     val: 'localhost:8000' },
              { label: 'STT Engine', val: 'Whisper-1' },
              { label: 'TTS Engine', val: 'OpenAI TTS' },
              { label: 'AI Model',   val: 'GPT-4o' },
            ].map(({ label, val }) => (
              <div key={label} className="flex justify-between items-center py-2 border-b border-border/25 last:border-0">
                <span className="text-[11px] text-slate-500">{label}</span>
                <span className="text-[11px] font-mono text-slate-400 bg-card/60 px-2 py-0.5 rounded">{val}</span>
              </div>
            ))}
          </div>
        </motion.section>

        {/* ── About ── */}
        <motion.section
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}
          className="glass-card p-4"
        >
          <SectionHeader title="About" icon={Info} />
          <div className="flex items-center gap-3 mb-4 p-3 rounded-xl bg-gradient-to-r from-brand/10 to-violet-900/10 border border-brand/20">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-brand to-violet-500 flex items-center justify-center shadow-lg shadow-brand/30 text-lg flex-shrink-0">
              🤖
            </div>
            <div>
              <div className="text-sm font-bold gradient-text">Xyron</div>
              <div className="text-[10px] text-slate-500">Voice-Driven AI OS · v1.0.0</div>
            </div>
          </div>
          {[
            { label: 'Stack',     val: 'Electron + React + Vite' },
            { label: 'Animations', val: 'Framer Motion' },
            { label: 'Developer', val: 'Tayyab Aziz' },
          ].map(({ label, val }) => (
            <div key={label} className="flex justify-between py-1.5 border-b border-border/25 last:border-0">
              <span className="text-[11px] text-slate-600">{label}</span>
              <span className="text-[11px] text-slate-400">{val}</span>
            </div>
          ))}
        </motion.section>

        <div className="h-4" />
      </div>
    </div>
  )
}
