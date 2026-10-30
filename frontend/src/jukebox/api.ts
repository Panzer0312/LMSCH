import type { JoinResult, JukeboxState, JukeEntry, JukeGuest, JukeGuestRef, JukeRole, JukeSettings, SearchResult, SpotifyDevice } from './types'

/** Separate from the main app's ApiError: guests never load the main bundle. */
export class JukeError extends Error {
  status: number
  constructor(message: string, status = 0) {
    super(message)
    this.status = status
  }
}

const STORE_KEY = 'spookhub.jukebox'

export interface Saved { token: string | null; name: string }

export function loadSaved(): Saved {
  try {
    const j = JSON.parse(localStorage.getItem(STORE_KEY) ?? '{}')
    return { token: typeof j.token === 'string' ? j.token : null, name: typeof j.name === 'string' ? j.name : '' }
  } catch {
    return { token: null, name: '' }
  }
}

export function saveSaved(s: Saved) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(s)) } catch { /* private mode: session-only */ }
}

let token: string | null = loadSaved().token
export const setToken = (t: string | null) => { token = t }
export const getToken = () => token

async function req<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['content-type'] = 'application/json'
  if (token) headers['X-Jukebox-Token'] = token
  let r: Response
  try {
    r = await fetch(`/api/jukebox${path}`, {
      method, headers, signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new JukeError('Can’t reach the hub. Check your WiFi.', 0)
  }
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const j = await r.json()
      if (j?.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* not json */ }
    throw new JukeError(msg, r.status)
  }
  return (await r.json()) as T
}

type Ok = { ok: boolean }
const post = <T = Ok>(p: string, b: unknown = {}) => req<T>('POST', p, b)

export const jukeApi = {
  join: (name: string) => post<JoinResult>('/join', { name }),
  me: () => req<{ guest: JukeGuestRef; role: JukeRole }>('GET', '/me'),
  state: () => req<JukeboxState>('GET', '/state'),
  search: (q: string, signal?: AbortSignal) =>
    req<SearchResult[]>('GET', `/search?q=${encodeURIComponent(q)}`, undefined, signal),
  add: (uri: string) => post<JukeEntry>('/queue', { uri }),
  remove: (id: number) => req<Ok>('DELETE', `/queue/${id}`),
  voteSkip: () => post<{ ok: boolean; skipped: boolean; votes: number; votes_needed: number }>('/vote_skip'),
  admin: {
    // The uri pins the skip to the track on screen, so a stale tap can't skip the next song.
    skip: (uri?: string) => post('/admin/skip', uri ? { uri } : {}),
    pause: () => post('/admin/pause'),
    resume: () => post('/admin/resume'),
    clear: () => post('/admin/clear'),
    bump: (id: number) => post(`/admin/queue/${id}/bump`),
    settings: (s: Partial<JukeSettings>) => req<JukeSettings>('PUT', '/admin/settings', s),
    guests: () => req<JukeGuest[]>('GET', '/admin/guests'),
    ban: (gid: string, banned: boolean) => post(`/admin/guests/${gid}/ban`, { banned }),
    makeAdmin: (gid: string) => post(`/admin/guests/${gid}/make_admin`),
    spotify: (b: { client_id?: string; device_name?: string }) => req<Ok>('PUT', '/admin/spotify', b),
    login: () => req<{ url: string }>('GET', '/admin/spotify/login'),
    code: (url: string) => post('/admin/spotify/code', { url }),
    logout: () => post('/admin/spotify/logout'),
    devices: () => req<SpotifyDevice[]>('GET', '/admin/spotify/devices'),
  },
}
