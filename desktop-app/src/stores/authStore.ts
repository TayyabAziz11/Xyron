import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// Mirrors the subset of Clerk's user we care about locally.
// Populated by the auth provider after Clerk loads.
export interface XyronUser {
  id: string
  email: string
  name: string
  imageUrl: string | null
  createdAt: number
  preferredName?: string | null  // custom Xyron nickname set during onboarding
}

interface AuthState {
  user: XyronUser | null
  isSignedIn: boolean
  isLoaded: boolean

  setUser: (user: XyronUser | null) => void
  setPreferredName: (name: string) => void
  setLoaded: (loaded: boolean) => void
  signOut: () => void

  /** Resolved display name: preferredName > firstName > email prefix > "boss" */
  getDisplayName: () => string
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      isSignedIn: false,
      isLoaded: false,

      setUser: (user) => set({ user, isSignedIn: !!user }),
      setPreferredName: (name) => {
        const user = get().user
        if (user) set({ user: { ...user, preferredName: name || null } })
      },
      setLoaded: (isLoaded) => set({ isLoaded }),
      signOut: () => set({ user: null, isSignedIn: false }),

      getDisplayName: () => {
        const user = get().user
        if (!user) return 'boss'
        if (user.preferredName) return user.preferredName
        // Extract firstName from full name
        const firstName = user.name?.split(' ')[0]
        if (firstName && firstName !== 'User') return firstName
        // Email prefix
        if (user.email) return user.email.split('@')[0]
        return 'boss'
      },
    }),
    {
      name: 'xyron-auth',
      partialize: (s) => ({ user: s.user, isSignedIn: s.isSignedIn }),
    }
  )
)
