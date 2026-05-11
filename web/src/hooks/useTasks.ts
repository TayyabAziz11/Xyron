'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import type { Task } from '@/lib/types'

const API_BASE = typeof window !== 'undefined' ? '' : 'http://localhost:8000'

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useTasks() {
  const [tasks,       setTasks]       = useState<Task[]>([])
  const [activeTasks, setActiveTasks] = useState<Task[]>([])
  const [loading,     setLoading]     = useState(false)
  const [error,       setError]       = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Fetch recent tasks ─────────────────────────────────────────────────────

  const fetchTasks = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/tasks?limit=10`)
      if (!r.ok) return
      const data: Task[] = await r.json()
      setTasks(data)
      setActiveTasks(data.filter((t) => t.status === 'planning' || t.status === 'running'))
    } catch {
      /* non-fatal */
    }
  }, [])

  // ── Submit a new multi-step task ───────────────────────────────────────────

  const submitTask = useCallback(
    async (goal: string, history: { role: string; text: string }[] = []): Promise<Task | null> => {
      setLoading(true)
      setError(null)
      try {
        const r = await fetch(`${API_BASE}/api/v1/tasks`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ goal, history }),
        })
        if (!r.ok) {
          setError('Failed to create task')
          return null
        }
        const task: Task = await r.json()
        setTasks((prev) => [task, ...prev])
        setActiveTasks((prev) => [task, ...prev])
        return task
      } catch (e) {
        setError('Network error')
        return null
      } finally {
        setLoading(false)
      }
    },
    [],
  )

  // ── Cancel a task ──────────────────────────────────────────────────────────

  const cancelTask = useCallback(async (taskId: string) => {
    try {
      await fetch(`${API_BASE}/api/v1/tasks/${taskId}`, { method: 'DELETE' })
      await fetchTasks()
    } catch { /* non-fatal */ }
  }, [fetchTasks])

  // ── Poll active tasks every 800ms ─────────────────────────────────────────

  useEffect(() => {
    fetchTasks()
  }, [fetchTasks])

  useEffect(() => {
    if (activeTasks.length === 0) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
      return
    }
    if (!pollRef.current) {
      pollRef.current = setInterval(() => {
        fetchTasks()
      }, 800)
    }
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    }
  }, [activeTasks.length, fetchTasks])

  return { tasks, activeTasks, loading, error, submitTask, cancelTask, refetch: fetchTasks }
}
