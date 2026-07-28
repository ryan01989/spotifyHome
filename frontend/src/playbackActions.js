// Shared across all four actions so a burst of taps/swipes (duplicate
// pointer events, accidental double-fire, etc.) can't hammer Spotify's
// rate limiter — only the first call in a cooldown window goes through.
const ACTION_COOLDOWN_MS = 200
let lastActionAt = 0

async function postAction(path) {
  const now = Date.now()
  if (now - lastActionAt < ACTION_COOLDOWN_MS) {
    return { success: false }
  }
  lastActionAt = now

  try {
    const res = await fetch(path, { method: 'POST' })
    return await res.json().catch(() => ({ success: false }))
  } catch (err) {
    console.error(`Playback action failed: ${path}`, err)
    return { success: false }
  }
}

export function playNext() {
  return postAction('/api/next_track')
}

export function playPrevious() {
  return postAction('/api/previous_track')
}

export function pausePlayback() {
  return postAction('/api/pause_playback')
}

export function resumePlayback() {
  return postAction('/api/resume_playback')
}
