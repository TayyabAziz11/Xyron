'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Mic, Terminal, Clock, ArrowRight, Sparkles,
  CheckCircle2, XCircle, Loader2, Volume2, VolumeX, Zap, Brain,
  FileText, Radio, UserCircle2, Wifi, WifiOff, Moon, Coffee,
} from 'lucide-react'
import { PageTransition } from '@/components/layout/PageTransition'
import { ConversationThread } from '@/components/voice/ConversationThread'
import { VoiceSessionPanel } from '@/components/voice/VoiceSessionPanel'
import { DraftPreview } from '@/components/command/DraftPreview'
import { TaskProgressPanel } from '@/components/tasks/TaskProgressPanel'
import { SystemInfoPanel } from '@/components/system/SystemInfoPanel'
import { FollowUpChip } from '@/components/voice/FollowUpChip'
import { MacrosPanel } from '@/components/voice/MacrosPanel'
import { NotesPanel } from '@/components/voice/NotesPanel'
import { MeetingPanel } from '@/components/voice/MeetingPanel'
import { ProfileSwitcher } from '@/components/voice/ProfileSwitcher'
import { ProactiveToast } from '@/components/system/ProactiveToast'
import { useVoiceSession } from '@/hooks/useVoiceSession'
import { useWakeWord } from '@/hooks/useWakeWord'
import { useReminders } from '@/hooks/useReminders'
import { useAssistantSettings } from '@/hooks/useAssistantSettings'
import { useCommands } from '@/hooks/useCommands'
import { useVoice } from '@/hooks/useVoice'
import { useDrafts } from '@/hooks/useDrafts'
import { useTasks } from '@/hooks/useTasks'
import { useProactive } from '@/hooks/useProactive'
import type { Command, Draft } from '@/lib/types'

// ── Helpers ───────────────────────────────────────────────────────────────────

const relTime = (iso: string) => {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60)   return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  completed: <CheckCircle2 className="h-3.5 w-3.5 text-status-success" />,
  failed:    <XCircle      className="h-3.5 w-3.5 text-status-error" />,
  running:   <Loader2      className="h-3.5 w-3.5 text-status-pending animate-spin" />,
  queued:    <Loader2      className="h-3.5 w-3.5 text-text-muted animate-spin" />,
}

const STATUS_COLOR: Record<string, string> = {
  completed: 'text-status-success bg-status-success/10 border-status-success/20',
  failed:    'text-status-error   bg-status-error/10   border-status-error/20',
  running:   'text-status-pending bg-status-pending/10 border-status-pending/20',
  queued:    'text-text-muted     bg-surface-overlay   border-surface-border',
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SessionBadge({ sessionActive }: { sessionActive: boolean }) {
  if (!sessionActive) return null
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0 }}
      className="inline-flex items-center gap-1.5 rounded-full border border-status-success/30 bg-status-success/10 px-3 py-1 text-xs font-medium text-status-success"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-status-success animate-pulse" />
      Voice session active
    </motion.div>
  )
}

function ChillModeBanner({ onWakeUp }: { onWakeUp: () => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.25 }}
      className="flex items-center gap-3 rounded-xl border border-purple-500/30 bg-purple-500/10 px-4 py-3"
    >
      <Moon className="h-4 w-4 shrink-0 text-purple-400 animate-pulse" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-purple-300">Chill mode active</p>
        <p className="text-xs text-purple-400/70 mt-0.5">Relaxed voice · YouTube open · Ask for recommendations</p>
      </div>
      <Coffee className="h-3.5 w-3.5 text-purple-400/50 shrink-0" />
      <button
        onClick={onWakeUp}
        className="shrink-0 rounded-lg border border-purple-500/30 bg-purple-500/20 px-3 py-1 text-xs font-medium text-purple-300 hover:bg-purple-500/30 transition-colors"
      >
        Wake up
      </button>
    </motion.div>
  )
}

function CommandHistoryItem({ cmd, onRerun }: { cmd: Command; onRerun: (text: string) => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      className="group flex items-center gap-3 rounded-lg border border-surface-border bg-surface-raised px-4 py-3 hover:border-brand/30 transition-colors cursor-pointer"
      onClick={() => onRerun(cmd.text)}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm text-text-primary truncate">{cmd.text}</p>
        <p className="text-xs text-text-muted mt-0.5">{relTime(cmd.created_at)}</p>
      </div>
      <span className={`shrink-0 flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_COLOR[cmd.status] || STATUS_COLOR.queued}`}>
        {STATUS_ICON[cmd.status]}
        {cmd.status}
      </span>
      <ArrowRight className="h-3.5 w-3.5 text-text-muted opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
    </motion.div>
  )
}

