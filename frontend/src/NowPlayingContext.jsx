import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { wakeDisplay as wakeDisplayRequest, sleepDisplay as sleepDisplayRequest } from './playbackActions'

const NowPlayingContext = createContext(null)
const POLL_INTERVAL_MS = 5000
// Slower cadence used while asleep — just enough to notice a wake (e.g. from
// an NFC-triggered /api/wake hit) without polling Spotify for no reason.
const STATUS_POLL_INTERVAL_MS = 2000

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
  const [awake, setAwake] = useState(true)
  // Mirrors `awake` for reads inside the poll loop's setTimeout callback,
  // which closes over stale state otherwise.
  const awakeRef = useRef(true)
  // Any in-flight fetch that started before this timestamp is stale and
  // should be discarded when it resolves.
  const ignoreStartedBeforeRef = useRef(0)
  const timeoutRef = useRef(null)

  const setAwakeState = useCallback((value) => {
    awakeRef.current = value
    setAwake(value)
  }, [])

  const fetchStatus = useCallback(() => {
    return fetch('/api/status')
      .then((res) => res.json())
      .then((data) => setAwakeState(!!data.awake))
  }, [setAwakeState])

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
  // While asleep, poll /api/status at a slower cadence instead of
  // /api/now_playing — no point hitting Spotify for a screen that's off —
  // and switch back to full polling the moment something (e.g. an
  // NFC-triggered /api/wake hit from the phone) flips it awake again.
  const scheduleNextPoll = useCallback(() => {
    clearTimeout(timeoutRef.current)
    const interval = awakeRef.current ? POLL_INTERVAL_MS : STATUS_POLL_INTERVAL_MS
    timeoutRef.current = setTimeout(() => {
      const poll = awakeRef.current
        ? fetchNowPlaying()
        : fetchStatus().then(() => (awakeRef.current ? fetchNowPlaying() : undefined))
      poll.finally(scheduleNextPoll)
    }, interval)
  }, [fetchNowPlaying, fetchStatus])

  useEffect(() => {
    fetchStatus().finally(() => {
      if (awakeRef.current) fetchNowPlaying()
      scheduleNextPoll()
    })
    return () => clearTimeout(timeoutRef.current)
  }, [fetchStatus, fetchNowPlaying, scheduleNextPoll])

  const wake = useCallback(async () => {
    const data = await wakeDisplayRequest()
    setAwakeState(data?.awake ?? true)
    fetchNowPlaying()
    scheduleNextPoll()
  }, [fetchNowPlaying, scheduleNextPoll, setAwakeState])

  const sleepDisplay = useCallback(async () => {
    const data = await sleepDisplayRequest()
    setAwakeState(data?.awake ?? false)
    scheduleNextPoll()
  }, [scheduleNextPoll, setAwakeState])

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
      value={{
        nowPlaying,
        prevTrack,
        nextTrack,
        awake,
        wake,
        sleepDisplay,
        applyNowPlaying,
        applyPreloadedTrack,
        reconcileNowPlaying,
      }}
    >
      {children}
    </NowPlayingContext.Provider>
  )
}

export function useNowPlaying() {
  return useContext(NowPlayingContext)
}
