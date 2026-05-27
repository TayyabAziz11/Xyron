import { Component } from 'react'
import type { ReactNode, ErrorInfo } from 'react'

interface Props {
  children: ReactNode
  label?: string   // shown in the fallback card
}

interface State {
  crashed: boolean
  message: string
}

/**
 * SafePanel — error boundary for individual dashboard panels.
 * If a panel throws during render, it shows a graceful fallback card
 * instead of propagating the crash to the whole dashboard.
 *
 * In dev mode the error message is shown; in production only the label.
 */
export class SafePanel extends Component<Props, State> {
  state: State = { crashed: false, message: '' }

  static getDerivedStateFromError(e: Error): State {
    return { crashed: true, message: e.message ?? 'Unknown error' }
  }

  componentDidCatch(e: Error, info: ErrorInfo) {
    console.error('[SafePanel] panel crash —', this.props.label ?? 'unknown', e, info)
  }

  render() {
    if (!this.state.crashed) return this.props.children

    const isDev = process.env.NODE_ENV !== 'production'

    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[60px] rounded"
        style={{
          background: 'rgba(255,31,45,0.05)',
          border: '1px solid rgba(255,31,45,0.2)',
        }}>
        <span className="font-mono text-[8px] text-[rgba(255,31,45,0.7)] tracking-widest uppercase">
          Panel temporarily unavailable
        </span>
        {this.props.label && (
          <span className="font-mono text-[7px] text-[#374151] mt-0.5">{this.props.label}</span>
        )}
        {isDev && (
          <span className="font-mono text-[6px] text-[#374151] mt-1 px-2 text-center max-w-[160px] truncate">
            {this.state.message}
          </span>
        )}
        <button
          onClick={() => this.setState({ crashed: false, message: '' })}
          className="mt-2 px-2 py-0.5 rounded font-mono text-[7px] text-[rgba(255,31,45,0.6)]"
          style={{ border: '1px solid rgba(255,31,45,0.2)' }}>
          retry
        </button>
      </div>
    )
  }
}
