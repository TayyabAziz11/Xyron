import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Check, Sparkles, Building2, Zap, Lock, ArrowRight } from 'lucide-react'
import { useSubscriptionStore } from '@desktop/stores/subscriptionStore'
import { useAuthStore } from '@desktop/stores/authStore'
import { TIER_META, type Tier } from '@desktop/lib/tiers'
import { getCurrentWindow } from '@tauri-apps/api/window'

const TIER_ICONS = {
  free:       Zap,
  pro:        Sparkles,
  enterprise: Building2,
}

const TIER_COLORS = {
  free:       { border: 'rgba(255,255,255,0.10)', glow: 'none',                              accent: '#94a3b8' },
  pro:        { border: 'rgba(124,58,237,0.50)',  glow: '0 0 24px rgba(124,58,237,0.15)',   accent: '#7c3aed' },
  enterprise: { border: 'rgba(16,185,129,0.30)',  glow: 'none',                              accent: '#10b981' },
}

export default function TierSelection() {
  const navigate = useNavigate()
  const { setTier } = useSubscriptionStore()
  const { user } = useAuthStore()
  const [selecting, setSelecting] = useState<Tier | null>(null)
  const win = getCurrentWindow()

  const handleSelect = async (tier: Tier) => {
    if (TIER_META.find(t => t.id === tier)?.ctaDisabled) return
    setSelecting(tier)
    setTier(tier)
    // Route to name onboarding on first-ever login (no preferred_name yet)
    const stored = localStorage.getItem('xyron-auth')
    let hasName = false
    try { hasName = !!JSON.parse(stored ?? '{}')?.state?.user?.preferredName } catch { /* ok */ }
    setTimeout(() => navigate(hasName ? '/dashboard' : '/onboarding/name', { replace: true }), 400)
  }

  return (
    <div className="relative h-screen w-screen overflow-hidden flex flex-col"
      style={{ background: 'linear-gradient(135deg, #08080f 0%, #0c0916 50%, #080b0f 100%)' }}>

      {/* Subtle grid */}
      <div className="absolute inset-0 pointer-events-none"
        style={{ backgroundImage: 'linear-gradient(rgba(124,58,237,0.025) 1px,transparent 1px),linear-gradient(90deg,rgba(124,58,237,0.025) 1px,transparent 1px)', backgroundSize: '48px 48px' }} />

      {/* Glow */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-64 pointer-events-none"
        style={{ background: 'radial-gradient(ellipse, rgba(124,58,237,0.07) 0%, transparent 70%)' }} />

      {/* Title bar */}
      <div data-tauri-drag-region
        className="flex-shrink-0 flex items-center justify-between px-4 h-9 select-none"
        style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
        <span data-tauri-drag-region className="font-mono text-[9px] tracking-[0.3em] text-white/20 pointer-events-none">
          XYRON OS v2.0
        </span>
        <div className="flex items-center gap-0.5">
          <button onClick={() => win.minimize()} className="flex h-5 w-5 items-center justify-center rounded text-white/20 hover:bg-white/8 hover:text-white/60 transition-colors">
            <span className="text-[8px] font-bold">—</span>
          </button>
          <button onClick={() => win.close()} className="flex h-5 w-5 items-center justify-center rounded text-white/20 hover:bg-red-500/20 hover:text-red-400 transition-colors">
            <span className="text-[10px]">✕</span>
          </button>
        </div>
      </div>

      <div className="flex-1 flex flex-col items-center justify-center px-8 py-6 overflow-y-auto">

        {/* Header */}
        <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}
          className="text-center mb-8 flex-shrink-0">
          <p className="text-xs font-mono tracking-[0.3em] text-[#7c3aed] mb-2">
            WELCOME{user?.name ? `, ${user.name.toUpperCase()}` : ''}
          </p>
          <h1 className="text-2xl font-bold text-white tracking-tight">Choose Your Xyron Plan</h1>
          <p className="text-sm text-white/40 mt-1.5">Start free. Upgrade when you're ready.</p>
        </motion.div>

        {/* Tier cards */}
        <div className="w-full max-w-3xl grid grid-cols-3 gap-4 flex-shrink-0">
          {TIER_META.map((tier, i) => {
            const Icon = TIER_ICONS[tier.id]
            const colors = TIER_COLORS[tier.id]
            const isSelecting = selecting === tier.id
            const disabled = tier.ctaDisabled

            return (
              <motion.div
                key={tier.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: i * 0.08 }}
                className="relative flex flex-col rounded-2xl overflow-hidden"
                style={{
                  background: tier.highlighted
                    ? 'linear-gradient(180deg, rgba(124,58,237,0.07) 0%, rgba(10,8,20,0.9) 100%)'
                    : 'rgba(12,12,20,0.7)',
                  border: `1px solid ${colors.border}`,
                  boxShadow: colors.glow,
                  backdropFilter: 'blur(12px)',
                }}
              >
                {/* Popular badge */}
                {tier.highlighted && (
                  <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 z-10">
                    <span className="bg-[#7c3aed] text-white text-[10px] font-semibold px-3 py-0.5 rounded-full tracking-wider">
                      MOST POPULAR
                    </span>
                  </div>
                )}

                <div className="p-5 flex flex-col flex-1">
                  {/* Icon + name */}
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
                      style={{ background: `${colors.accent}14`, border: `1px solid ${colors.accent}30` }}>
                      <Icon className="w-4.5 h-4.5" style={{ color: colors.accent }} />
                    </div>
                    <div>
                      <div className="text-sm font-bold text-white">{tier.name}</div>
                      <div className="text-[10px] text-white/35">{tier.tagline}</div>
                    </div>
                  </div>

                  {/* Price */}
                  <div className="mb-4">
                    {tier.price
                      ? <span className="text-2xl font-bold text-white">{tier.price}<span className="text-sm font-normal text-white/40 ml-1">/mo</span></span>
                      : <span className="text-sm font-semibold" style={{ color: colors.accent }}>Pricing TBD</span>
                    }
                  </div>

                  {/* Status badge */}
                  {tier.status === 'coming_soon' && (
                    <div className="mb-3 flex items-center gap-1.5">
                      <Lock className="w-3 h-3 text-white/30" />
                      <span className="text-[10px] font-mono tracking-wider text-white/30">COMING SOON</span>
                    </div>
                  )}

                  {/* Features */}
                  <ul className="space-y-2 flex-1 mb-5">
                    {tier.features.map(f => (
                      <li key={f} className="flex items-start gap-2.5">
                        <Check className="w-3.5 h-3.5 flex-shrink-0 mt-0.5"
                          style={{ color: disabled ? '#374151' : colors.accent }} />
                        <span className={`text-xs leading-relaxed ${disabled ? 'text-white/25' : 'text-white/65'}`}>
                          {f}
                        </span>
                      </li>
                    ))}
                  </ul>

                  {/* CTA */}
                  <button
                    onClick={() => handleSelect(tier.id)}
                    disabled={disabled || !!selecting}
                    className={`
                      w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold transition-all
                      ${disabled
                        ? 'bg-white/[0.04] text-white/25 cursor-not-allowed border border-white/8'
                        : isSelecting
                          ? 'opacity-60 cursor-wait'
                          : tier.highlighted
                            ? 'bg-[#7c3aed] text-white hover:bg-[#6d28d9] shadow-[0_0_16px_rgba(124,58,237,0.35)]'
                            : 'bg-white/[0.07] text-white/80 hover:bg-white/[0.12] border border-white/10'
                      }
                    `}
                  >
                    {isSelecting
                      ? <span className="text-xs">Loading...</span>
                      : <>
                          {tier.ctaLabel}
                          {!disabled && <ArrowRight className="w-3.5 h-3.5" />}
                        </>
                    }
                  </button>
                </div>
              </motion.div>
            )
          })}
        </div>

        <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 }}
          className="mt-6 text-center text-[11px] text-white/20 flex-shrink-0">
          No credit card required for Free tier. Cancel anytime.
        </motion.p>
      </div>
    </div>
  )
}
