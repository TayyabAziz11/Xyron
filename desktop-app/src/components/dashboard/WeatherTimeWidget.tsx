import { useEffect, useState } from 'react'
import { MapPin, Thermometer, Cloud } from 'lucide-react'

function pad(n: number) { return n.toString().padStart(2, '0') }

function useClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

const DAYS   = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

export function WeatherTimeWidget() {
  const now     = useClock()
  const hours   = now.getHours()
  const mins    = now.getMinutes()
  const secs    = now.getSeconds()
  const ampm    = hours >= 12 ? 'PM' : 'AM'
  const h12     = hours % 12 || 12
  const dayName = DAYS[now.getDay()]
  const dateStr = `${MONTHS[now.getMonth()]} ${now.getDate()}, ${now.getFullYear()}`

  return (
    <div className="flex items-center gap-4">
      {/* Clock */}
      <div className="text-right">
        <div className="font-mono font-bold leading-none" style={{ fontSize: '1.6rem', color: '#e8ecf0' }}>
          {pad(h12)}:{pad(mins)}
          <span className="text-[#ff1f2d] ml-1" style={{ fontSize: '0.9rem' }}>{ampm}</span>
          <span className="text-[rgba(255,255,255,0.3)] ml-1" style={{ fontSize: '0.7rem' }}>:{pad(secs)}</span>
        </div>
        <div className="font-mono text-[9px] tracking-widest text-[#6b7280] mt-0.5">
          {dayName.toUpperCase()}, {dateStr.toUpperCase()}
        </div>
      </div>

      <div className="w-px h-8" style={{ background: 'rgba(255,35,45,0.2)' }} />

      {/* Location + weather stub */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-1">
          <MapPin size={10} className="text-[#ff1f2d]" />
          <span className="font-mono text-[9px] text-[#6b7280] tracking-widest">KARACHI, PK</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            <Thermometer size={10} style={{ color: '#ff8c00' }} />
            <span className="font-mono text-[10px] text-[#e8ecf0]">34°C</span>
          </div>
          <div className="flex items-center gap-1">
            <Cloud size={10} className="text-[rgba(255,255,255,0.35)]" />
            <span className="font-mono text-[9px] text-[#6b7280]">Partly Cloudy</span>
          </div>
        </div>
      </div>
    </div>
  )
}
