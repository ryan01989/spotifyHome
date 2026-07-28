import { useSpin } from './SpinContext'
import { useNowPlaying } from './NowPlayingContext'
import WarpedCover from './WarpedCover'

export default function CurrentTrackCover({ progressMs }) {
  const { nowPlaying } = useNowPlaying()
  const { spinSeconds } = useSpin()

  return (
    <>
      <div className="album-cover">
        <WarpedCover
          className="current"
          src={nowPlaying?.cover}
          style={{ '--spin-duration': `${spinSeconds}s` }}
        />
      </div>
    </>
  )
}
