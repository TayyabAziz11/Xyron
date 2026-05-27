import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@desktop/stores/authStore'
import { useSubscriptionStore } from '@desktop/stores/subscriptionStore'

interface ProtectedRouteProps {
  children: React.ReactNode
  requireTier?: boolean
}

export function ProtectedRoute({ children, requireTier = true }: ProtectedRouteProps) {
  const { isLoaded, isSignedIn } = useAuthStore()
  const { tierSelectedAt } = useSubscriptionStore()

  // Still loading Clerk — show nothing (blank dark screen)
  if (!isLoaded) {
    return <div style={{ background: '#08080f', height: '100vh' }} />
  }

  if (!isSignedIn) {
    return <Navigate to="/auth" replace />
  }

  if (requireTier && !tierSelectedAt) {
    return <Navigate to="/onboarding/plan" replace />
  }

  return <>{children}</>
}
