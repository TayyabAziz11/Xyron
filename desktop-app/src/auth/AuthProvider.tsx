import { useEffect, useRef } from 'react'
import { ClerkProvider, useUser, useClerk } from '@clerk/clerk-react'
import { useAuthStore } from '@desktop/stores/authStore'
import { useSubscriptionStore } from '@desktop/stores/subscriptionStore'
import { runStartupSequence } from '@desktop/lib/startup'
import { syncUser } from '@desktop/services/authApi'

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined

// Syncs Clerk's user state into our Zustand store on every auth change.
function AuthSync() {
  const { user, isLoaded, isSignedIn } = useUser()
  const { setUser, setPreferredName, setLoaded } = useAuthStore()
  const syncedRef = useRef<string | null>(null)

  useEffect(() => {
    setLoaded(isLoaded)
    if (!isLoaded) return

    if (isSignedIn && user) {
      const xyronUser = {
        id: user.id,
        email: user.primaryEmailAddress?.emailAddress ?? '',
        name: user.fullName ?? user.firstName ?? 'User',
        imageUrl: user.imageUrl ?? null,
        createdAt: user.createdAt?.getTime() ?? Date.now(),
      }
      setUser(xyronUser)
      console.log('[AUTH_USER_LOADED] id=%s name=%s', xyronUser.id, xyronUser.name)

      // Best-effort backend sync — also loads preferred_name from backend store
      if (syncedRef.current !== user.id) {
        syncedRef.current = user.id
        syncUser({ id: xyronUser.id, email: xyronUser.email, name: xyronUser.name, image_url: xyronUser.imageUrl })
          .then(backendUser => {
            if (backendUser?.preferred_name) {
              setPreferredName(backendUser.preferred_name)
              console.log('[PREFERRED_NAME_LOADED] name=%s', backendUser.preferred_name)
            }
          })
          .catch(() => {})
      }
    } else {
      setUser(null)
      syncedRef.current = null
    }
  }, [isLoaded, isSignedIn, user, setUser, setPreferredName, setLoaded])

  return null
}

// Runs the startup sequence once after Clerk has loaded.
function StartupRunner() {
  const { isLoaded } = useAuthStore()

  useEffect(() => {
    if (!isLoaded) return
    runStartupSequence().catch(console.error)
  }, [isLoaded])

  return null
}

interface AuthProviderProps {
  children: React.ReactNode
}

export function AuthProvider({ children }: AuthProviderProps) {
  if (!PUBLISHABLE_KEY) {
    // Dev fallback: no Clerk key → skip auth, treat as signed-in free user.
    // Set VITE_CLERK_PUBLISHABLE_KEY in desktop-app/.env to enable real auth.
    return (
      <DevAuthFallback>
        {children}
      </DevAuthFallback>
    )
  }

  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="/auth">
      <AuthSync />
      <StartupRunner />
      {children}
    </ClerkProvider>
  )
}

// ── Dev fallback (no Clerk key configured) ───────────────────────────────────
function DevAuthFallback({ children }: { children: React.ReactNode }) {
  const { setUser, setLoaded } = useAuthStore()

  useEffect(() => {
    const stored = localStorage.getItem('xyron_dev_user')
    if (stored) {
      try { setUser(JSON.parse(stored)) } catch {}
    }
    setLoaded(true)
    runStartupSequence().catch(console.error)
  }, [setUser, setLoaded])

  return <>{children}</>
}

export function useSignOut() {
  const { signOut: storeSignOut } = useAuthStore()
  const { reset: resetSub } = useSubscriptionStore()
  // useClerk() must be called unconditionally (rules of hooks).
  // When no ClerkProvider is mounted (dev mode without key), clerk.signOut()
  // will throw — that's caught below and ignored.
  const clerk = useClerk()

  return async () => {
    // 1. Invalidate the Clerk session first.
    //    Without this, AuthSync immediately re-syncs the user back in.
    try { await clerk.signOut() } catch { /* dev mode — no ClerkProvider */ }
    // 2. Clear local stores
    storeSignOut()
    resetSub()
    localStorage.removeItem('xyron_dev_user')
    // 3. Hard reload to `/welcome` so all React state is torn down cleanly
    window.location.replace('/welcome')
  }
}
