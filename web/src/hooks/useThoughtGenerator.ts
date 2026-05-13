'use client'

import { useEffect, useRef, useState } from 'react'
import type { CognitiveState } from './useCognitiveState'
import { getEmotion, ATTENTION_THOUGHTS, type Thought } from '@/config/emotions'

let _nextId = 0

function pickThought(cogState: CognitiveState): string {
  const emotion    = cogState.last_user_emotion ?? 'neutral'
  const attention  = cogState.attention
  const emotionPool = getEmotion(emotion).thoughts
  const attnPool    = ATTENTION_THOUGHTS[attention] ?? []

  // 30% chance of an attention-specific thought when available
  const pool = attnPool.length > 0 && Math.random() < 0.3 ? attnPool : emotionPool
  return pool[Math.floor(Math.random() * pool.length)]
}

export function useThoughtGenerator(cogState: CognitiveState | null): Thought[] {
  const [thoughts, setThoughts] = useState<Thought[]>([])
  const prevKeyRef  = useRef('')
  const cogStateRef = useRef(cogState)

  // Keep ref fresh without triggering effects
  useEffect(() => { cogStateRef.current = cogState }, [cogState])

  // Fire a thought on first load + whenever emotion/attention changes
  useEffect(() => {
    if (!cogState) return
    const key = `${cogState.last_user_emotion}:${cogState.attention}`
    if (prevKeyRef.current === key) return
    prevKeyRef.current = key

    const thought: Thought = {
      id:        ++_nextId,
      text:      pickThought(cogState),
      emotion:   cogState.last_user_emotion ?? 'neutral',
      timestamp: Date.now(),
    }
    setThoughts(prev => [...prev, thought].slice(-20))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cogState?.last_user_emotion, cogState?.attention])

  // Periodic ambient thought every 12–18 s to keep the stream alive
  useEffect(() => {
    const delay = 12000 + Math.random() * 6000
    const timer = setTimeout(() => {
      const cs = cogStateRef.current
      if (!cs) return
      const thought: Thought = {
        id:        ++_nextId,
        text:      pickThought(cs),
        emotion:   cs.last_user_emotion ?? 'neutral',
        timestamp: Date.now(),
      }
      setThoughts(prev => [...prev, thought].slice(-20))
    }, delay)
    return () => clearTimeout(timer)
  }, [thoughts]) // re-arm after each new thought

  return thoughts
}
