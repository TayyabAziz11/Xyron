import { lazy, Suspense, useEffect, useState, Component } from 'react'
import type { ReactNode, ErrorInfo } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { UIModeProvider } from '@/contexts/UIModeContext'
import { AppShell } from './components/AppShell'
import { TitleBar } from './components/TitleBar'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { useAuthStore } from './stores/authStore'
import { useSubscriptionStore } from './stores/subscriptionStore'

// ── Error boundary ────────────────────────────────────────────────────────────
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null }
  static getDerivedStateFromError(e: Error) { return { error: e } }
  componentDidCatch(e: Error, info: ErrorInfo) { console.error('[Xyron]', e, info) }
  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div style={{ background:'#08080f', color:'#ff2020', fontFamily:'monospace', padding:'2rem', minHeight:'100vh' }}>
        <div style={{ fontSize:'1.2rem', fontWeight:'bold', marginBottom:'1rem' }}>XYRON — RENDER ERROR</div>
        <div style={{ color:'#f59e0b', marginBottom:'0.5rem' }}>{(error as Error).message}</div>
        <pre style={{ color:'#475569', fontSize:'0.7rem', whiteSpace:'pre-wrap', wordBreak:'break-all' }}>
          {(error as Error).stack}
        </pre>
        <button onClick={() => { localStorage.clear(); window.location.reload() }}
          style={{ marginTop:'1.5rem', padding:'0.5rem 1.5rem', background:'#ff2020', color:'white', border:'none', borderRadius:'4px', fontFamily:'monospace', cursor:'pointer' }}>
          CLEAR &amp; RELOAD
        </button>
      </div>
    )
  }
}

// ── Lazy-loaded views ─────────────────────────────────────────────────────────
const Auth           = lazy(() => import('./views/Auth'))
const TierSelection  = lazy(() => import('./views/TierSelection'))
const OnboardingName = lazy(() => import('./views/OnboardingName'))
const Welcome        = lazy(() => import('./views/Welcome'))
const Dashboard     = lazy(() => import('./views/Dashboard'))
const CommandCenter = lazy(() => import('./views/CommandCenter'))
const Activity      = lazy(() => import('./views/Activity'))
const Approvals     = lazy(() => import('./views/Approvals'))
const History       = lazy(() => import('./views/History'))
const Integrations  = lazy(() => import('./views/Integrations'))
const Settings      = lazy(() => import('./views/Settings'))
const Stats         = lazy(() => import('./views/Stats'))
const Workflows     = lazy(() => import('./views/Workflows'))

function PageSpinner() {
  return (
    <div className="flex h-full min-h-screen items-center justify-center" style={{ background: '#08080f' }}>
      <div className="flex items-center gap-2">
        {[0, 1, 2].map(i => (
          <motion.div
            key={i}
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: '#7c3aed' }}
            animate={{ scale: [1, 1.5, 1], opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 0.8, repeat: Infinity, delay: i * 0.15 }}
          />
        ))}
      </div>
    </div>
  )
}

function PageTransition({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      className="h-full w-full"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
    >
      {children}
    </motion.div>
  )
}

// Keyboard shortcuts: 1-9 switch pages
function KeyboardNav() {
  const navigate = useNavigate()
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      const map: Record<string, string> = {
        '1': '/dashboard', '2': '/commands', '3': '/activity',
        '4': '/approvals', '5': '/history', '6': '/integrations',
        '7': '/workflows', '8': '/stats', '9': '/settings',
      }
      if (map[e.key]) navigate(map[e.key])
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate])
  return null
}

// ── Authenticated app shell with all protected routes ─────────────────────────
function AppRoutes() {
  const location = useLocation()
  return (
    <>
      <KeyboardNav />
      <TitleBar />
      <UIModeProvider>
        <AppShell>
          <AnimatePresence mode="wait" initial={false}>
            <Routes location={location} key={location.pathname}>
              <Route path="/dashboard"    element={<PageTransition><Suspense fallback={<PageSpinner />}><Dashboard /></Suspense></PageTransition>} />
              <Route path="/commands"     element={<PageTransition><Suspense fallback={<PageSpinner />}><CommandCenter /></Suspense></PageTransition>} />
              <Route path="/activity"     element={<PageTransition><Suspense fallback={<PageSpinner />}><Activity /></Suspense></PageTransition>} />
              <Route path="/approvals"    element={<PageTransition><Suspense fallback={<PageSpinner />}><Approvals /></Suspense></PageTransition>} />
              <Route path="/history"      element={<PageTransition><Suspense fallback={<PageSpinner />}><History /></Suspense></PageTransition>} />
              <Route path="/integrations" element={<PageTransition><Suspense fallback={<PageSpinner />}><Integrations /></Suspense></PageTransition>} />
              <Route path="/workflows"    element={<PageTransition><Suspense fallback={<PageSpinner />}><Workflows /></Suspense></PageTransition>} />
              <Route path="/stats"        element={<PageTransition><Suspense fallback={<PageSpinner />}><Stats /></Suspense></PageTransition>} />
              <Route path="/settings"     element={<PageTransition><Suspense fallback={<PageSpinner />}><Settings /></Suspense></PageTransition>} />
              <Route path="*"             element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </AnimatePresence>
        </AppShell>
      </UIModeProvider>
    </>
  )
}

// Always boot through the splash/welcome screen on every launch.
function RootRedirect() {
  return <Navigate to="/welcome" replace />
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Suspense fallback={<PageSpinner />}>
          <Routes>
            {/* Public */}
            <Route path="/welcome"         element={<Welcome />} />
            <Route path="/auth"            element={<Auth />} />
            <Route path="/sso-callback"    element={<SSOCallback />} />
            <Route path="/onboarding/plan" element={
              <ProtectedRoute requireTier={false}>
                <TierSelection />
              </ProtectedRoute>
            } />
            <Route path="/onboarding/name" element={
              <ProtectedRoute requireTier={false}>
                <OnboardingName />
              </ProtectedRoute>
            } />

            {/* Protected app */}
            <Route path="/*" element={
              <ProtectedRoute>
                <AppRoutes />
              </ProtectedRoute>
            } />

            {/* Root — smart redirect */}
            <Route index element={<RootRedirect />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ErrorBoundary>
  )
}

// Handles Clerk's OAuth redirect callback
function SSOCallback() {
  const navigate = useNavigate()
  const { isSignedIn } = useAuthStore()
  const { tierSelectedAt } = useSubscriptionStore()

  useEffect(() => {
    // Give Clerk a moment to process the token from the URL, then redirect
    const t = setTimeout(() => {
      navigate(isSignedIn
        ? tierSelectedAt ? '/dashboard' : '/onboarding/plan'
        : '/auth',
        { replace: true })
    }, 800)
    return () => clearTimeout(t)
  }, [isSignedIn, tierSelectedAt, navigate])

  return <PageSpinner />
}