// ── Manual text input (fallback / complement to voice) ─────────────────────

function TextCommandBar({ onSubmit, loading }: { onSubmit: (t: string) => void; loading?: boolean }) {
  const [text, setText] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const t = text.trim()
    if (!t || loading) return
    onSubmit(t)
    setText('')
  }

  return (
    <form onSubmit={handleSubmit} className="flex items-center gap-2">
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Or type a command and press Enter…"
        disabled={loading}
        className="flex-1 rounded-lg border border-surface-border bg-surface-overlay px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-brand/50 focus:outline-none focus:ring-1 focus:ring-brand/20 transition-colors disabled:opacity-50"
      />
      <button
        type="submit"
        disabled={!text.trim() || loading}
        className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-light transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
        Run
      </button>
    </form>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function CommandCenterPage() {
  const session  = useVoiceSession()
  // Wake word: start session if not running. During active session the system
  // auto-listens — wake word can also trigger a listen cycle as a backup (guards protect).
  const wakeActivate = useCallback(() => {
    if (session.isActive) session.startListening()
    else session.startSession()
  }, [session])

  const { supported: wakeSupported, listening: wakeListening } = useWakeWord(
    wakeActivate,
    session.state === 'idle',   // only run wake word loop when idle
  )
  const { data: commands, submit, submitting, lastResult, refetch } = useCommands()
  const { settings, saveSettings, speak, stop, isSpeaking } = useVoice()
  const { getDraft, confirmDraft, rejectDraft, editDraft, executing } = useDrafts()
  const { tasks, activeTasks, submitTask, cancelTask } = useTasks()

  // Proactive reminders — polls backend every 5s and speaks fired reminders aloud
  const { settings: aSettings, saveSettings: saveASettings } = useAssistantSettings()
  useReminders({
    active: session.isActive,
    voice:  aSettings.voice,
    speed:  aSettings.speed,
    volume: aSettings.volume,
    onFire: (_message) => { /* TTS handled inside useReminders */ },
  })

  const [activeTab,    setActiveTab]    = useState<'voice' | 'history' | 'tasks' | 'macros' | 'notes' | 'meeting'>('voice')

  // Proactive notifications (Feature #7)
  const { notifications, dismiss: dismissNotif } = useProactive()
  const [currentDraft, setCurrentDraft] = useState<Draft | null>(null)
  const spokenIdRef = useRef<string | null>(null)

  // Auto-play voice for non-session commands
  useEffect(() => {
    if (!lastResult || lastResult.status !== 'completed') return
    if (!lastResult.assistant_response) return
    if (spokenIdRef.current === lastResult.id) return
    if (!settings.autoPlay) return
    if (session.isActive || session.state !== 'idle') return   // session handles its own TTS
    spokenIdRef.current = lastResult.id
    speak(lastResult.assistant_response)
  }, [lastResult?.id, lastResult?.status, lastResult?.assistant_response, settings.autoPlay, speak, session.isActive])

  // Draft watcher
  useEffect(() => {
    if (!lastResult?.draft_id || lastResult.status !== 'completed') return
    getDraft(lastResult.draft_id).then((d) => { if (d) setCurrentDraft(d) })
  }, [lastResult?.id, lastResult?.draft_id, lastResult?.status, getDraft])

  // Action URL / app handler for text commands
  useEffect(() => {
    if (!lastResult || lastResult.status !== 'completed') return
    if (lastResult.action_url) {
      window.open(lastResult.action_url, '_blank', 'noopener')
    }
    if (lastResult.action_app) {
      const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
      fetch(`${API_BASE}/api/v1/system/open-app`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ app: lastResult.action_app }),
      }).catch(() => {/* non-fatal */})
    }
  }, [lastResult?.id, lastResult?.status])

  const handleTextSubmit = useCallback(async (text: string) => {
    setCurrentDraft(null)
    await submit(text)
    refetch()
  }, [submit, refetch])

  const handleRerun = useCallback((text: string) => {
    setActiveTab('voice')
    handleTextSubmit(text)
  }, [handleTextSubmit])

  const handleConfirmDraft = async (id: string) => {
    const r = await confirmDraft(id)
    if (r.spoken && settings.enabled) speak(r.spoken)
    const updated = await getDraft(id)
    if (updated) setCurrentDraft(updated)
  }

  const handleRejectDraft = async (id: string) => {
    await rejectDraft(id)
    const updated = await getDraft(id)
    if (updated) setCurrentDraft(updated)
  }

  const handleEditDraft = async (id: string, content: string) => {
    await editDraft(id, content)
    const updated = await getDraft(id)
    if (updated) setCurrentDraft(updated)
  }

  return (
    <PageTransition>
      <div className="max-w-5xl mx-auto space-y-6">

        {/* ── Page header ─────────────────────────────────────────────── */}
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-brand-light" />
              Command Center
            </h1>
            <p className="text-sm text-text-muted mt-0.5">
              Voice-first AI assistant with continuous conversation
            </p>
          </div>

          <div className="flex items-center gap-3">
            <AnimatePresence>
              <SessionBadge sessionActive={session.isActive} />
            </AnimatePresence>

            {/* Voice toggle */}
            <button
              onClick={() => {
                if (isSpeaking) stop()
                saveSettings({ enabled: !settings.enabled })
              }}
              title={settings.enabled ? 'Mute voice' : 'Enable voice'}
              className={[
                'flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors',
                settings.enabled
                  ? 'border-brand/30 bg-brand/10 text-brand-light hover:bg-brand/20'
                  : 'border-surface-border text-text-muted hover:text-text-secondary hover:bg-surface-raised',
              ].join(' ')}
            >
              {settings.enabled
                ? <Volume2  className="h-3.5 w-3.5" />
                : <VolumeX  className="h-3.5 w-3.5" />}
              {settings.enabled ? 'Voice on' : 'Voice off'}
            </button>
          </div>
        </div>

        {/* ── Main layout ──────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">

          {/* Left: voice session panel + conversation */}
          <div className="lg:col-span-3 flex flex-col gap-4">

            {/* Tab bar — row 1 */}
            <div className="flex rounded-lg border border-surface-border bg-surface-raised p-1 gap-1">
              {([
                ['voice',   Mic,         'Voice'],
                ['history', Clock,       'History'],
                ['tasks',   Zap,         'Tasks'],
                ['macros',  Brain,       'Macros'],
                ['notes',   FileText,    'Notes'],
                ['meeting', Radio,       'Meeting'],
              ] as const).map(([tab, Icon, label]) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={[
                    'relative flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-2 text-xs font-medium transition-colors',
                    activeTab === tab
                      ? 'bg-surface-overlay text-text-primary shadow-sm'
                      : 'text-text-muted hover:text-text-secondary',
                  ].join(' ')}
                >
                  <Icon className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">{label}</span>
                  {tab === 'tasks' && activeTasks.length > 0 && (
                    <span className="absolute -top-1 -right-1 h-4 w-4 rounded-full bg-brand text-white text-[10px] flex items-center justify-center font-bold">
                      {activeTasks.length}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Tab content */}
            <AnimatePresence mode="wait">
              {activeTab === 'voice' && (
                <motion.div
                  key="voice"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.18 }}
                  className="flex flex-col gap-4"
                >
                  {/* Chill mode banner */}
                  <AnimatePresence>
                    {aSettings.mode === 'chill' && (
                      <ChillModeBanner
                        onWakeUp={() => saveASettings({ mode: 'assistant', voice: 'nova' })}
                      />
                    )}
                  </AnimatePresence>

                  {/* Offline mode indicator (Feature #8) */}
                  {session.offlineMode && (
                    <div className="flex items-center gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-xs text-yellow-400">
                      <WifiOff className="h-3.5 w-3.5 shrink-0" />
                      Offline mode — responding via local LLM
                    </div>
                  )}

                  {/* Follow-up suggestion chip (Feature #3) */}
                  <FollowUpChip
                    suggestion={session.followUp}
                    onDismiss={session.dismissFollowUp}
                    onAccept={(text) => {
                      session.dismissFollowUp()
                      // Trigger a new voice query with the suggestion text
                      handleTextSubmit(text)
                    }}
                  />

                  {/* Voice controls */}
                  <div className="rounded-xl border border-surface-border bg-surface-raised p-6">
                    <VoiceSessionPanel
                      state={session.state}
                      sessionActive={session.isActive}
                      onStart={session.startSession}
                      onStop={session.stopSession}
                      error={session.error}
                      wakeWordListening={wakeListening}
                      wakeWordSupported={wakeSupported}
                    />
                  </div>

                  {/* Conversation thread */}
                  {(session.messages.length > 0 || session.state !== 'idle') && (
                    <div className="rounded-xl border border-surface-border bg-surface-raised overflow-hidden">
                      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border">
                        <span className="text-xs font-medium uppercase tracking-wider text-text-muted">
                          Conversation
                        </span>
                        {session.messages.length > 0 && (
                          <button
                            onClick={session.clearMessages}
                            className="text-xs text-text-muted hover:text-text-secondary transition-colors"
                          >
                            Clear
                          </button>
                        )}
                      </div>
                      <div className="max-h-[420px] overflow-y-auto px-4">
                        <ConversationThread messages={session.messages} />
                      </div>
                    </div>
                  )}

                  {/* Text input fallback */}
                  <div className="rounded-xl border border-surface-border bg-surface-raised p-4">
                    <p className="text-xs font-medium uppercase tracking-wider text-text-muted mb-3 flex items-center gap-1.5">
                      <Terminal className="h-3.5 w-3.5" />
                      Text command
                    </p>
                    <TextCommandBar onSubmit={handleTextSubmit} loading={submitting} />
                  </div>
                </motion.div>
              )}

              {activeTab === 'history' && (
                <motion.div
                  key="history"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.18 }}
                  className="rounded-xl border border-surface-border bg-surface-raised overflow-hidden"
                >
                  <div className="px-4 py-3 border-b border-surface-border">
                    <span className="text-xs font-medium uppercase tracking-wider text-text-muted">
                      Recent commands
                    </span>
                  </div>
                  <div className="max-h-[560px] overflow-y-auto p-4">
                    {!commands?.length ? (
                      <div className="flex flex-col items-center justify-center py-10 text-center">
                        <Terminal className="h-8 w-8 text-text-muted mb-3 opacity-40" />
                        <p className="text-sm text-text-muted">No commands yet</p>
                      </div>
                    ) : (
                      <div className="flex flex-col gap-2">
                        {commands.slice(0, 20).map((cmd) => (
                          <CommandHistoryItem key={cmd.id} cmd={cmd} onRerun={handleRerun} />
                        ))}
                      </div>
                    )}
                  </div>
                </motion.div>
              )}

              {activeTab === 'tasks' && (
                <motion.div
                  key="tasks"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.18 }}
                  className="rounded-xl border border-surface-border bg-surface-raised overflow-hidden"
                >
                  <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border">
                    <span className="text-xs font-medium uppercase tracking-wider text-text-muted flex items-center gap-1.5">
                      <Zap className="h-3.5 w-3.5" />
                      Multi-step tasks
                    </span>
                    {activeTasks.length > 0 && (
                      <span className="rounded-full border border-brand/30 bg-brand/10 px-2 py-0.5 text-xs font-medium text-brand-light">
                        {activeTasks.length} active
                      </span>
                    )}
                  </div>
                  <div className="p-4">
                    <TaskProgressPanel
                      tasks={tasks}
                      onCancel={cancelTask}
                      onSubmit={(goal) => submitTask(goal)}
                    />
                  </div>
                </motion.div>
              )}

              {activeTab === 'macros' && (
                <motion.div
                  key="macros"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.18 }}
                  className="rounded-xl border border-surface-border bg-surface-raised p-4"
                >
                  <MacrosPanel />
                </motion.div>
              )}

              {activeTab === 'notes' && (
                <motion.div
                  key="notes"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.18 }}
                  className="rounded-xl border border-surface-border bg-surface-raised p-4"
                >
                  <NotesPanel />
                </motion.div>
              )}

              {activeTab === 'meeting' && (
                <motion.div
                  key="meeting"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.18 }}
                  className="rounded-xl border border-surface-border bg-surface-raised p-4"
                >
                  <MeetingPanel />
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Right: last result + draft */}
          <div className="lg:col-span-2 flex flex-col gap-4">

            {/* Last result card */}
            <div className="rounded-xl border border-surface-border bg-surface-raised overflow-hidden">
              <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border">
                <span className="text-xs font-medium uppercase tracking-wider text-text-muted">
                  Last result
                </span>
                {lastResult && (
                  <span className={`text-xs font-medium rounded-full border px-2 py-0.5 ${STATUS_COLOR[lastResult.status] || STATUS_COLOR.queued}`}>
                    {lastResult.status}
                  </span>
                )}
              </div>
              <div className="p-4">
                <AnimatePresence mode="wait">
                  {!lastResult ? (
                    <motion.div
                      key="empty"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="flex flex-col items-center justify-center py-8 text-center"
                    >
                      <Sparkles className="h-8 w-8 text-text-muted mb-3 opacity-30" />
                      <p className="text-sm text-text-muted">Results will appear here</p>
                    </motion.div>
                  ) : (
                    <motion.div
                      key={lastResult.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="space-y-3"
                    >
                      <p className="text-sm text-text-muted line-clamp-2 italic">
                        &ldquo;{lastResult.text}&rdquo;
                      </p>

                      {(lastResult.status === 'running' || lastResult.status === 'queued') && (
                        <div className="flex items-center gap-2 rounded-lg border border-status-pending/20 bg-status-pending/5 px-3 py-2.5">
                          <Loader2 className="h-4 w-4 animate-spin text-status-pending shrink-0" />
                          <span className="text-sm text-status-pending">Processing…</span>
                        </div>
                      )}

                      {lastResult.result && (
                        <div className="rounded-lg border border-surface-border bg-surface-overlay p-3 text-sm text-text-secondary leading-relaxed max-h-48 overflow-y-auto whitespace-pre-wrap">
                          {lastResult.result}
                        </div>
                      )}

                      {lastResult.assistant_response && (
                        <div className="rounded-lg border border-brand/20 bg-brand/5 px-3 py-2.5 text-sm text-brand-light">
                          {lastResult.assistant_response}
                        </div>
                      )}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>

            {/* Draft card */}
            <AnimatePresence>
              {currentDraft && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                >
                  <div className="text-xs font-medium uppercase tracking-wider text-text-muted mb-3">
                    Draft — Review &amp; Confirm
                  </div>
                  <DraftPreview
                    draft={currentDraft}
                    onConfirm={handleConfirmDraft}
                    onReject={handleRejectDraft}
                    onEdit={handleEditDraft}
                    isExecuting={executing === currentDraft.id}
                  />
                </motion.div>
              )}
            </AnimatePresence>

            {/* Conversation context panel */}
            {session.messages.length > 0 && (
              <div className="rounded-xl border border-surface-border bg-surface-raised p-4">
                <div className="flex items-center gap-1.5 mb-3">
                  <Brain className="h-3.5 w-3.5 text-brand-light" />
                  <p className="text-xs font-medium uppercase tracking-wider text-text-muted">
                    Context ({session.messages.filter(m => m.role !== 'system').length} turns)
                  </p>
                </div>
                <div className="flex flex-col gap-1.5 max-h-40 overflow-y-auto">
                  {session.messages.filter(m => m.role !== 'system').slice(-6).map((msg) => (
                    <div key={msg.id} className="flex items-start gap-2">
                      <span className={`shrink-0 text-[10px] font-bold uppercase mt-0.5 w-8 ${
                        msg.role === 'user' ? 'text-brand-light' : 'text-text-muted'
                      }`}>
                        {msg.role === 'user' ? 'You' : 'AI'}
                      </span>
                      <p className="text-xs text-text-secondary line-clamp-2 leading-relaxed">
                        {msg.text}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Voice Profile Switcher (Feature #4) */}
            <div className="rounded-xl border border-surface-border bg-surface-raised p-4">
              <ProfileSwitcher />
            </div>

            {/* System Info Panel */}
            <SystemInfoPanel
              apiBase={process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}
              autoRefresh={session.isActive}
              collapsed={true}
            />

            {/* Stop phrases reference */}
            <div className="rounded-xl border border-surface-border bg-surface-raised p-4">
              <p className="text-xs font-medium uppercase tracking-wider text-text-muted mb-3">
                Voice stop phrases
              </p>
              <div className="flex flex-wrap gap-1.5">
                {[
                  'stop', 'bye', 'ok stop now', 'end session',
                  'exit conversation', 'that\'s all', 'done',
                ].map((p) => (
                  <span
                    key={p}
                    className="rounded-full border border-surface-border bg-surface-overlay px-2.5 py-0.5 text-xs text-text-muted"
                  >
                    &quot;{p}&quot;
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Proactive notifications (Feature #7) */}
      <ProactiveToast notifications={notifications} onDismiss={dismissNotif} />
    </PageTransition>
  )
}
