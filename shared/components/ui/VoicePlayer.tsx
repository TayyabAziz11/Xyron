
import { Volume2, VolumeX, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

interface VoicePlayerProps {
  isSpeaking: boolean
  voiceEnabled: boolean
  onToggle: () => void
  onSpeak?: () => void
  className?: string
}

export function VoicePlayer({
  isSpeaking,
  voiceEnabled,
  onToggle,
  className,
}: VoicePlayerProps) {
  return (
    <div className={cn('flex items-center gap-2', className)}>
      {isSpeaking && (
        <div className="flex items-center gap-1.5 text-brand-light text-xs">
          <Loader2 className="w-3 h-3 animate-spin" />
          <WaveIndicator />
          <span>Speaking…</span>
        </div>
      )}
      <button
        onClick={onToggle}
        title={voiceEnabled ? 'Mute voice responses' : 'Enable voice responses'}
        className={cn(
          'p-1.5 rounded-md border transition-all duration-150',
          voiceEnabled
            ? 'border-brand/30 text-brand-light bg-brand/5 hover:bg-brand/10'
            : 'border-surface-border text-text-muted hover:text-text-primary hover:border-surface-border'
        )}
      >
        {voiceEnabled
          ? <Volume2 className="w-3.5 h-3.5" />
          : <VolumeX className="w-3.5 h-3.5" />
        }
      </button>
    </div>
  )
}

function WaveIndicator() {
  return (
    <span className="flex items-end gap-px h-3.5">
      {[0, 1, 2, 3].map(i => (
        <span
          key={i}
          className="w-0.5 bg-brand-light rounded-full"
          style={{
            height: `${40 + Math.sin(i * 1.2) * 40}%`,
            animation: 'wave 0.8s ease-in-out infinite',
            animationDelay: `${i * 0.12}s`,
          }}
        />
      ))}
      <style>{`
        @keyframes wave {
          0%, 100% { transform: scaleY(0.4); }
          50% { transform: scaleY(1); }
        }
      `}</style>
    </span>
  )
}
