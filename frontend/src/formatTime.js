import { useEffect, useRef, useState } from 'react'

export function formatTime(ms) {
  const totalSeconds = Math.floor((ms ?? 0) / 1000)
  const mm = String(Math.floor(totalSeconds / 60)).padStart(2, '0')
  const ss = String(totalSeconds % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

const TICK_MS = 200

// Polling only happens every few seconds, which would otherwise make the
// progress bar/time jump in visible steps. 
// Correct to the server's value whenever a fresh one arrives, 
// Also freeze/resume immediately on play-state changes
// instead of waiting for the next poll to reflect a tap.
export function useSimulatedProgress(progressMs, durationMs, isPlaying) {
  const [displayProgressMs, setDisplayProgressMs] = useState(progressMs ?? 0)
  const displayRef = useRef(progressMs ?? 0)
  // The interval's reference point. Kept in a ref so a poll correction can update it in place
  const anchorRef = useRef({ base: progressMs ?? 0, at: Date.now() })

  const setDisplay = (value) => {
    displayRef.current = value
    setDisplayProgressMs(value)
  }

  useEffect(() => {
    anchorRef.current = { base: progressMs ?? 0, at: Date.now() }
    setDisplay(progressMs ?? 0)
  }, [progressMs])

  useEffect(() => {
    if (!isPlaying) return

    // Re-anchor at resume time from wherever we're currently sitting, so
    // time spent paused isn't counted as elapsed playback.
    anchorRef.current = { base: displayRef.current, at: Date.now() }
    const cap = durationMs || Infinity

    const id = setInterval(() => {
      const { base, at } = anchorRef.current
      setDisplay(Math.min(base + (Date.now() - at), cap))
    }, TICK_MS)

    return () => clearInterval(id)
  }, [isPlaying, durationMs])

  return displayProgressMs
}
