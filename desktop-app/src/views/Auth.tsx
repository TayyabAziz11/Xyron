import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { useSignIn, useSignUp, useClerk } from '@clerk/clerk-react'
import { Zap, Mail, Lock, Eye, EyeOff, ArrowRight, Github, Chrome, Loader2, AlertCircle } from 'lucide-react'
import { useAuthStore } from '@desktop/stores/authStore'
import { useSubscriptionStore } from '@desktop/stores/subscriptionStore'
import { getCurrentWindow } from '@tauri-apps/api/window'

type AuthMode = 'sign_in' | 'sign_up'
type AuthStep = 'credentials' | 'verify_email'

// ── No-Clerk dev fallback ─────────────────────────────────────────────────────
function DevAuthForm({ onSuccess }: { onSuccess: () => void }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const { setUser } = useAuthStore()

  const submit = () => {
    if (!name.trim() || !email.trim()) return
    const user = {
      id: `dev_${Date.now()}`,
      email: email.trim(),
      name: name.trim(),
      imageUrl: null,
      createdAt: Date.now(),
    }
    setUser(user)
    localStorage.setItem('xyron_dev_user', JSON.stringify(user))
    onSuccess()
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-3 mb-5">
        <p className="font-mono text-[10px] text-yellow-500/80 tracking-wider">
          DEV MODE — No Clerk key set. Add VITE_CLERK_PUBLISHABLE_KEY to .env for real auth.
        </p>
      </div>
      <input
        type="text" placeholder="Your name" value={name} onChange={e => setName(e.target.value)}
        className="w-full rounded-lg bg-white/[0.04] border border-white/10 px-4 py-3 text-sm text-white placeholder-white/20 focus:outline-none focus:border-[#7c3aed]/60 transition-colors"
      />
      <input
        type="email" placeholder="Email address" value={email} onChange={e => setEmail(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && submit()}
        className="w-full rounded-lg bg-white/[0.04] border border-white/10 px-4 py-3 text-sm text-white placeholder-white/20 focus:outline-none focus:border-[#7c3aed]/60 transition-colors"
      />
      <button onClick={submit} disabled={!name.trim() || !email.trim()}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-lg bg-[#7c3aed] text-white text-sm font-semibold hover:bg-[#6d28d9] disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
        Continue <ArrowRight className="w-4 h-4" />
      </button>
    </div>
  )
}

// ── Clerk-backed auth form ────────────────────────────────────────────────────
function ClerkAuthForm({ mode, onModeChange, onSuccess }: {
  mode: AuthMode
  onModeChange: (m: AuthMode) => void
  onSuccess: () => void
}) {
  const { signIn, isLoaded: siLoaded } = useSignIn()
  const { signUp, isLoaded: suLoaded } = useSignUp()
  const clerk = useClerk()
  const { setUser } = useAuthStore()

  const [step, setStep]       = useState<AuthStep>('credentials')
  const [email, setEmail]     = useState('')
  const [password, setPassword] = useState('')
  const [name, setName]       = useState('')
  const [code, setCode]       = useState('')
  const [showPw, setShowPw]   = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)

  const emailRef = useRef<HTMLInputElement>(null)
  useEffect(() => { emailRef.current?.focus() }, [mode])

  // After setActive() resolves clerk.user is immediately available — push it into
  // authStore right now so ProtectedRoute doesn't see isSignedIn=false on navigate.
  const commitSession = async (sessionId: string | null) => {
    await clerk.setActive({ session: sessionId })
    const u = clerk.user
    if (u) {
      setUser({
        id: u.id,
        email: u.primaryEmailAddress?.emailAddress ?? '',
        name: u.fullName ?? u.firstName ?? 'User',
        imageUrl: u.imageUrl ?? null,
        createdAt: u.createdAt?.getTime() ?? Date.now(),
      })
    }
  }

  const handleOAuth = async (provider: 'oauth_google' | 'oauth_github') => {
    if (!signIn) return
    setLoading(true)
    try {
      await signIn.authenticateWithRedirect({
        strategy: provider,
        redirectUrl: 'http://localhost:1420/sso-callback',
        redirectUrlComplete: '/',
      })
    } catch (e: unknown) {
      setError((e as { message?: string }).message ?? 'OAuth failed')
      setLoading(false)
    }
  }

  const handleCredentials = async () => {
    setError(null)
    setLoading(true)
    try {
      if (mode === 'sign_in') {
        if (!signIn) return
        const result = await signIn.create({ identifier: email, password })
        if (result.status === 'complete') {
          await commitSession(result.createdSessionId)
          onSuccess()
        }
      } else {
        if (!signUp) return
        await signUp.create({
          emailAddress: email,
          password,
          firstName: name.split(' ')[0],
          lastName: name.split(' ').slice(1).join(' ') || undefined,
        })
        // Handle missing fields (e.g. username required in Clerk dashboard)
        // before sending the verification email so the code is only used once.
        const missing = (signUp.missingFields ?? []) as string[]
        if (missing.includes('username')) {
          const auto = email.split('@')[0].replace(/[^a-zA-Z0-9_]/g, '_') + '_' + Date.now().toString().slice(-4)
          await signUp.update({ username: auto })
        }
        await signUp.prepareEmailAddressVerification({ strategy: 'email_code' })
        setStep('verify_email')
      }
    } catch (e: unknown) {
      setError((e as { errors?: Array<{ message: string }> })?.errors?.[0]?.message ?? 'Authentication failed')
    } finally {
      setLoading(false)
    }
  }

  const handleVerify = async () => {
    if (!signUp) return
    setError(null)
    setLoading(true)
    try {
      const result = await signUp.attemptEmailAddressVerification({ code })
      if (result.status === 'complete') {
        await commitSession(result.createdSessionId)
        onSuccess()
      } else {
        setError('Verification failed. Please request a new code and try again.')
      }
    } catch (e: unknown) {
      setError((e as { errors?: Array<{ message: string }> })?.errors?.[0]?.message ?? 'Invalid code')
    } finally {
      setLoading(false)
    }
  }

  if (!siLoaded || !suLoaded) {
    return <div className="flex items-center justify-center h-32"><Loader2 className="w-5 h-5 text-[#7c3aed] animate-spin" /></div>
  }

  if (step === 'verify_email') {
    return (
      <div className="space-y-4">
        <div className="text-center mb-6">
          <div className="w-12 h-12 rounded-full bg-[#7c3aed]/10 border border-[#7c3aed]/20 flex items-center justify-center mx-auto mb-3">
            <Mail className="w-5 h-5 text-[#7c3aed]" />
          </div>
          <p className="text-sm text-white/60">We sent a verification code to</p>
          <p className="text-sm text-white font-medium">{email}</p>
        </div>
        <input
          type="text" placeholder="6-digit code" value={code} onChange={e => setCode(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleVerify()}
          className="w-full rounded-lg bg-white/[0.04] border border-white/10 px-4 py-3 text-sm text-white placeholder-white/20 focus:outline-none focus:border-[#7c3aed]/60 transition-colors text-center tracking-[0.5em] font-mono text-lg"
          maxLength={6}
        />
        {error && <ErrorBadge message={error} />}
        <button onClick={handleVerify} disabled={code.length < 6 || loading}
          className="w-full flex items-center justify-center gap-2 py-3 rounded-lg bg-[#7c3aed] text-white text-sm font-semibold hover:bg-[#6d28d9] disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <>Verify Email <ArrowRight className="w-4 h-4" /></>}
        </button>
        <button onClick={() => setStep('credentials')} className="w-full text-center text-xs text-white/30 hover:text-white/60 transition-colors py-1">
          Back
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* OAuth buttons */}
      <button onClick={() => handleOAuth('oauth_google')} disabled={loading}
        className="w-full flex items-center justify-center gap-3 py-2.5 rounded-lg border border-white/10 bg-white/[0.03] text-sm text-white/80 hover:bg-white/[0.07] hover:border-white/20 disabled:opacity-40 transition-colors">
        <Chrome className="w-4 h-4" />
        Continue with Google
      </button>
      <button onClick={() => handleOAuth('oauth_github')} disabled={loading}
        className="w-full flex items-center justify-center gap-3 py-2.5 rounded-lg border border-white/10 bg-white/[0.03] text-sm text-white/80 hover:bg-white/[0.07] hover:border-white/20 disabled:opacity-40 transition-colors">
        <Github className="w-4 h-4" />
        Continue with GitHub
      </button>

      {/* Divider */}
      <div className="flex items-center gap-3 my-1">
        <div className="flex-1 h-px bg-white/[0.06]" />
        <span className="text-[11px] text-white/20 tracking-widest">OR</span>
        <div className="flex-1 h-px bg-white/[0.06]" />
      </div>

      {/* Email form */}
      {mode === 'sign_up' && (
        <input
          type="text" placeholder="Full name" value={name} onChange={e => setName(e.target.value)}
          className="w-full rounded-lg bg-white/[0.04] border border-white/10 px-4 py-3 text-sm text-white placeholder-white/20 focus:outline-none focus:border-[#7c3aed]/60 transition-colors"
        />
      )}

      <div className="relative">
        <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-white/25" />
        <input
          ref={emailRef}
          type="email" placeholder="Email address" value={email} onChange={e => setEmail(e.target.value)}
          className="w-full rounded-lg bg-white/[0.04] border border-white/10 pl-10 pr-4 py-3 text-sm text-white placeholder-white/20 focus:outline-none focus:border-[#7c3aed]/60 transition-colors"
        />
      </div>

      <div className="relative">
        <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-white/25" />
        <input
          type={showPw ? 'text' : 'password'} placeholder="Password" value={password}
          onChange={e => setPassword(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleCredentials()}
          className="w-full rounded-lg bg-white/[0.04] border border-white/10 pl-10 pr-10 py-3 text-sm text-white placeholder-white/20 focus:outline-none focus:border-[#7c3aed]/60 transition-colors"
        />
        <button type="button" onClick={() => setShowPw(v => !v)}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-white/25 hover:text-white/60 transition-colors">
          {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>

      {error && <ErrorBadge message={error} />}

      <button onClick={handleCredentials} disabled={!email || !password || loading}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-lg bg-[#7c3aed] text-white text-sm font-semibold hover:bg-[#6d28d9] disabled:opacity-40 disabled:cursor-not-allowed transition-colors shadow-[0_0_20px_rgba(124,58,237,0.3)]">
        {loading
          ? <Loader2 className="w-4 h-4 animate-spin" />
          : <>{mode === 'sign_in' ? 'Sign In' : 'Create Account'} <ArrowRight className="w-4 h-4" /></>}
      </button>
    </div>
  )
}

function ErrorBadge({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg bg-red-500/8 border border-red-500/20 px-3 py-2.5">
      <AlertCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0 mt-0.5" />
      <p className="text-[12px] text-red-400 leading-relaxed">{message}</p>
    </div>
  )
}

// ── Main Auth view ────────────────────────────────────────────────────────────
const HAS_CLERK = !!import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

export default function Auth() {
  const navigate = useNavigate()
  const { isSignedIn, isLoaded } = useAuthStore()
  const { tierSelectedAt } = useSubscriptionStore()
  const [mode, setMode] = useState<AuthMode>('sign_in')
  const win = getCurrentWindow()

  // Already signed in — redirect
  useEffect(() => {
    if (!isLoaded) return
    if (isSignedIn) {
      navigate(tierSelectedAt ? '/dashboard' : '/onboarding/plan', { replace: true })
    }
  }, [isLoaded, isSignedIn, tierSelectedAt, navigate])


  const handleSuccess = () => {
    navigate(tierSelectedAt ? '/dashboard' : '/onboarding/plan', { replace: true })
  }

  return (
    <div className="relative h-screen w-screen overflow-hidden flex flex-col"
      style={{ background: 'linear-gradient(135deg, #08080f 0%, #0c0916 50%, #080b0f 100%)' }}>

      {/* Subtle grid */}
      <div className="absolute inset-0 pointer-events-none"
        style={{ backgroundImage: 'linear-gradient(rgba(124,58,237,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(124,58,237,0.03) 1px,transparent 1px)', backgroundSize: '48px 48px' }} />

      {/* Glow blob */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 w-96 h-96 rounded-full pointer-events-none"
        style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.08) 0%, transparent 70%)' }} />

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

      {/* Auth card */}
      <div className="flex-1 flex items-center justify-center px-6">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
          className="w-full max-w-sm"
        >
          {/* Logo */}
          <div className="flex flex-col items-center mb-8">
            <div className="w-12 h-12 rounded-2xl border border-[#7c3aed]/30 bg-[#7c3aed]/8 flex items-center justify-center mb-4 shadow-[0_0_24px_rgba(124,58,237,0.2)]">
              <Zap className="w-6 h-6 text-[#7c3aed]" />
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-white">Xyron</h1>
            <p className="text-sm text-white/40 mt-1">AI Operating System</p>
          </div>

          {/* Mode tabs */}
          <div className="flex rounded-lg bg-white/[0.04] border border-white/8 p-0.5 mb-6">
            {(['sign_in', 'sign_up'] as const).map(m => (
              <button key={m} onClick={() => setMode(m)}
                className={`flex-1 py-2 text-xs font-semibold rounded-md transition-all ${
                  mode === m
                    ? 'bg-[#7c3aed] text-white shadow-sm'
                    : 'text-white/40 hover:text-white/70'
                }`}>
                {m === 'sign_in' ? 'Sign In' : 'Sign Up'}
              </button>
            ))}
          </div>

          <AnimatePresence mode="wait">
            <motion.div key={mode}
              initial={{ opacity: 0, x: mode === 'sign_in' ? -8 : 8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
            >
              {HAS_CLERK
                ? <ClerkAuthForm mode={mode} onModeChange={setMode} onSuccess={handleSuccess} />
                : <DevAuthForm onSuccess={handleSuccess} />
              }
            </motion.div>
          </AnimatePresence>

          <p className="mt-6 text-center text-[11px] text-white/20">
            Your data stays local. Xyron never sells it.
          </p>
        </motion.div>
      </div>
    </div>
  )
}
