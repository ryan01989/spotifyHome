import { useEffect, useState } from 'react'
import Album from './album'
import './App.css'

const TRACK_SECONDS = 30

export default function App() {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const id = setInterval(() => {
      setElapsed((e) => (e + 1) % TRACK_SECONDS)
    }, 1000)
    return () => clearInterval(id)
  }, [])

  const progress = (elapsed / TRACK_SECONDS) * 100
  const mm = String(Math.floor(elapsed / 60)).padStart(2, '0')
  const ss = String(elapsed % 60).padStart(2, '0')

  return (
    <div className="stage">
      <Album />
      <div className="meta">
        <div className="progress-track">
          <div className="progress-fill" style={{ transform: `scaleX(${progress / 100})` }} />
        </div>
        <span className="time">{mm}:{ss}</span>
      </div>
    </div>
  )
}