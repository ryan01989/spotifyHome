import { useEffect, useState } from 'react'
import { useSpin } from './SpinContext'

export default function Album() {
  const [cover, setCover] = useState(null)
  const { spinSeconds } = useSpin()

  useEffect(() => {
    fetch('/api/current_cover')
      .then((res) => res.json())
      .then((data) => setCover(data.cover))
  }, [])

  return (
    <div className="album-cover">
      <img
        className="current"
        src={cover}
        alt="Album Cover"
        style={{ '--spin-duration': `${spinSeconds}s` }}
      />
    </div>
  )
}
