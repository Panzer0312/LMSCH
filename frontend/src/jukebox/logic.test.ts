import { describe, expect, it } from 'vitest'
import { fmtMs, friendlyError, initialLive, isLoopback, isSpotifyTrackLink, joinUrl, myPending, progressMs, reduceJuke, voteKey } from './logic'
import type { JukeboxState, JukeNow } from './types'

const now = (o: Partial<JukeNow> = {}): JukeNow => ({
  uri: 'spotify:track:a', title: 't', artist: 'a', album: 'b', art_url: null, duration_ms: 200_000, explicit: false,
  progress_ms: 10_000, at: 1000, is_playing: true, entry_id: 1, requested_by: null, votes: 0, votes_needed: 2, ...o,
})

const state = (o: Partial<JukeboxState> = {}): JukeboxState => ({
  spotify: { client_id_set: true, connected: true, account: 'x', device_name: 'Spookhub', device_found: true, redirect_uri: 'r', error: null },
  now: null, queue: [], history: [], paused: null,
  settings: { accepting: true, per_guest_limit: 3, allow_explicit: true, fallback_uri: null, volume: 70, skip_vote_ratio: 0.5, max_track_min: 10 },
  guests_active: 1, admin_id: null, port80: false, ...o,
})

describe('progressMs', () => {
  it('adds sample age and local elapsed while playing', () => {
    // sampled at 1000 s hub time, received 1 s later, 2 s passed locally
    expect(progressMs(now(), 1_001_000, 1_003_000)).toBe(13_000)
  })
  it('clamps sample age when clocks are skewed', () => {
    expect(progressMs(now(), 9_999_000_000, 9_999_000_000)).toBe(25_000)
    expect(progressMs(now(), 1, 1)).toBe(10_000)
  })
  it('freezes when paused and caps at duration', () => {
    expect(progressMs(now({ is_playing: false }), 1_001_000, 1_100_000)).toBe(10_000)
    expect(progressMs(now(), 1_000_000, 2_000_000)).toBe(200_000)
  })
})

describe('reduceJuke', () => {
  it('takes snapshot and jukebox messages, ignores others', () => {
    const s = state()
    const a = reduceJuke(initialLive, { type: 'snapshot', data: { jukebox: s } }, 5)
    expect(a).toEqual({ ...initialLive, state: s, recvMs: 5 })
    const s2 = state({ paused: 'show' })
    expect(reduceJuke(a, { type: 'jukebox', data: s2 }, 9)).toEqual({ ...initialLive, state: s2, recvMs: 9 })
    expect(reduceJuke(a, { type: 'board', data: {} }, 9)).toBe(a)
    expect(reduceJuke(a, { type: 'snapshot', data: {} }, 9)).toBe(a)
  })
  it('keeps guests from jukebox_guests across state updates', () => {
    const g = [{ id: 'a', name: 'A', banned: false, last_seen: 1, pending: 0, is_admin: true }]
    const a = reduceJuke(initialLive, { type: 'jukebox_guests', data: g }, 7)
    expect(a.guests).toBe(g)
    expect(a.guestsMs).toBe(7)
    const b = reduceJuke(a, { type: 'jukebox', data: state() }, 8)
    expect(b.guests).toBe(g)
  })
})

describe('joinUrl', () => {
  it('uses bare hostname on port 80, origin otherwise', () => {
    const loc = { hostname: '192.168.1.5', origin: 'http://192.168.1.5:8080' }
    expect(joinUrl(loc, true)).toBe('http://192.168.1.5/music')
    expect(joinUrl(loc, false)).toBe('http://192.168.1.5:8080/music')
  })
  it('detects loopback', () => {
    expect(isLoopback('localhost')).toBe(true)
    expect(isLoopback('127.0.0.1')).toBe(true)
    expect(isLoopback('192.168.1.5')).toBe(false)
  })
})

describe('helpers', () => {
  it('formats ms', () => {
    expect(fmtMs(0)).toBe('0:00')
    expect(fmtMs(61_999)).toBe('1:01')
  })
  it('recognizes spotify track links', () => {
    expect(isSpotifyTrackLink('https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=x')).toBe(true)
    expect(isSpotifyTrackLink('https://open.spotify.com/intl-de/track/4uLU6hMCjMI75M1A2tKUQC')).toBe(true)
    expect(isSpotifyTrackLink('spotify:track:4uLU6hMCjMI75M1A2tKUQC')).toBe(true)
    expect(isSpotifyTrackLink('thriller')).toBe(false)
  })
  it('counts my pending songs', () => {
    const e = (id: number, gid: string) => ({ ...now(), id, guest: { id: gid, name: gid }, added_at: 0, bumped: false })
    expect(myPending(state({ queue: [e(1, 'a'), e(2, 'b'), e(3, 'a')] }), 'a')).toBe(2)
  })
  it('vote key changes per entry', () => {
    expect(voteKey(now())).not.toBe(voteKey(now({ entry_id: 2 })))
  })
  it('maps 503 to a friendly message', () => {
    expect(friendlyError(503, 'spotify not connected')).toMatch(/host/)
    expect(friendlyError(409, 'limit reached')).toBe('limit reached')
  })
})
