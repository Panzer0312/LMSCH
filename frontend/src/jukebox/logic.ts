import type { JukeboxState, JukeGuest, JukeNow } from './types'

/**
 * Store value: the hub state plus the local time it arrived (for clock-skew-safe progress).
 * `guests` comes only to the host's socket (`jukebox_guests`).
 */
export interface JukeLive {
  state: JukeboxState | null
  recvMs: number
  guests: JukeGuest[] | null
  guestsMs: number
}

export const initialLive: JukeLive = { state: null, recvMs: 0, guests: null, guestsMs: 0 }

export interface JukeMsg { type: string; data: unknown }

export function reduceJuke(s: JukeLive, m: JukeMsg, recvMs: number): JukeLive {
  if (m.type === 'snapshot') {
    const d = m.data as { jukebox?: JukeboxState } | null
    return d?.jukebox ? { ...s, state: d.jukebox, recvMs } : s
  }
  if (m.type === 'jukebox') return { ...s, state: m.data as JukeboxState, recvMs }
  if (m.type === 'jukebox_guests') return { ...s, guests: m.data as JukeGuest[], guestsMs: recvMs }
  return s
}

/*
 * `at` is the hub clock, the phone clock may be off by minutes. The hub-side age of the sample
 * (recv − at) is normally a few seconds (poll interval ≤ 15 s), so clamp it; local elapsed time
 * since receipt uses only the phone clock.
 */
const MAX_SAMPLE_AGE_MS = 15000

export function progressMs(now: JukeNow, recvMs: number, nowMs: number): number {
  if (!now.is_playing) return Math.min(now.progress_ms, now.duration_ms)
  const sampleAge = Math.min(MAX_SAMPLE_AGE_MS, Math.max(0, recvMs - now.at * 1000))
  const local = Math.max(0, nowMs - recvMs)
  return Math.max(0, Math.min(now.duration_ms, now.progress_ms + sampleAge + local))
}

export function fmtMs(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

const LOOPBACK = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])
export const isLoopback = (hostname: string) => LOOPBACK.has(hostname)

/** Port 80 is proxied by the hub when the backend saw it listening; then the short URL works. */
export function joinUrl(loc: { hostname: string; origin: string }, port80: boolean): string {
  return port80 ? `http://${loc.hostname}/music` : `${loc.origin}/music`
}

/** Spotify track link or URI pasted into the search box; the backend resolves it. */
export function isSpotifyTrackLink(q: string): boolean {
  return /^spotify:track:[A-Za-z0-9]+$/.test(q.trim()) || /open\.spotify\.com\/(intl-[a-z-]+\/)?track\/[A-Za-z0-9]+/.test(q)
}

export function myPending(state: JukeboxState, guestId: string): number {
  return state.queue.filter((e) => e.guest.id === guestId).length
}

export function queuedUris(state: JukeboxState | null): Set<string> {
  return new Set(state?.queue.map((e) => e.uri) ?? [])
}

/** Key for "already voted": votes reset per track, and the same uri may come back later. */
export function voteKey(now: JukeNow): string {
  return `${now.entry_id ?? 'x'}:${now.uri}`
}

/**
 * Why nothing plays although songs wait. The hub starts the queue only with Spotify connected,
 * the speaker found and nothing paused; otherwise the queue sits silently.
 */
export function idleReason(state: JukeboxState): string {
  const sp = state.spotify
  if (!sp.connected) return 'Spotify isn’t connected yet. The host sets it up on the Host tab.'
  if (state.queue.length === 0) return 'Add a song to get the party going.'
  if (state.paused) return 'Paused. The queue waits.'
  if (sp.error) return sp.error
  if (!sp.device_found) {
    return `Speaker “${sp.device_name}” not found. On a phone in this WiFi open Spotify, tap the devices icon and pick “${sp.device_name}” once.`
  }
  return 'Starting the next song…'
}

/** User-facing text for HTTP failures where the server detail is too technical. */
export function friendlyError(status: number, detail: string): string {
  // 503 also means "speaker not found", which the host can fix; keep that detail.
  if (status === 503) return detail.startsWith('Speaker') ? detail : 'Spotify isn’t connected yet. Ask the host to set it up.'
  if (status === 0) return detail
  if (status >= 500) return 'The hub hiccupped. Try again.'
  return detail
}
