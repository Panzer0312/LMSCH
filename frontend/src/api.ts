import type { Board, Cue, Media, Profile, ProfileIn, Settings, LightState, ThumbsMeta, Waveform } from './types'

export class ApiError extends Error {
  status: number
  constructor(message: string, status = 0) {
    super(message)
    this.status = status
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const j = await r.json()
      if (j?.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* not json */ }
    throw new ApiError(msg, r.status)
  }
  return (await r.json()) as T
}

const post = <T = { ok: boolean }>(p: string, b: unknown = {}) => req<T>('POST', p, b)

export const api = {
  boards: {
    list: () => req<Board[]>('GET', '/api/boards'),
    scan: () => post<{ started: boolean }>('/api/boards/scan'),
    add: (ip: string) => post<Board>('/api/boards/add', { ip }),
    adopt: (mac: string, name: string) => post<Board>(`/api/boards/${mac}/adopt`, { name }),
    rename: (mac: string, name: string) => req<Board>('PATCH', `/api/boards/${mac}`, { name }),
    remove: (mac: string) => req<{ ok: boolean }>('DELETE', `/api/boards/${mac}`),
    identify: (mac: string) => post(`/api/boards/${mac}/identify`),
    setState: (mac: string, s: LightState) => post(`/api/boards/${mac}/state`, s),
    refresh: (mac: string) => post<Board>(`/api/boards/${mac}/refresh`),
    /** Board's current live state as a LightState (for 'capture from board'). */
    capture: (mac: string) => req<LightState>('GET', `/api/boards/${mac}/capture`),
  },
  profiles: {
    list: () => req<Profile[]>('GET', '/api/profiles'),
    get: (id: number) => req<Profile>('GET', `/api/profiles/${id}`),
    create: (p: ProfileIn) => post<Profile>('/api/profiles', p),
    update: (id: number, p: ProfileIn) => req<Profile>('PUT', `/api/profiles/${id}`, p),
    remove: (id: number) => req<{ ok: boolean }>('DELETE', `/api/profiles/${id}`),
    duplicate: (id: number) => post<Profile>(`/api/profiles/${id}/duplicate`),
    activate: (id: number) => post(`/api/profiles/${id}/activate`),
    setCues: (id: number, cues: Cue[]) => req<Profile>('PUT', `/api/profiles/${id}/cues`, cues),
    /** Start the real show on the projector at a position (activates the profile). */
    playFrom: (id: number, from_s: number) => post(`/api/profiles/${id}/play_from`, { from_s }),
  },
  show: {
    trigger: () => post<{ ok: boolean; message: string }>('/api/trigger'),
    play: (from_s = 0) => post('/api/show/play', { from_s }),
    stop: () => post('/api/show/stop'),
    seek: (t: number) => post('/api/show/seek', { t }),
    pause: (on: boolean) => post('/api/show/pause', { on }),
    next: () => post('/api/show/next'),
    deactivate: () => post('/api/show/deactivate'),
    blackout: (on: boolean) => post('/api/blackout', { on }),
    /** Live audition: report preview playhead; call on play/pause/seek and every ~1 s. */
    audition: (profile_id: number, t: number, playing: boolean) =>
      post<{ ok: boolean; message: string }>('/api/audition', { profile_id, t, playing }),
    auditionStop: () => post('/api/audition/stop'),
  },
  settings: {
    get: () => req<Settings>('GET', '/api/settings'),
    update: (s: Partial<Settings>) => req<Settings>('PUT', '/api/settings', s),
  },
  media: {
    list: () => req<Media[]>('GET', '/api/media'),
    remove: (id: number) => req<{ ok: boolean }>('DELETE', `/api/media/${id}`),
    fileUrl: (id: number) => `/api/media/${id}/file`,
    thumbsUrl: (id: number) => `/api/media/${id}/thumbs.jpg`,
    thumbs: (id: number) => req<ThumbsMeta>('GET', `/api/media/${id}/thumbs`),
    waveform: (id: number) => req<Waveform>('GET', `/api/media/${id}/waveform`),
    /** XHR instead of fetch: fetch has no upload progress events. */
    upload: (file: File, onProgress: (frac: number) => void) =>
      new Promise<Media>((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        xhr.open('POST', '/api/media')
        xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText))
          else {
            let msg = `${xhr.status}`
            try { msg = JSON.parse(xhr.responseText).detail ?? msg } catch { /* ignore */ }
            reject(new ApiError(msg))
          }
        }
        xhr.onerror = () => reject(new ApiError('network error'))
        const fd = new FormData()
        fd.append('file', file)
        xhr.send(fd)
      }),
  },
}
