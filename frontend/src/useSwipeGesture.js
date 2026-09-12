import { useRef, useState } from 'react'

const TAP_MAX_DISTANCE = 10
const TAP_MAX_DURATION = 300
export const SWIPE_THRESHOLD_PX = 180
export const SLIDE_OUT_DURATION_MS = 220
const SLIDE_IN_DURATION_MS = 220

export function useSwipeGesture({ isPlaying, onNext, onPrevious, onTogglePlay }) {
  const startRef = useRef({ x: 0, y: 0, t: 0, id: null, active: false })
  const [dragX, setDragX] = useState(0)
  // exiting/entering are transitioned (see App.css); dragging/snapping aren't/
  const [phase, setPhase] = useState('idle')
  const generationRef = useRef(0)

  // Shared gesture logic, driven by whichever event API actually reports
  // usable data on this hardware — see onTouch*/onPointer* below. `id` is a
  // prefixed string (e.g. "touch-0" / "pointer-1") so the two sources can
  // never collide.
  const handleStart = (x, y, id) => {
    generationRef.current += 1
    startRef.current = { x, y, t: performance.now(), id, active: true }
    setPhase('dragging')
    setDragX(0)
  }

  const handleMove = (x, y, id) => {
    const s = startRef.current
    if (!s.active || id !== s.id) return
    const dx = x - s.x
    const dy = y - s.y
    if (Math.abs(dx) > Math.abs(dy)) {
      setDragX(dx)
    }
  }

  const handleEnd = (x, y, id) => {
    const s = startRef.current
    if (!s.active || id !== s.id) return
    const dx = x - s.x
    const dy = y - s.y
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
      const stageWidth = window.innerWidth
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

  const handleCancel = () => {
    generationRef.current += 1
    startRef.current.active = false
    setPhase('idle')
    setDragX(0)
  }

  // Pointer Events (mouse on the Mac during dev). On the Pi's touch panel,
  // this was observed reporting pointerType "mouse" with the up-position
  // equal to the down-position regardless of real drag distance, so touch
  // handlers exist as a separate, working path below rather than relying
  // on this alone for real hardware.
  const onPointerDown = (e) => {
    e.preventDefault()
    handleStart(e.clientX, e.clientY, `pointer-${e.pointerId}`)
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e) => {
    e.preventDefault()
    handleMove(e.clientX, e.clientY, `pointer-${e.pointerId}`)
  }
  const onPointerUp = (e) => handleEnd(e.clientX, e.clientY, `pointer-${e.pointerId}`)
  const onPointerCancel = () => handleCancel()

  // Raw Touch Events — the path that actually works reliably on the Pi's
  // touch panel (see comment above). preventDefault() here also suppresses
  // the synthetic compatibility mouse/pointer events browsers normally fire
  // after a real touch, so this path and the pointer path above shouldn't
  // both fire for the same physical touch.
  const onTouchStart = (e) => {
    e.preventDefault()
    const t = e.changedTouches[0]
    handleStart(t.clientX, t.clientY, `touch-${t.identifier}`)
  }
  const onTouchMove = (e) => {
    e.preventDefault()
    const t = e.changedTouches[0]
    handleMove(t.clientX, t.clientY, `touch-${t.identifier}`)
  }
  const onTouchEnd = (e) => {
    const t = e.changedTouches[0]
    handleEnd(t.clientX, t.clientY, `touch-${t.identifier}`)
  }
  const onTouchCancel = () => handleCancel()

  return {
    dragX,
    phase,
    handlers: {
      onPointerDown, onPointerMove, onPointerUp, onPointerCancel,
      onTouchStart, onTouchMove, onTouchEnd, onTouchCancel,
    },
  }
}
