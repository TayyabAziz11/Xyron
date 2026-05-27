import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Zap } from 'lucide-react'
import { useAuthStore } from '@desktop/stores/authStore'
import { useSubscriptionStore } from '@desktop/stores/subscriptionStore'
import { setPreferredName as apiSetPreferredName } from '@desktop/services/authApi'

const EXAMPLES = ['Tayyab', 'Tay', 'Boss', 'Aziz']

export default function OnboardingName() {
  const navigate = useNavigate()
  const { user, setPreferredName } = useAuthStore()
  const { tierSelectedAt } = useSubscriptionStore()
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  // If no tier selected, redirect to plan selection first
  useEffect(() => {
    if (!tierSelectedAt) navigate('/onboarding/plan', { replace: true })
  }, [tierSelectedAt, navigate])

  const handleSubmit = async () => {
    const trimmed = name.trim()
    if (!trimmed || loading) return
    setLoading(true)
    try {
      // Persist to local store immediately so greeting works offline
      setPreferredName(trimmed)
      // Persist to backend (best effort)
      if (user?.id) {
        await apiSetPreferredName(user.id, trimmed).catch(() => {})
      }
      console.log('[PREFERRED_NAME_LOADED] name=%s (onboarding)', trimmed)
      navigate('/dashboard', { replace: true })
    } finally {
      setLoading(false)
    }
  }

  const handleSkip = () => {
    navigate('/dashboard', { replace: true })
  }

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-[#08080f] flex flex-col items-center justify-center">
      {/* Grid overlay */}
      <div className="absolute inset-0 pointer-events-none"
        style={{ backgroundImage: 'linear-gradient(rgba(255,32,32,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(255,32,32,0.03) 1px,transparent 1px)', backgroundSize: '40px 40px' }} />

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: 'easeOut' }}
        className="relative z-10 w-full max-w-md px-8"
      >
        {/* Icon */}
        <div className="flex justify-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl border border-[rgba(255,32,32,0.4)] bg-[rgba(255,32,32,0.08)] shadow-[0_0_30px_rgba(255,32,32,0.2)]">
            <Zap className="w-7 h-7 text-[#ff2020]" />
          </div>
        </div>

        {/* Heading */}
        <h1 className="text-center text-2xl font-bold text-[#e8ecf0] mb-2 tracking-tight">
          What should Xyron call you?
        </h1>
        <p className="text-center font-mono text-[11px] text-[#6b7280] tracking-wider mb-8">
          This is how Xyron will greet and address you in voice sessions.
        </p>

        {/* Input */}
        <input
          ref={inputRef}
          type="text"
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSubmit()}
          placeholder="Enter your name or nickname…"
          maxLength={50}
          className="w-full rounded-lg bg-white/[0.04] border border-white/10 px-4 py-3 text-base text-white placeholder-white/20 focus:outline-none focus:border-[#ff2020]/50 transition-colors mb-4"
        />

        {/* Examples */}
        <div className="flex items-center gap-2 mb-6 flex-wrap">
          <span className="font-mono text-[10px] text-[#4b5563] tracking-wider">Examples:</span>
          {EXAMPLES.map(ex => (
            <button
              key={ex}
              onClick={() => setName(ex)}
              className="px-2.5 py-1 rounded-full font-mono text-[10px] tracking-wider transition-colors"
              style={{ border: '1px solid rgba(255,32,32,0.2)', color: 'rgba(255,32,32,0.7)', background: 'rgba(255,32,32,0.05)' }}
            >
              {ex}
            </button>
          ))}
        </div>

        {/* CTA */}
        <button
          onClick={handleSubmit}
          disabled={!name.trim() || loading}
          className="w-full py-3 rounded-lg font-mono text-sm font-semibold tracking-wider transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          style={{ background: '#ff2020', color: 'white', boxShadow: name.trim() ? '0 0 20px rgba(255,32,32,0.3)' : 'none' }}
        >
          {loading ? 'Saving…' : "Let's go"}
        </button>

        <button
          onClick={handleSkip}
          className="w-full mt-3 py-2 font-mono text-[11px] text-[#4b5563] hover:text-[#6b7280] tracking-wider transition-colors"
        >
          Skip for now
        </button>
      </motion.div>
    </div>
  )
}
