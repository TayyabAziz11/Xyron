

import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { useCommands } from '@/hooks/useCommands'

export function QuickCommand() {
  const [text, setText] = useState('')
  const { submit, submitting, lastResult } = useCommands()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    await submit(text)
    setText('')
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Quick Command</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Tell Xyron what to do…"
            className="w-full rounded-lg bg-surface-overlay border border-surface-border px-4 py-3 text-sm text-text-primary placeholder:text-text-muted focus:border-brand/50 focus:outline-none focus:ring-1 focus:ring-brand/30 transition-colors"
          />
          <div className="flex items-center justify-between">
            <p className="text-xs text-text-muted">
              Voice commands coming soon
            </p>
            <Button type="submit" size="sm" loading={submitting} icon={ArrowRight}>
              Run
            </Button>
          </div>
        </form>

        {lastResult && (
          <div className="mt-4 rounded-lg bg-surface-overlay border border-surface-border p-3">
            <p className="text-xs text-text-muted mb-1">Last command queued</p>
            <p className="text-sm text-text-secondary truncate">{lastResult.text}</p>
            <span className="mt-1 inline-block text-xs text-status-pending">
              Status: {lastResult.status}
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
