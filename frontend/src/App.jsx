import { useEffect, useState } from 'react'
import CurrentTrackCover from './currentTrackCover'
import CurrentTrackInfo from './currentTrackInfo'
import { useNowPlaying } from './NowPlayingContext'
import { useSwipeGesture } from './useSwipeGesture'
import { playNext, playPrevious, pausePlayback, resumePlayback } from './playbackActions'
import { useSimulatedProgress } from './formatTime'
import './App.css'

export default function App() {
  const { nowPlaying, applyNowPlaying, applyPreloadedTrack, reconcileNowPlaying } = useNowPlaying()

  // nowPlaying.is_playing only updates after a network round-trip, so a
  // second tap arriving before that lands would still see the pre-tap
  // state. Track the state locally and flip when the user acts; defer
  // back to server truth once a poll confirms it.
  const [optimisticPlaying, setOptimisticPlaying] = useState(null)
  useEffect(() => {
    setOptimisticPlaying(null)
  }, [nowPlaying?.is_playing])

  const isPlaying = optimisticPlaying ?? nowPlaying?.is_playing ?? false
  const displayProgressMs = useSimulatedProgress(nowPlaying?.progress_ms, nowPlaying?.duration_ms, isPlaying)

  // If the neighboring track has already been preloaded, show it the
  // instant the swipe commits and reconcile with the real response once it
  // lands in the background. Otherwise fall back to waiting on the request
  // itself, so the swipe never reveals stale (pre-skip) track info.
  const handleNext = () => {
    const preloaded = applyPreloadedTrack('next')
    const request = playNext().then((data) => reconcileNowPlaying(data?.now_playing ?? null))
    if (!preloaded) return request
  }

  const handlePrevious = () => {
    const preloaded = applyPreloadedTrack('prev')
    const request = playPrevious().then((data) => reconcileNowPlaying(data?.now_playing ?? null))
    if (!preloaded) return request
  }

  const handleTogglePlay = async (shouldPlay) => {
    setOptimisticPlaying(shouldPlay)
    const data = await (shouldPlay ? resumePlayback() : pausePlayback())
    if (data?.now_playing) applyNowPlaying(data.now_playing)
  }

  const { dragX, phase, handlers } = useSwipeGesture({
    isPlaying,
    onNext: handleNext,
    onPrevious: handlePrevious,
    onTogglePlay: handleTogglePlay,
  })

  return (
    <div className="stage" {...handlers}>
      <div
        className={`gesture-surface${phase !== 'idle' ? ` is-${phase}` : ''}`}
        style={{ transform: `translateX(${dragX}px)` }}
      >
        <CurrentTrackCover />
      </div>
      <CurrentTrackInfo progressMs={displayProgressMs} phase={phase} dragX={dragX} />
    </div>
  )
}
