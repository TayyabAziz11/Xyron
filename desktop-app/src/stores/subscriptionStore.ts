import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { type Tier, type FeatureKey, canAccess } from '@desktop/lib/tiers'

interface SubscriptionState {
  tier: Tier
  tierSelectedAt: number | null

  setTier: (tier: Tier) => void
  hasFeature: (feature: FeatureKey) => boolean
  requireFeature: (feature: FeatureKey) => boolean
  reset: () => void
}

export const useSubscriptionStore = create<SubscriptionState>()(
  persist(
    (set, get) => ({
      tier: 'free',
      tierSelectedAt: null,

      setTier: (tier) => set({ tier, tierSelectedAt: Date.now() }),

      hasFeature: (feature) => canAccess(get().tier, feature),

      requireFeature: (feature) => {
        const has = canAccess(get().tier, feature)
        if (!has) console.warn(`[Tier] Feature '${feature}' requires upgrade from '${get().tier}'`)
        return has
      },

      reset: () => set({ tier: 'free', tierSelectedAt: null }),
    }),
    {
      name: 'xyron-subscription',
      partialize: (s) => ({ tier: s.tier, tierSelectedAt: s.tierSelectedAt }),
    }
  )
)
