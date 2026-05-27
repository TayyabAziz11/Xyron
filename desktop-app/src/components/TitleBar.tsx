import { getCurrentWindow } from '@tauri-apps/api/window'
import { Minus, Square, X } from 'lucide-react'

async function minimize() { await getCurrentWindow().minimize() }
async function maximize() {
  const win = getCurrentWindow()
  if (await win.isMaximized()) await win.unmaximize()
  else await win.maximize()
}
async function close() { await getCurrentWindow().close() }

export function TitleBar() {
  return (
    <div
      data-tauri-drag-region
      className="flex h-8 items-center justify-between bg-[#08080f] border-b border-[rgba(255,32,32,0.15)] px-4 flex-shrink-0 select-none"
    >
      {/* Left: logo text */}
      <span
        data-tauri-drag-region
        className="font-mono text-[10px] tracking-[0.3em] text-[#ff2020]/60 pointer-events-none"
      >
        XYRON OS v2.0
      </span>

      {/* Center: drag area */}
      <div data-tauri-drag-region className="flex-1" />

      {/* Right: window controls */}
      <div className="flex items-center gap-1">
        <button
          onClick={minimize}
          className="flex h-6 w-6 items-center justify-center rounded text-[#475569] hover:bg-[rgba(255,255,255,0.06)] hover:text-white transition-colors"
          aria-label="Minimize"
        >
          <Minus className="h-3 w-3" />
        </button>
        <button
          onClick={maximize}
          className="flex h-6 w-6 items-center justify-center rounded text-[#475569] hover:bg-[rgba(255,255,255,0.06)] hover:text-white transition-colors"
          aria-label="Maximize"
        >
          <Square className="h-3 w-3" />
        </button>
        <button
          onClick={close}
          className="flex h-6 w-6 items-center justify-center rounded text-[#475569] hover:bg-[rgba(255,32,32,0.15)] hover:text-[#ff2020] transition-colors"
          aria-label="Close"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    </div>
  )
}
