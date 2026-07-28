import { useRef, useState } from 'react'

const TAP_MAX_DISTANCE = 10
const TAP_MAX_DURATION = 300
export const SWIPE_THRESHOLD_PX = 100
export const SLIDE_OUT_DURATION_MS = 220
const SLIDE_IN_DURATION_MS = 220

export function useSwipeGesture({ isPlaying, onNext, onPrevious, onTogglePlay }) {
  const startRef = useRef({ x: 0, y: 0, t: 0, pointerId: null, active: false })
  const [dragX, setDragX] = useState(0)
  // 'dragging' -> 'exiting' -> 'snapping' -> 'entering' -> 'idle'.
  // exiting/entering are transitioned (see App.css); dragging/snapping aren't/
  const [phase, setPhase] = useState('idle')
  // Bumped on every new gesture so a stale exit/enter sequence from a swipe
  // can tell it's been superseded and stop touching state instead of stomping the new one.
  const generationRef = useRef(0)

  const onPointerDown = (e) => {
    generationRef.current += 1
    startRef.current = { x: e.clientX, y: e.clientY, t: performance.now(), pointerId: e.pointerId, active: true }
    setPhase('dragging')
    setDragX(0)
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e) => {
    const s = startRef.current
    if (!s.active || e.pointerId !== s.pointerId) return
    const dx = e.clientX - s.x
    const dy = e.clientY - s.y
    if (Math.abs(dx) > Math.abs(dy)) {
      setDragX(dx)
    }
  }

  const onPointerUp = (e) => {
    const s = startRef.current
    if (!s.active || e.pointerId !== s.pointerId) return
    const dx = e.clientX - s.x
    const dy = e.clientY - s.y
    const dt = performance.now() - s.t
    s.active = false

    const isTap = Math.max(Math.abs(dx), Math.abs(dy)) < TAP_MAX_DISTANCE && dt < TAP_MAX_DURATION
    const isHorizontalSwipe = Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > SWIPE_THRESHOLD_PX

    if (isTap) {
      setPhase('idle')
      setDragX(0)
      onTogglePlay(!isPlaying)
      return
    }

    if (isHorizontalSwipe) {
      const generation = generationRef.current
      const direction = dx < 0 ? 'left' : 'right'
      const stageWidth = e.currentTarget.offsetWidth || window.innerWidth
      const action = direction === 'left' ? onNext : onPrevious

      // Slide the current track the rest of the way off in the direction
      // the user was already dragging.
      setPhase('exiting')
      setDragX(direction === 'left' ? -stageWidth : stageWidth)

      window.setTimeout(async () => {
        if (generationRef.current !== generation) return

        setPhase('snapping')
        setDragX(direction === 'left' ? stageWidth : -stageWidth)

        await action()
        if (generationRef.current !== generation) return

        // Wait a couple of frames so the browser commits the snapped,
        // transition-less position before we turn the transition back on —
        // otherwise it can coalesce the snap and the slide-in into one and
        // the entrance never renders.
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            if (generationRef.current !== generation) return
            setPhase('entering')
            setDragX(0)
          })
        })

        window.setTimeout(() => {
          if (generationRef.current !== generation) return
          setPhase('idle')
        }, SLIDE_IN_DURATION_MS)
      }, SLIDE_OUT_DURATION_MS)
      return
    }

    setPhase('idle')
    setDragX(0)
  }

  const onPointerCancel = () => {
    generationRef.current += 1
    startRef.current.active = false
    setPhase('idle')
    setDragX(0)
  }

  return {
    dragX,
    phase,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel },
  }
}