'use client'

import { useEffect, useRef } from 'react'

// Zero-rerender mouse parallax — writes transform directly to the DOM via ref.
export function useParallaxCore(strength = 10) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const target  = { x: 0, y: 0 }
    const current = { x: 0, y: 0 }
    let rafId: number

    const onMove = (e: MouseEvent) => {
      const cx = window.innerWidth  / 2
      const cy = window.innerHeight / 2
      target.x = ((e.clientX - cx) / cx) * strength
      target.y = ((e.clientY - cy) / cy) * strength
    }

    const tick = () => {
      current.x += (target.x - current.x) * 0.055
      current.y += (target.y - current.y) * 0.055
      if (ref.current) {
        ref.current.style.transform = `translate(${current.x.toFixed(2)}px, ${current.y.toFixed(2)}px)`
      }
      rafId = requestAnimationFrame(tick)
    }

    window.addEventListener('mousemove', onMove, { passive: true })
    rafId = requestAnimationFrame(tick)
    return () => {
      window.removeEventListener('mousemove', onMove)
      cancelAnimationFrame(rafId)
    }
  }, [strength])

  return ref
}
