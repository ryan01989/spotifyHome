import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

const NowPlayingContext = createContext(null)
const POLL_INTERVAL_MS = 5000

// Warms the browser's image cache for a track we might swipe to next, so
// its cover is already decoded and ready by the time it needs to render.
function preloadCover(src) {
  if (!src) return
  new Image().src = src
}

export function NowPlayingProvider({ children }) {
  const [nowPlaying, setNowPlaying] = useState(null)
  const [prevTrack, setPrevTrack] = useState(null)
  const [nextTrack, setNextTrack] = useState(null)
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
        setNowPlaying(data.now_playing)
        setPrevTrack(data.prev)
        setNextTrack(data.next)
        preloadCover(data.prev?.cover)
        preloadCover(data.next?.cover)
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

  // Called the instant a skip/back swipe is committed. Shows the already-
  // preloaded neighbor track immediately instead of waiting on the network,
  // so the swipe never lingers on the track being left. Returns false (and
  // changes nothing) when there's no preloaded data to show yet, so the
  // caller can fall back to waiting for the real request.
  const applyPreloadedTrack = useCallback((direction) => {
    const track = direction === 'next' ? nextTrack : prevTrack
    if (!track) return false
    ignoreStartedBeforeRef.current = Date.now()
    setNowPlaying((current) => ({
      ...track,
      progress_ms: 0,
      is_playing: current?.is_playing ?? track.is_playing,
    }))
    // Push the periodic poll back out — otherwise it could fire before
    // Spotify's own state has caught up to the skip and stomp this
    // optimistic update with the track we just left.
    scheduleNextPoll()
    return true
  }, [nextTrack, prevTrack, scheduleNextPoll])

  // Reconciles with the authoritative track once the skip/back request
  // actually resolves (correcting an optimistic guess if it was wrong), and
  // refreshes prev/next so the following swipe has fresh data to preload.
  const reconcileNowPlaying = useCallback((data) => {
    ignoreStartedBeforeRef.current = Date.now()
    if (data) setNowPlaying(data)
    fetchNowPlaying().finally(scheduleNextPoll)
  }, [fetchNowPlaying, scheduleNextPoll])

  return (
    <NowPlayingContext.Provider
      value={{ nowPlaying, prevTrack, nextTrack, applyNowPlaying, applyPreloadedTrack, reconcileNowPlaying }}
    >
      {children}
    </NowPlayingContext.Provider>
  )
}

export function useNowPlaying() {
  return useContext(NowPlayingContext)
}
