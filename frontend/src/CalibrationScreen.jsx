import { useCallback, useEffect, useState } from 'react'

// Fast while the user is turning, so the step count keeps up with the wheel;
// slow otherwise - just enough to notice the backend restarting (which raises
// the startup prompt again).
const ACTIVE_POLL_MS = 200
const IDLE_POLL_MS = 2000

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  return { ok: res.ok, data: await res.json().catch(() => ({})) }
}

// Owns /api/calibration: the backend raises a prompt on every startup and
// keeps the motor off until it's completed, skipped or times out.
export function useCalibration() {
  const [status, setStatus] = useState(null)
  // Set when the backend counted a plausible turn that isn't the expected
  // steps per revolution - the screen then asks before saving it.
  const [mismatch, setMismatch] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    let timeout = null
    let active = false

    const poll = () => {
      fetch('/api/calibration')
        .then((res) => res.json())
        .then((data) => {
          if (cancelled) return
          active = !!(data.prompt || data.in_progress)
          setStatus(data)
        })
        .catch(() => {})
        .finally(() => {
          if (!cancelled) timeout = setTimeout(poll, active ? ACTIVE_POLL_MS : IDLE_POLL_MS)
        })
    }

    poll()
    return () => {
      cancelled = true
      clearTimeout(timeout)
    }
  }, [])

  const apply = useCallback(({ ok, data }) => {
    if (ok) {
      setStatus(data)
      setMismatch(null)
      setError(null)
    } else if (data.error === 'mismatch') {
      setMismatch(data.steps)
    } else {
      setError(data.message ?? 'Something went wrong')
    }
  }, [])

  const start = useCallback(() => post('/api/calibration/start').then(apply), [apply])
  const finish = useCallback((force = false) => post('/api/calibration/finish', { force }).then(apply), [apply])
  const skip = useCallback(() => post('/api/calibration/skip').then(apply), [apply])

  return {
    status,
    active: !!(status?.prompt || status?.in_progress),
    mismatch,
    error,
    start,
    finish,
    skip,
  }
}

export default function CalibrationScreen({ status, mismatch, error, start, finish, skip }) {
  const expected = status.expected_steps_per_rev

  if (mismatch !== null) {
    return (
      <div className="stage calibration">
        <h1>Unexpected count</h1>
        <p>
          Counted <strong>{mismatch}</strong> steps, expected {expected}. A magnet or sensor may be
          out of place.
        </p>
        <div className="calibration-actions">
          <button type="button" onClick={start}>Try again</button>
          <button type="button" className="secondary" onClick={() => finish(true)}>
            Save {mismatch}
          </button>
        </div>
      </div>
    )
  }

  if (status.in_progress) {
    return (
      <div className="stage calibration">
        <p>Turn the wheel exactly one full revolution, then tap Done.</p>
        <div className="calibration-count">
          {status.steps}
          <span> / {expected}</span>
        </div>
        {error && <p className="calibration-error">{error}</p>}
        <div className="calibration-actions">
          <button type="button" onClick={() => finish()}>Done</button>
          <button type="button" className="secondary" onClick={skip}>Cancel</button>
        </div>
      </div>
    )
  }

  return (
    <div className="stage calibration">
      <h1>Calibrate the wheel</h1>
      <p>Note where the wheel sits, tap Start, then turn it one full revolution by hand.</p>
      {error && <p className="calibration-error">{error}</p>}
      <div className="calibration-actions">
        <button type="button" onClick={start}>Start</button>
        <button type="button" className="secondary" onClick={skip}>
          {status.calibrated ? 'Keep current' : 'Skip'}
        </button>
      </div>
    </div>
  )
}
