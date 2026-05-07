'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

interface Props { active: boolean }

const R = 26
const CIRC = 2 * Math.PI * R

function Gauge({ label, value, unit, color }: { label: string; value: number; unit: string; color: string }) {
  const dashOffset = CIRC * (1 - value / 100)
  const isHigh     = value > 75

  return (
    <div className="flex flex-col items-center gap-1.5">
      <div className="relative flex items-center justify-center" style={{ width: 72, height: 72 }}>
        <svg width="72" height="72" viewBox="0 0 68 68" style={{ transform: 'rotate(-90deg)' }}>
          {/* Track */}
          <circle cx="34" cy="34" r={R} fill="none" stroke="rgba(180,0,0,0.18)" strokeWidth="4.5" />
          {/* Fill */}
          <motion.circle
            cx="34" cy="34" r={R} fill="none"
            stroke={isHigh ? 'rgba(255,50,50,0.95)' : color}
            strokeWidth="4.5"
            strokeLinecap="round"
            strokeDasharray={CIRC}
            animate={{ strokeDashoffset: dashOffset }}
            transition={{ duration: 1, ease: 'easeOut' }}
            style={{ filter: `drop-shadow(0 0 5px ${isHigh ? 'rgba(255,0,0,0.9)' : color})` }}
          />
        </svg>
        {/* Value */}
        <div className="absolute text-center">
          <div className="text-[13px] font-mono font-bold tabular-nums leading-none"
            style={{ color: isHigh ? 'rgba(255,70,70,0.98)' : 'rgba(230,200,200,0.95)' }}>
            {value}
          </div>
          <div className="text-[8px] font-mono leading-none mt-0.5" style={{ color: 'rgba(180,100,100,0.75)' }}>{unit}</div>
        </div>
      </div>
      <div className="text-[8px] font-mono tracking-[2px] uppercase font-semibold"
        style={{ color: 'rgba(200,100,100,0.75)' }}>
        {label}
      </div>
    </div>
  )
}

function useStats() {
  const [cpu,  setCpu]  = useState(0)
  const [gpu,  setGpu]  = useState(0)
  const [ram,  setRam]  = useState(0)
  const [temp, setTemp] = useState(0)
  const [net,  setNet]  = useState(0)
  const cpuB = useRef(38 + Math.random() * 20)
  const gpuB = useRef(28 + Math.random() * 18)
  const tmpB = useRef(52 + Math.random() * 14)

  useEffect(() => {
    setCpu(Math.round(cpuB.current)); setGpu(Math.round(gpuB.current))
    setRam(Math.round(60 + Math.random() * 16)); setTemp(Math.round(tmpB.current))
    setNet(Math.round(2 + Math.random() * 8))
    const id = setInterval(() => {
      cpuB.current = Math.max(18, Math.min(88, cpuB.current + (Math.random() - 0.47) * 9))
      gpuB.current = Math.max(12, Math.min(82, gpuB.current + (Math.random() - 0.48) * 8))
      tmpB.current = Math.max(46, Math.min(82, tmpB.current + (Math.random() - 0.5) * 2))
      setCpu(Math.round(cpuB.current)); setGpu(Math.round(gpuB.current))
      setRam(Math.round(60 + Math.random() * 16)); setTemp(Math.round(tmpB.current))
      setNet(Math.round(2 + Math.random() * 8))
    }, 1400)
    return () => clearInterval(id)
  }, [])

  return { cpu, gpu, ram, temp, net }
}

function useSession() {
  const [s, setS] = useState(0)
  const t = useRef(Date.now())
  useEffect(() => {
    t.current = Date.now()
    const id = setInterval(() => setS(Math.floor((Date.now() - t.current) / 1000)), 1000)
    return () => clearInterval(id)
  }, [])
  return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`
}

function useClock() {
  const [t, setT] = useState('')
  useEffect(() => {
    const tick = () => setT(new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }))
    tick(); const id = setInterval(tick, 1000); return () => clearInterval(id)
  }, [])
  return t
}

export default function RightPanel({ active }: Props) {
  const { cpu, gpu, ram, temp, net } = useStats()
  const session = useSession()
  const clock   = useClock()

  return (
    <AnimatePresence>
      {active && (
        <motion.div
          key="right-panel"
          className="fixed right-5 top-1/2 -translate-y-1/2 z-[76]"
          style={{ width: 228 }}
          initial={{ opacity: 0, x: 50 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 50 }}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1], delay: 0.15 }}
        >
          <div
            style={{
              background: 'rgba(4,0,0,0.88)',
              border: '1px solid rgba(200,0,0,0.30)',
              borderRadius: 12,
              padding: '14px 16px',
              backdropFilter: 'blur(18px)',
              boxShadow: '0 0 40px rgba(180,0,0,0.10), 0 0 1px rgba(255,40,40,0.15) inset',
            }}
          >
            <div className="text-[8px] font-mono tracking-[4px] uppercase mb-3 pb-2 border-b"
              style={{ color: 'rgba(220,60,60,0.75)', borderColor: 'rgba(200,0,0,0.20)' }}>
              SYSTEM METRICS
            </div>

            {/* Circular gauges grid */}
            <div className="grid grid-cols-2 gap-3 mb-4">
              <Gauge label="CPU"  value={cpu}  unit="%"  color="rgba(220,70,70,0.9)"  />
              <Gauge label="GPU"  value={gpu}  unit="%"  color="rgba(200,50,100,0.9)" />
              <Gauge label="RAM"  value={ram}  unit="%"  color="rgba(170,60,180,0.9)" />
              <Gauge label="TEMP" value={temp} unit="°C" color="rgba(220,120,20,0.9)" />
            </div>

            {/* Session info */}
            <div className="border-t pt-3 space-y-2" style={{ borderColor: 'rgba(200,0,0,0.18)' }}>
              {[
                { label: 'SESSION', value: session },
                { label: 'NETWORK', value: `${net} MB/s` },
                { label: 'TIME',    value: clock },
                { label: 'MODE',    value: 'TAKEOVER' },
              ].map((row, i) => (
                <div key={i} className="flex items-center justify-between">
                  <span className="text-[8px] font-mono tracking-[2px] uppercase"
                    style={{ color: 'rgba(180,80,80,0.65)' }}>
                    {row.label}
                  </span>
                  <span className="text-[11px] font-mono tabular-nums font-semibold"
                    style={{ color: row.label === 'MODE' ? 'rgba(255,60,60,0.95)' : 'rgba(220,170,170,0.92)' }}>
                    {row.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
