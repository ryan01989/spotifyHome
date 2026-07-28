import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

const NowPlayingContext = createContext(null)
const POLL_INTERVAL_MS = 5000

export function NowPlayingProvider({ children }) {
  const [nowPlaying, setNowPlaying] = useState(null)
  // Any in-flight fetch that started before this timestamp is stale and
  // should be discarded when it resolves.
  const ignoreStartedBeforeRef = useRef(0)
  const timeoutRef = useRef(null)

  const fetchNowPlaying = useCallback(() => {
    const startedAt = Date.now()
    return fetch('/api/now_playing')
      .then((res) => res.json())
      .then((data) => {
        if (startedAt < ignoreStartedBeforeRef.current) return
        setNowPlaying(data)
      })
  }, [])

  // Self-reschedule so each cycle's timing is relative to the last fetch.
  const scheduleNextPoll = useCallback(() => {
    clearTimeout(timeoutRef.current)
    timeoutRef.current = setTimeout(() => {
      fetchNowPlaying().finally(scheduleNextPoll)
    }, POLL_INTERVAL_MS)
  }, [fetchNowPlaying])

  useEffect(() => {
    fetchNowPlaying()
    scheduleNextPoll()
    return () => clearTimeout(timeoutRef.current)
  }, [fetchNowPlaying, scheduleNextPoll])

  const applyNowPlaying = useCallback((data) => {
    ignoreStartedBeforeRef.current = Date.now()
    setNowPlaying(data)
    scheduleNextPoll()
  }, [scheduleNextPoll])

  return (
    <NowPlayingContext.Provider value={{ nowPlaying, applyNowPlaying }}>
      {children}
    </NowPlayingContext.Provider>
  )
}

export function useNowPlaying() {
  return useContext(NowPlayingContext)
}
