import { createContext, useContext, useEffect, useState } from 'react'

const SpinContext = createContext(null)
const FALLBACK_SPIN_DURATION = 3

export function SpinProvider({ children, initialSeconds = FALLBACK_SPIN_DURATION }) {
  const [spinSeconds, setSpinSeconds] = useState(initialSeconds)

  useEffect(() => {
    fetch('/presets.json')
      .then((res) => res.json())
      .then((data) => {
        if (data.defaultSpinDuration != null) {
          setSpinSeconds(data.defaultSpinDuration)
        }
      })
      .catch(() => {})
  }, [])

  return (
    <SpinContext.Provider value={{ spinSeconds, setSpinSeconds }}>
      {children}
    </SpinContext.Provider>
  )
}

export function useSpin() {
  return useContext(SpinContext)
}
