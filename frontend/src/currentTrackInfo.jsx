import { formatTime } from './formatTime'
import { useNowPlaying } from './NowPlayingContext'

export default function CurrentTrackInfo({ progressMs }) {
    const { nowPlaying } = useNowPlaying()
    const durationMs = nowPlaying?.duration_ms ?? 0
    const progress = durationMs ? (progressMs / durationMs) * 100 : 0
    return (<>
        <div className="track-info">
            <span className="track-name">{nowPlaying?.name}</span>
            <span className="track-artist">{nowPlaying?.artist}</span>
        </div>
        <div className="meta">
            <div className="progress-track">
            <div className="progress-fill" style={{ transform: `scaleX(${progress / 100})` }} />
            </div>
            <span className="time">{formatTime(progressMs)}</span>
        </div>
    </>)
}