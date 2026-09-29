import { useEffect, useRef, useState } from 'react'

// The arc has to track the wheel closely enough to feel attached to it, so it
// gets its own fast poll rather than riding the 5s /api/now_playing cycle.
const POLL_INTERVAL_MS = 200
const HOLD_MS = 2000

// Geometry in the display's own 720x720 pixels. The drawn path spans 86.37deg
// so that the *visible* span including both round caps is exactly 90deg - a
// quarter of the wheel's circumference, matching KNOB_ARC_DEGREES in
// backend/backend/physical_control/volume.py. Radius 316 plus half the 20px
// stroke clears the physical rim by 34px, so the bezel can't eat a cap.
const ARC_PATH = 'M 143.81 129.53 A 316 316 0 0 1 576.19 129.53'

export default function VolumeArc() {
  const [volume, setVolume] = useState(null)
  const [visible, setVisible] = useState(false)
  // Null until a reading has been seen, so the first poll after mount seeds
  // silently rather than flashing the arc for a change the user didn't make.
  const lastVolumeRef = useRef(null)
  const hideTimeoutRef = useRef(null)
  const pollTimeoutRef = useRef(null)

  useEffect(() => {
    let cancelled = false

    const poll = () => {
      fetch('/api/volume')
        .then((res) => res.json())
        .then((data) => {
          if (cancelled || typeof data.volume !== 'number') return
          setVolume(data.volume)
          if (lastVolumeRef.current !== null && data.volume !== lastVolumeRef.current) {
            setVisible(true)
            clearTimeout(hideTimeoutRef.current)
            hideTimeoutRef.current = setTimeout(() => setVisible(false), HOLD_MS)
          }
          lastVolumeRef.current = data.volume
        })
        .catch(() => {})
        .finally(() => {
          if (!cancelled) pollTimeoutRef.current = setTimeout(poll, POLL_INTERVAL_MS)
        })
    }

    poll()
    return () => {
      cancelled = true
      clearTimeout(pollTimeoutRef.current)
      clearTimeout(hideTimeoutRef.current)
    }
  }, [])

  if (volume === null) return null

  return (
    <svg className={`volume-arc${visible ? ' is-visible' : ''}`} viewBox="0 0 720 720" aria-hidden="true">
      <path className="volume-arc-track" d={ARC_PATH} />
      <path
        className="volume-arc-fill"
        pathLength="100"
        strokeDasharray={`${volume} 100`}
        d={ARC_PATH}
      />
    </svg>
  )
}
