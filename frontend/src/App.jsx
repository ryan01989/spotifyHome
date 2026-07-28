import { useEffect, useState } from 'react'
import CurrentTrackCover from './currentTrackCover'
import CurrentTrackInfo from './currentTrackInfo'
import { useNowPlaying } from './NowPlayingContext'
import { useSwipeGesture } from './useSwipeGesture'
import { playNext, playPrevious, pausePlayback, resumePlayback } from './playbackActions'
import { useSimulatedProgress } from './formatTime'
import './App.css'

export default function App() {
  const { nowPlaying, applyNowPlaying } = useNowPlaying()

  // nowPlaying.is_playing only updates after a network round-trip, so a
  // second tap arriving before that lands would still see the pre-tap
  // state 
  // Track the state locally and flip when the user acts; defer
  // back to server truth once a poll confirms it.
  const [optimisticPlaying, setOptimisticPlaying] = useState(null)
  useEffect(() => {
    setOptimisticPlaying(null)
  }, [nowPlaying?.is_playing])

  const isPlaying = optimisticPlaying ?? nowPlaying?.is_playing ?? false
  const displayProgressMs = useSimulatedProgress(nowPlaying?.progress_ms, nowPlaying?.duration_ms, isPlaying)

  const handleNext = async () => {
    const data = await playNext()
    if (data?.now_playing) applyNowPlaying(data.now_playing)
  }

  const handlePrevious = async () => {
    const data = await playPrevious()
    if (data?.now_playing) applyNowPlaying(data.now_playing)
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
