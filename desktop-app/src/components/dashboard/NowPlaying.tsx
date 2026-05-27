import { useState } from 'react'
import { Play, Pause, SkipBack, SkipForward, Music2, Volume2 } from 'lucide-react'
import { motion } from 'framer-motion'

// Static music widget — no external API required
const TRACKS = [
  { title: 'Neural Drift',     artist: 'Xyron AI',   duration: '3:42' },
  { title: 'Quantum Pulse',    artist: 'SynthCore',   duration: '4:11' },
  { title: 'Deep Protocol',    artist: 'CyberWave',   duration: '5:07' },
]

export function NowPlaying() {
  const [playing, setPlaying]     = useState(false)
  const [trackIdx, setTrackIdx]   = useState(0)
  const [progress, setProgress]   = useState(38)

  const track = TRACKS[trackIdx]

  const prev = () => setTrackIdx(i => (i - 1 + TRACKS.length) % TRACKS.length)
  const next = () => setTrackIdx(i => (i + 1) % TRACKS.length)

  return (
    <div className="flex flex-col gap-3">
      <span className="x-section-label">Now Playing</span>

      {/* Album art placeholder */}
      <div className="flex items-center gap-3">
        <div className="relative w-10 h-10 rounded flex items-center justify-center flex-shrink-0"
          style={{ background: 'rgba(255,31,45,0.1)', border: '1px solid rgba(255,31,45,0.25)' }}>
          <Music2 size={16} style={{ color: '#ff1f2d' }} />
          {playing && (
            <motion.div className="absolute inset-0 rounded"
              animate={{ boxShadow: ['0 0 0px rgba(255,31,45,0)', '0 0 12px rgba(255,31,45,0.5)', '0 0 0px rgba(255,31,45,0)'] }}
              transition={{ duration: 1.5, repeat: Infinity }} />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-mono text-[10px] font-semibold text-[#e8ecf0] truncate">{track.title}</div>
          <div className="font-mono text-[8px] text-[#6b7280]">{track.artist}</div>
        </div>
        <span className="font-mono text-[8px] text-[#6b7280] flex-shrink-0">{track.duration}</span>
      </div>

      {/* Progress bar */}
      <div className="flex flex-col gap-1">
        <div className="h-1 rounded-full w-full cursor-pointer"
          style={{ background: 'rgba(255,255,255,0.06)' }}>
          <div className="h-full rounded-full"
            style={{ width: `${progress}%`, background: 'linear-gradient(90deg, #ff1f2d88, #ff1f2d)' }} />
        </div>
        <div className="flex justify-between">
          <span className="font-mono text-[7px] text-[#374151]">1:27</span>
          <span className="font-mono text-[7px] text-[#374151]">{track.duration}</span>
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center justify-center gap-4">
        <button onClick={prev} className="text-[#6b7280] hover:text-[#e8ecf0] transition-colors">
          <SkipBack size={14} />
        </button>
        <button
          onClick={() => setPlaying(p => !p)}
          className="w-8 h-8 rounded-full flex items-center justify-center transition-all"
          style={{ background: '#ff1f2d', boxShadow: playing ? '0 0 14px rgba(255,31,45,0.7)' : 'none' }}>
          {playing ? <Pause size={13} className="text-white" /> : <Play size={13} className="text-white" />}
        </button>
        <button onClick={next} className="text-[#6b7280] hover:text-[#e8ecf0] transition-colors">
          <SkipForward size={14} />
        </button>
        <Volume2 size={12} className="text-[#374151] ml-2" />
      </div>
    </div>
  )
}
