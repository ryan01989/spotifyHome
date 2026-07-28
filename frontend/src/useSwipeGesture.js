import { useRef, useState } from 'react'

const TAP_MAX_DISTANCE = 10
const TAP_MAX_DURATION = 300
const SWIPE_THRESHOLD_PX = 100
export const SLIDE_OUT_DURATION_MS = 220

export function useSwipeGesture({ isPlaying, onNext, onPrevious, onTogglePlay }) {
  const startRef = useRef({ x: 0, y: 0, t: 0, pointerId: null, active: false })
  const [dragX, setDragX] = useState(0)
  const [isDragging, setIsDragging] = useState(false)
  const [isAnimatingOut, setIsAnimatingOut] = useState(false)
  const [animateOutDirection, setAnimateOutDirection] = useState(null)

  const onPointerDown = (e) => {
    startRef.current = { x: e.clientX, y: e.clientY, t: performance.now(), pointerId: e.pointerId, active: true }
    setIsDragging(true)
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
    setIsDragging(false)

    const isTap = Math.max(Math.abs(dx), Math.abs(dy)) < TAP_MAX_DISTANCE && dt < TAP_MAX_DURATION
    const isHorizontalSwipe = Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > SWIPE_THRESHOLD_PX

    if (isTap) {
      setDragX(0)
      onTogglePlay(!isPlaying)
      return
    }

    if (isHorizontalSwipe) {
      const direction = dx < 0 ? 'left' : 'right'
      const stageWidth = e.currentTarget.offsetWidth || window.innerWidth
      setAnimateOutDirection(direction)
      setIsAnimatingOut(true)
      setDragX(direction === 'left' ? -stageWidth : stageWidth)

      window.setTimeout(() => {
        if (direction === 'left') {
          onNext()
        } else {
          onPrevious()
        }
        setIsAnimatingOut(false)
        setAnimateOutDirection(null)
        setDragX(0)
      }, SLIDE_OUT_DURATION_MS)
      return
    }

    setDragX(0)
  }

  const onPointerCancel = () => {
    startRef.current.active = false
    setIsDragging(false)
    setDragX(0)
  }

  return {
    dragX,
    isDragging,
    isAnimatingOut,
    animateOutDirection,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel },
  }
}
