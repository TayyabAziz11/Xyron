import { Zap, Sparkles, Building2, ArrowRight } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useSubscriptionStore } from '@desktop/stores/subscriptionStore'
import type { Tier } from '@desktop/lib/tiers'

const TIER_CONFIG: Record<Tier, { icon: typeof Zap; label: string; color: string; bg: string; showUpgrade: boolean }> = {
  free: {
    icon: Zap,
    label: 'Free',
    color: '#94a3b8',
    bg: 'rgba(148,163,184,0.08)',
    showUpgrade: true,
  },
  pro: {
    icon: Sparkles,
    label: 'Pro',
    color: '#7c3aed',
    bg: 'rgba(124,58,237,0.1)',
    showUpgrade: false,
  },
  enterprise: {
    icon: Building2,
    label: 'Enterprise',
    color: '#10b981',
    bg: 'rgba(16,185,129,0.1)',
    showUpgrade: false,
  },
}

export function TierBadge() {
  const { tier } = useSubscriptionStore()
  const navigate = useNavigate()
  const cfg = TIER_CONFIG[tier]
  const Icon = cfg.icon

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold tracking-wider"
        style={{ background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.color}22` }}>
        <Icon className="w-3 h-3" />
        {cfg.label.toUpperCase()}
      </div>
      {cfg.showUpgrade && (
        <button
          onClick={() => navigate('/onboarding/plan')}
          className="flex items-center gap-1 text-[10px] text-[#7c3aed] hover:text-[#a78bfa] transition-colors"
        >
          Upgrade <ArrowRight className="w-3 h-3" />
        </button>
      )}
    </div>
  )
}
