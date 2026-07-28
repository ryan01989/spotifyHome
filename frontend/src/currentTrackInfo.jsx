import { useEffect, useRef, useState } from 'react'
import { formatTime } from './formatTime'
import { useNowPlaying } from './NowPlayingContext'
import { SWIPE_THRESHOLD_PX } from './useSwipeGesture'

// Matches the (name-based) track-change detection the backend already uses
// to decide a skip has landed (see spotify_api.py::_get_now_playing_after_change).
const FADE_DURATION_MS = 220
const TITLE_MAX_WIDTH_PX = 600
const TITLE_MARQUEE_GAP_PX = 64
const TITLE_MARQUEE_PX_PER_SEC = 70
const trackKey = (track) => (track ? `${track.name}::${track.artist}` : null)

export default function CurrentTrackInfo({ progressMs, phase, dragX }) {
    const { nowPlaying } = useNowPlaying()
    const [displayed, setDisplayed] = useState(nowPlaying)
    const [idleFading, setIdleFading] = useState(false)
    const keyRef = useRef(trackKey(nowPlaying))
    const titleMeasureRef = useRef(null)
    const [titleWidth, setTitleWidth] = useState(0)

    useEffect(() => {
        const newKey = trackKey(nowPlaying)
        if (newKey === keyRef.current) {
            setDisplayed(nowPlaying)
            return
        }
        keyRef.current = newKey

        // Mid-swipe, the drag-driven dissolve below has already hidden the
        // info by the time the new track lands, so just swap silently.
        if (phase && phase !== 'idle') {
            setDisplayed(nowPlaying)
            return
        }

        // Track changed with no swipe in progress (e.g. changed from Spotify
        // itself) - dissolve independently instead of popping.
        setIdleFading(true)
        const timeout = setTimeout(() => {
            setDisplayed(nowPlaying)
            setIdleFading(false)
        }, FADE_DURATION_MS)
        return () => clearTimeout(timeout)
    }, [nowPlaying, phase])

    useEffect(() => {
        const measure = () => {
            const el = titleMeasureRef.current
            if (!el) return
            // A hidden, always-single-copy measurer, so its scrollWidth
            // stays the true content width regardless of whether the
            // visible title is currently duplicated for the marquee.
            setTitleWidth(el.scrollWidth)
        }
        measure()
        window.addEventListener('resize', measure)
        return () => window.removeEventListener('resize', measure)
    }, [displayed?.name])

    const isOverflowing = titleWidth > TITLE_MAX_WIDTH_PX
    const marqueeDistance = titleWidth + TITLE_MARQUEE_GAP_PX
    const marqueeDuration = marqueeDistance / TITLE_MARQUEE_PX_PER_SEC

    const durationMs = displayed?.duration_ms ?? 0
    const progress = durationMs ? (progressMs / durationMs) * 100 : 0

    // While actively dragging, dissolve in lockstep with the swipe distance
    // instead of translating; the swipe surface still handles the slide.
    const dragOpacity = phase === 'dragging'
        ? Math.max(0, 1 - Math.min(1, Math.abs(dragX) / SWIPE_THRESHOLD_PX))
        : null
    const phaseClass = phase && phase !== 'idle' ? ` is-${phase}` : idleFading ? ' is-fading' : ''
    const style = dragOpacity !== null ? { opacity: dragOpacity } : undefined

    return (<>
        <div className={`track-info${phaseClass}`} style={style}>
            <div
                className={`track-name-wrap${isOverflowing ? ' is-overflowing' : ''}`}
                style={isOverflowing ? { width: `${TITLE_MAX_WIDTH_PX}px` } : undefined}
            >
                {isOverflowing ? (
                    <div
                        className="track-name-track"
                        style={{
                            animationDuration: `${marqueeDuration}s`,
                            '--marquee-gap': `${TITLE_MARQUEE_GAP_PX}px`,
                        }}
                        key={trackKey(displayed)}
                    >
                        <span className="track-name">{displayed?.name}</span>
                        <span className="track-name" aria-hidden="true">{displayed?.name}</span>
                    </div>
                ) : (
                    <span className="track-name">{displayed?.name}</span>
                )}
                <span className="track-name track-name-measure" ref={titleMeasureRef} aria-hidden="true">
                    {displayed?.name}
                </span>
            </div>
            <span className="track-artist">{displayed?.artist}</span>
        </div>
        <div className={`meta${phaseClass}`} style={style}>
            <div className="progress-track">
            <div className="progress-fill" style={{ transform: `scaleX(${progress / 100})` }} />
            </div>
            <div className="time-row">
                <span className="time">{formatTime(progressMs)}</span>
                <span className="time">-{formatTime(durationMs - progressMs)}</span>
            </div>
        </div>
    </>)
}