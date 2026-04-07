'use client'

import { useState } from 'react'
import { Mic } from 'lucide-react'
import { CommandBar } from '@/components/command/CommandBar'
import { CommandResult } from '@/components/command/CommandResult'
import { CommandHistory } from '@/components/command/CommandHistory'
import { ExampleCommands } from '@/components/command/ExampleCommands'
import { useCommands } from '@/hooks/useCommands'

export default function CommandPage() {
  const { data: commands, submit, submitting, lastResult } = useCommands()
  const [inputText, setInputText] = useState('')

  const handleSubmit = async (text: string) => {
    setInputText('')
    await submit(text)
  }

  const handleExampleSelect = (text: string) => {
    setInputText(text)
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6 animate-fade-in">
      {/* Voice banner */}
      <div className="flex items-center gap-3 rounded-xl border border-brand/20 bg-brand/5 px-4 py-3">
        <Mic className="h-4 w-4 text-brand-light flex-shrink-0" />
        <p className="text-sm text-text-secondary">
          <span className="font-medium text-brand-light">Push-to-talk voice commands</span>
          {' '}coming in the next version
        </p>
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

      {/* Last result */}
      {lastResult && (
        <div>
          <h2 className="text-xs font-medium uppercase tracking-wider text-text-muted mb-3">
            Result
          </h2>
          <CommandResult command={lastResult} />
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
  )
}
