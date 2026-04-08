'use client'

import { useState } from 'react'
import { CheckCircle, X, Edit3, Send, ExternalLink } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Draft } from '@/lib/types'

interface DraftPreviewProps {
  draft: Draft
  onConfirm: (id: string) => Promise<void>
  onReject: (id: string) => void
  onEdit: (id: string, content: string) => void
  isExecuting?: boolean
  className?: string
}

const TYPE_LABELS: Record<string, string> = {
  linkedin_post: 'LinkedIn Post',
  email: 'Email Draft',
  instagram: 'Instagram Post',
  whatsapp: 'WhatsApp Message',
}

const CONFIRM_LABELS: Record<string, string> = {
  linkedin_post: 'Publish to LinkedIn',
  email: 'Send Email',
  instagram: 'Post to Instagram',
  whatsapp: 'Send WhatsApp',
}

const VOICE_HINTS: Record<string, string> = {
  email: 'send it',
  linkedin_post: 'post it',
  instagram: 'post it',
  whatsapp: 'send it',
}

export function DraftPreview({
  draft,
  onConfirm,
  onReject,
  onEdit,
  isExecuting,
  className,
}: DraftPreviewProps) {
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState(draft.content)

  const typeLabel = TYPE_LABELS[draft.draft_type] ?? draft.draft_type
  const confirmLabel = CONFIRM_LABELS[draft.draft_type] ?? 'Confirm'
  const voiceHint = VOICE_HINTS[draft.draft_type] ?? 'confirm it'

  if (draft.status === 'executed') {
    return (
      <div
        className={cn(
          'rounded-xl border border-status-success/30 bg-status-success/5 p-4',
          className,
        )}
      >
        <div className="flex items-center gap-2 text-status-success mb-1">
          <CheckCircle className="w-4 h-4" />
          <span className="font-semibold text-sm">{typeLabel} Published!</span>
        </div>
        {draft.execution_url && (
          <a
            href={draft.execution_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-text-secondary hover:text-brand-light flex items-center gap-1 mt-1"
          >
            <ExternalLink className="w-3 h-3" /> View post
          </a>
        )}
      </div>
    )
  }

  if (draft.status === 'rejected') {
    return (
      <div
        className={cn(
          'rounded-xl border border-surface-border bg-surface-raised p-3 opacity-60',
          className,
        )}
      >
        <p className="text-xs text-text-muted">Draft cancelled.</p>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'rounded-xl border bg-surface-raised overflow-hidden',
        draft.status === 'executing' ? 'border-brand/40' : 'border-surface-border',
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-brand-light">
            {typeLabel}
          </span>
          <span className="text-xs text-text-muted">· Ready for review</span>
        </div>
        <button
          onClick={() => setEditing((e) => !e)}
          className="p-1.5 rounded-md hover:bg-surface-hover text-text-muted hover:text-text-primary transition-colors"
          title="Edit draft"
          aria-label="Edit draft"
        >
          <Edit3 className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Content */}
      <div className="p-4">
        {editing ? (
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="w-full bg-surface-base border border-surface-border rounded-lg p-3 text-sm
                       text-text-primary outline-none focus:border-brand resize-none min-h-[140px]
                       font-sans leading-relaxed"
            autoFocus
          />
        ) : (
          <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
            {draft.content}
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 px-4 pb-4">
        {editing ? (
          <>
            <button
              onClick={() => {
                onEdit(draft.id, editContent)
                setEditing(false)
              }}
              className="flex-1 flex items-center justify-center gap-2 py-2 rounded-lg
                         bg-brand text-white text-sm font-medium hover:bg-brand-light transition-colors"
            >
              Save changes
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-3 py-2 rounded-lg border border-surface-border text-sm text-text-muted
                         hover:text-text-primary transition-colors"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              onClick={() => onConfirm(draft.id)}
              disabled={isExecuting}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg
                         bg-brand text-white text-sm font-semibold hover:bg-brand-light
                         disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            >
              {isExecuting ? (
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : (
                <Send className="w-3.5 h-3.5" />
              )}
              {isExecuting ? 'Publishing…' : confirmLabel}
            </button>
            <button
              onClick={() => onReject(draft.id)}
              disabled={isExecuting}
              className="p-2.5 rounded-lg border border-surface-border text-text-muted
                         hover:border-status-error/40 hover:text-status-error
                         disabled:opacity-50 transition-colors"
              title="Cancel draft"
              aria-label="Cancel draft"
            >
              <X className="w-4 h-4" />
            </button>
          </>
        )}
      </div>

      {/* Voice hint */}
      {!editing && draft.status === 'draft' && (
        <div className="px-4 pb-3 text-xs text-text-muted flex items-center gap-1.5">
          <span>🎙</span>
          Say{' '}
          <span className="font-mono bg-surface-overlay px-1.5 py-0.5 rounded text-brand-light">
            &ldquo;{voiceHint}&rdquo;
          </span>{' '}
          to confirm by voice
        </div>
      )}
    </div>
  )
}
