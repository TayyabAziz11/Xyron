import { motion } from 'framer-motion'
import { Lock, Sparkles, ArrowRight } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useSubscriptionStore } from '@desktop/stores/subscriptionStore'
import type { FeatureKey } from '@desktop/lib/tiers'

interface UpgradeCardProps {
  feature: FeatureKey
  label: string
  description: string
}

export function UpgradeCard({ label, description }: UpgradeCardProps) {
  const navigate = useNavigate()
  const { tier } = useSubscriptionStore()

  if (tier !== 'free') return null

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      className="relative flex flex-col gap-2 rounded-xl overflow-hidden cursor-pointer group"
      style={{
        background: 'linear-gradient(135deg, rgba(124,58,237,0.06) 0%, rgba(10,8,20,0.8) 100%)',
        border: '1px solid rgba(124,58,237,0.20)',
      }}
      onClick={() => navigate('/onboarding/plan')}
    >
      {/* Blur overlay */}
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 opacity-100 group-hover:opacity-90 transition-opacity"
        style={{ backdropFilter: 'blur(1px)', background: 'rgba(8,8,15,0.6)' }}>
        <div className="flex items-center gap-1.5 rounded-full px-3 py-1.5 bg-[#7c3aed]/10 border border-[#7c3aed]/20">
          <Lock className="w-3 h-3 text-[#7c3aed]" />
          <span className="text-[10px] font-semibold text-[#7c3aed] tracking-wider">PRO FEATURE</span>
        </div>
        <p className="text-[10px] text-white/40 text-center px-4">{description}</p>
        <div className="flex items-center gap-1 text-[10px] text-[#7c3aed] font-semibold">
          <Sparkles className="w-3 h-3" />
          Upgrade to unlock
          <ArrowRight className="w-3 h-3" />
        </div>
      </div>

      <div className="p-3 opacity-30">
        <p className="font-mono text-[10px] text-white/60 tracking-wider">{label}</p>
      </div>
    </motion.div>
  )
}

// Inline locked feature badge — use inline where a feature is partially visible
export function LockedBadge({ label }: { label: string }) {
  return (
    <div className="inline-flex items-center gap-1 rounded px-1.5 py-0.5"
      style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.15)' }}>
      <Lock className="w-2.5 h-2.5 text-[#7c3aed]" />
      <span className="text-[9px] font-mono text-[#7c3aed]/70 tracking-wider">{label} · PRO</span>
    </div>
  )
}
