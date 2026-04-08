'use client'

import { useState, useEffect, useRef } from 'react'
import { Mic } from 'lucide-react'
import { CommandBar } from '@/components/command/CommandBar'
import { CommandResult } from '@/components/command/CommandResult'
import { CommandHistory } from '@/components/command/CommandHistory'
import { ExampleCommands } from '@/components/command/ExampleCommands'
import { DraftPreview } from '@/components/command/DraftPreview'
import { PageTransition } from '@/components/layout/PageTransition'
import { VoicePlayer } from '@/components/ui/VoicePlayer'
import { useCommands } from '@/hooks/useCommands'
import { useVoice } from '@/hooks/useVoice'
import { useDrafts } from '@/hooks/useDrafts'
import type { Command, Draft } from '@/lib/types'

export default function CommandPage() {
  const { data: commands, submit, submitting, lastResult } = useCommands()
  const [inputText, setInputText] = useState('')
  const { settings, saveSettings, speak, stop, isSpeaking } = useVoice()
  const { getDraft, confirmDraft, rejectDraft, editDraft, executing } = useDrafts()

  // Current draft shown below the result card
  const [currentDraft, setCurrentDraft] = useState<Draft | null>(null)

  // Track the last completed command we've spoken to avoid double-speak
  const spokenIdRef = useRef<string | null>(null)

  // Poll the latest completed command and auto-play its assistant_response
  useEffect(() => {
    if (!lastResult) return
    if (lastResult.status !== 'completed') return
    if (!lastResult.assistant_response) return
    if (spokenIdRef.current === lastResult.id) return
    if (!settings.autoPlay) return

    spokenIdRef.current = lastResult.id
    speak(lastResult.assistant_response)
  }, [lastResult?.id, lastResult?.status, lastResult?.assistant_response, settings.autoPlay, speak])

  // When the last result completes with a draft_id, fetch the draft
  useEffect(() => {
    if (!lastResult?.draft_id || lastResult?.status !== 'completed') return
    getDraft(lastResult.draft_id).then((d) => {
      if (d) setCurrentDraft(d)
    })
  }, [lastResult?.id, lastResult?.draft_id, lastResult?.status, getDraft])

  // Also watch the commands list for late completions (polled from server)
  const prevCommandsRef = useRef<Command[]>([])
  useEffect(() => {
    if (!commands) return
    const prev = prevCommandsRef.current
    prevCommandsRef.current = commands

    for (const cmd of commands) {
      if (
        cmd.status === 'completed' &&
        cmd.assistant_response &&
        spokenIdRef.current !== cmd.id
      ) {
        // Check if this command just completed (wasn't completed in previous poll)
        const prevCmd = prev.find((p) => p.id === cmd.id)
        if (prevCmd && prevCmd.status !== 'completed') {
          spokenIdRef.current = cmd.id
          if (settings.autoPlay) {
            speak(cmd.assistant_response)
          }
          break // only speak the most recent new completion
        }
      }
    }
  }, [commands, settings.autoPlay, speak])

  const handleSubmit = async (text: string) => {
    setInputText('')
    // Clear any existing draft when submitting a new command
    setCurrentDraft(null)
    await submit(text)
  }

  const handleExampleSelect = (text: string) => {
    setInputText(text)
  }

  const handleVoiceToggle = () => {
    if (isSpeaking) {
      stop()
    }
    saveSettings({ enabled: !settings.enabled })
  }

  const handleSpeak = () => {
    if (lastResult?.assistant_response) {
      speak(lastResult.assistant_response)
    }
  }

  const handleConfirmDraft = async (draftId: string) => {
    const result = await confirmDraft(draftId)
    if (result.spoken && settings.enabled) {
      speak(result.spoken)
    }
    // Refresh the draft to show executed state
    const updated = await getDraft(draftId)
    if (updated) setCurrentDraft(updated)
  }

  const handleRejectDraft = async (draftId: string) => {
    await rejectDraft(draftId)
    const updated = await getDraft(draftId)
    if (updated) setCurrentDraft(updated)
  }

  const handleEditDraft = async (draftId: string, content: string) => {
    await editDraft(draftId, content)
    const updated = await getDraft(draftId)
    if (updated) setCurrentDraft(updated)
  }

  return (
    <PageTransition>
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Header row with voice controls */}
        <div className="flex items-center justify-between gap-3 rounded-xl border border-brand/20 bg-brand/5 px-4 py-3">
          <div className="flex items-center gap-3">
            <Mic className="h-4 w-4 text-brand-light flex-shrink-0" />
            <p className="text-sm text-text-secondary">
              <span className="font-medium text-brand-light">Voice responses</span>
              {' '}— AI Operator will speak results when complete
            </p>
          </div>
          <VoicePlayer
            isSpeaking={isSpeaking}
            voiceEnabled={settings.enabled}
            onToggle={handleVoiceToggle}
          />
        </div>

        {/* Command input */}
        <div>
          <h2 className="text-xs font-medium uppercase tracking-wider text-text-muted mb-3">
            Run a command
          </h2>
          <CommandBar
            onSubmit={handleSubmit}
            loading={submitting}
          />
        </div>

        {/* Last result — with voice controls */}
        {lastResult && (
          <div>
            <h2 className="text-xs font-medium uppercase tracking-wider text-text-muted mb-3">
              Result
            </h2>
            <CommandResult
              command={lastResult}
              isSpeaking={isSpeaking}
              voiceEnabled={settings.enabled}
              onVoiceToggle={handleVoiceToggle}
              onSpeak={lastResult.status === 'completed' ? handleSpeak : undefined}
            />
          </div>
        )}

        {/* Draft preview — appears when command produces a reviewable draft */}
        {currentDraft && (
          <div>
            <h2 className="text-xs font-medium uppercase tracking-wider text-text-muted mb-3">
              Draft — Review &amp; Confirm
            </h2>
            <DraftPreview
              draft={currentDraft}
              onConfirm={handleConfirmDraft}
              onReject={handleRejectDraft}
              onEdit={handleEditDraft}
              isExecuting={executing === currentDraft.id}
            />
          </div>
        )}

        {/* Example commands */}
        <ExampleCommands onSelect={handleExampleSelect} />

        {/* History */}
        <div>
          <h2 className="text-xs font-medium uppercase tracking-wider text-text-muted mb-3">
            Command history
          </h2>
          <div className="rounded-xl border border-surface-border bg-surface-raised">
            <CommandHistory
              commands={commands ?? []}
              className="px-5"
            />
          </div>
        </div>
      </div>
    </PageTransition>
  )
}
