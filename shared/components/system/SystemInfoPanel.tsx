

import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Cpu, HardDrive, MemoryStick, Monitor, RefreshCw,
  Activity, ChevronDown, ChevronRight,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface DriveInfo {
  name:     string
  total_gb: number
  used_gb:  number
  free_gb:  number
  pct_used: number
}

interface RamInfo {
  total_gb: number
  used_gb:  number
  avail_gb: number
  pct_used: number
}

interface CpuInfo {
  name:           string
  logical_cores:  number
  physical_cores: number
}

interface SystemData {
  os:     string
  cpu:    CpuInfo
  ram:    RamInfo
  drives: DriveInfo[]
}

interface HealthData {
  cpu_pct:      number
  ram_pct:      number
  ram_used_gb:  number
  ram_total_gb: number
  drives:       Array<{ name: string; pct_used: number; free_gb: number; total_gb: number }>
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function pctColor(pct: number): string {
  if (pct >= 90) return 'bg-status-error'
  if (pct >= 70) return 'bg-status-pending'
  return 'bg-status-success'
}

function pctTextColor(pct: number): string {
  if (pct >= 90) return 'text-status-error'
  if (pct >= 70) return 'text-status-pending'
  return 'text-status-success'
}

// ── Sub-components ────────────────────────────────────────────────────────────

function UsageBar({ pct, label }: { pct: number; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-16 shrink-0 text-xs text-text-muted">{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-surface-border overflow-hidden">
        <motion.div
          className={`h-full rounded-full ${pctColor(pct)}`}
          initial={{ width: 0 }}
          animate={{ width: `${Math.min(100, pct)}%` }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
        />
      </div>
      <span className={`w-9 shrink-0 text-right text-xs font-medium ${pctTextColor(pct)}`}>
        {pct.toFixed(0)}%
      </span>
    </div>
  )
}

function DriveBar({ drive }: { drive: DriveInfo | HealthData['drives'][number] }) {
  const pct = 'pct_used' in drive ? drive.pct_used : 0
  const free = drive.free_gb
  const total = drive.total_gb
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-text-primary">{drive.name}</span>
        <span className="text-text-muted">
          {free.toFixed(1)} GB free / {total.toFixed(1)} GB
        </span>
      </div>
      <div className="h-2 rounded-full bg-surface-border overflow-hidden">
        <motion.div
          className={`h-full rounded-full ${pctColor(pct)}`}
          initial={{ width: 0 }}
          animate={{ width: `${Math.min(100, pct)}%` }}
          transition={{ duration: 0.7, ease: 'easeOut' }}
        />
      </div>
    </div>
  )
}

// ── Main Panel ────────────────────────────────────────────────────────────────

interface Props {
  apiBase?:     string
  autoRefresh?: boolean   // if true, polls health every 5 s
  collapsed?:   boolean
}

export function SystemInfoPanel({ apiBase = 'http://localhost:8000', autoRefresh = false, collapsed: initCollapsed = false }: Props) {
  const [collapsed,   setCollapsed]   = useState(initCollapsed)
  const [sysData,     setSysData]     = useState<SystemData | null>(null)
  const [healthData,  setHealthData]  = useState<HealthData | null>(null)
  const [loading,     setLoading]     = useState(false)
  const [error,       setError]       = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const fetchInfo = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [infoRes, healthRes] = await Promise.all([
        fetch(`${apiBase}/api/v1/system/system-info`),
        fetch(`${apiBase}/api/v1/system/system-health`),
      ])
      if (infoRes.ok) {
        const j = await infoRes.json()
        if (j.success && j.data) setSysData(j.data as SystemData)
      }
      if (healthRes.ok) {
        const j = await healthRes.json()
        if (j.success && j.data) setHealthData(j.data as HealthData)
      }
      setLastUpdated(new Date())
    } catch {
      setError('Could not reach backend.')
    } finally {
      setLoading(false)
    }
  }, [apiBase])

  // Fetch on first expand
  useEffect(() => {
    if (!collapsed && !sysData) fetchInfo()
  }, [collapsed, sysData, fetchInfo])

  // Auto-refresh health (live usage) every 5 s when enabled
  useEffect(() => {
    if (!autoRefresh || collapsed) return
    const id = setInterval(async () => {
      try {
        const r = await fetch(`${apiBase}/api/v1/system/system-health`)
        if (r.ok) {
          const j = await r.json()
          if (j.success && j.data) { setHealthData(j.data as HealthData); setLastUpdated(new Date()) }
        }
      } catch { /* silent */ }
    }, 5000)
    return () => clearInterval(id)
  }, [autoRefresh, collapsed, apiBase])

  // Shorthand for CPU brand cleanup
  const cpuShort = sysData?.cpu?.name
    ?.replace(/\(R\)|\(TM\)|\(C\)/g, '')
    ?.replace(/\s*CPU\s*@\s*[\d.]+\s*GHz/i, '')
    ?.replace(/\s{2,}/g, ' ')
    ?.trim() ?? ''

  return (
    <div className="rounded-xl border border-surface-border bg-surface-raised overflow-hidden">
      {/* Header */}
      <button
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-surface-overlay transition-colors"
        onClick={() => setCollapsed((v) => !v)}
      >
        <div className="flex items-center gap-2">
          <Monitor className="h-4 w-4 text-brand-light" />
          <span className="text-sm font-medium text-text-primary">System Info</span>
          {sysData && (
            <span className="text-xs text-text-muted ml-1">{sysData.os}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {lastUpdated && !collapsed && (
            <span className="text-xs text-text-muted">
              {lastUpdated.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
            </span>
          )}
          {!collapsed && (
            <button
              onClick={(e) => { e.stopPropagation(); fetchInfo() }}
              disabled={loading}
              className="rounded p-1 text-text-muted hover:text-brand-light transition-colors disabled:opacity-40"
              title="Refresh"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
          )}
          {collapsed
            ? <ChevronRight className="h-4 w-4 text-text-muted" />
            : <ChevronDown  className="h-4 w-4 text-text-muted" />}
        </div>
      </button>

      {/* Body */}
      <AnimatePresence initial={false}>
        {!collapsed && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22 }}
            className="overflow-hidden"
          >
            <div className="border-t border-surface-border px-4 py-3 space-y-4">

              {error && (
                <p className="text-xs text-status-error">{error}</p>
              )}

              {loading && !sysData && (
                <div className="flex items-center gap-2 text-xs text-text-muted py-2">
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                  Fetching system data…
                </div>
              )}

              {/* CPU */}
              {sysData?.cpu && (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-1.5">
                    <Cpu className="h-3.5 w-3.5 text-text-muted" />
                    <span className="text-xs font-medium uppercase tracking-wider text-text-muted">CPU</span>
                  </div>
                  <p className="text-sm text-text-primary pl-5">{cpuShort || 'Unknown'}</p>
                  <p className="text-xs text-text-muted pl-5">
                    {sysData.cpu.logical_cores} logical / {sysData.cpu.physical_cores} physical cores
                  </p>
                  {healthData && (
                    <div className="pl-5">
                      <UsageBar pct={healthData.cpu_pct} label="Usage" />
                    </div>
                  )}
                </div>
              )}

              {/* RAM */}
              {(sysData?.ram || healthData) && (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-1.5">
                    <MemoryStick className="h-3.5 w-3.5 text-text-muted" />
                    <span className="text-xs font-medium uppercase tracking-wider text-text-muted">RAM</span>
                  </div>
                  <div className="pl-5 space-y-1">
                    {sysData?.ram && (
                      <p className="text-xs text-text-muted">
                        {sysData.ram.total_gb.toFixed(1)} GB total ·{' '}
                        {sysData.ram.avail_gb.toFixed(1)} GB free
                      </p>
                    )}
                    {healthData && (
                      <UsageBar pct={healthData.ram_pct} label="Usage" />
                    )}
                  </div>
                </div>
              )}

              {/* Drives */}
              {(() => {
                const drives = sysData?.drives?.length ? sysData.drives :
                               healthData?.drives?.length ? healthData.drives.map(d => ({
                                 name: d.name, total_gb: d.total_gb, used_gb: d.total_gb - d.free_gb,
                                 free_gb: d.free_gb, pct_used: d.pct_used,
                               })) : []
                if (!drives.length) return null
                return (
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5">
                      <HardDrive className="h-3.5 w-3.5 text-text-muted" />
                      <span className="text-xs font-medium uppercase tracking-wider text-text-muted">
                        Storage
                      </span>
                    </div>
                    <div className="pl-5 space-y-3">
                      {drives.map((d) => (
                        <DriveBar key={d.name} drive={d} />
                      ))}
                    </div>
                  </div>
                )
              })()}

              {/* Live health summary row */}
              {healthData && (
                <div className="flex items-center gap-2 pt-1 border-t border-surface-border/60">
                  <Activity className="h-3.5 w-3.5 text-brand-light shrink-0" />
                  <span className="text-xs text-text-muted">Live</span>
                  <span className={`text-xs font-medium ${pctTextColor(healthData.cpu_pct)}`}>
                    CPU {healthData.cpu_pct.toFixed(0)}%
                  </span>
                  <span className="text-text-muted text-xs">·</span>
                  <span className={`text-xs font-medium ${pctTextColor(healthData.ram_pct)}`}>
                    RAM {healthData.ram_pct.toFixed(0)}%
                  </span>
                </div>
              )}

            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
