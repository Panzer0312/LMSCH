import type { Board, Cue, Media, Profile, ProfileIn, Settings, LightState, ThumbsMeta, Waveform, Recommendation, ProfileExport, AppState, Preflight, DrmOutput, AudioDevice, EventRow, EventStats, FallbackResult, LastTrigger, Marker, Onsets, SavedTemplate, TemplateCue, SceneState, Moment, MomentIn, MomentState, DisplayGeometry, DisplayProfile, DisplaySource, DisplayState, PatternInfo, BlackCheck } from './types'

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
  state: () => req<AppState>('GET', '/api/state'),
  /** Clears the jukebox host so the next device joining /music becomes host. */
  jukeboxReset: () => post('/api/jukebox_reset'),
  showLock: (on: boolean, pin?: string) => post<{ show_lock: boolean }>('/api/show_lock', { on, pin: pin ?? null }),
  setup: {
    outputs: () => req<DrmOutput[]>('GET', '/api/setup/outputs'),
    audioDevices: () => req<AudioDevice[]>('GET', '/api/setup/audio_devices'),
    testSound: () => post<{ ok: boolean; started: boolean }>('/api/setup/test_sound'),
    triggers: () => req<{ last_trigger: LastTrigger | null }>('GET', '/api/setup/triggers'),
    calibration: (on: boolean) => post<{ ok: boolean; calibrating: boolean }>('/api/setup/calibration', { on }),
  },
  preflight: {
    get: () => req<Preflight>('GET', '/api/preflight'),
    testAll: () => post<{ ok: boolean; started: boolean }>('/api/preflight/test_all'),
  },
  events: {
    list: (params: { since?: number; kind?: string; limit?: number } = {}) => {
      const q = new URLSearchParams()
      if (params.since !== undefined) q.set('since', String(params.since))
      if (params.kind) q.set('kind', params.kind)
      if (params.limit !== undefined) q.set('limit', String(params.limit))
      const qs = q.toString()
      return req<EventRow[]>('GET', `/api/events${qs ? `?${qs}` : ''}`)
    },
    stats: (since?: number) => req<EventStats>('GET', `/api/events/stats${since === undefined ? '' : `?since=${since}`}`),
  },
  boards: {
    setOffset: (mac: string, offset_ms: number) => req<Board>('PATCH', `/api/boards/${mac}`, { offset_ms }),
    fallbackAll: () => post<{ results: FallbackResult[] }>('/api/boards/fallback'),
    fallback: (mac: string) => post<{ results: FallbackResult[] }>(`/api/boards/${mac}/fallback`),
    list: () => req<Board[]>('GET', '/api/boards'),
    scan: () => post<{ started: boolean }>('/api/boards/scan'),
    add: (ip: string) => post<Board>('/api/boards/add', { ip }),
    adopt: (mac: string, name: string) => post<Board>(`/api/boards/${mac}/adopt`, { name }),
    rename: (mac: string, name: string) => req<Board>('PATCH', `/api/boards/${mac}`, { name }),
    remove: (mac: string) => req<{ ok: boolean }>('DELETE', `/api/boards/${mac}`),
    clearManual: (mac: string) => req<{ ok: boolean }>('DELETE', `/api/boards/${mac}/state`),
    identify: (mac: string) => post(`/api/boards/${mac}/identify`),
    setState: (mac: string, s: LightState) => post(`/api/boards/${mac}/state`, s),
    refresh: (mac: string) => post<Board>(`/api/boards/${mac}/refresh`),
    /** Board's current live state as a LightState (for 'capture from board'). */
    capture: (mac: string) => req<LightState>('GET', `/api/boards/${mac}/capture`),
    recommendations: (mac: string) => req<Recommendation[]>('GET', `/api/boards/${mac}/recommendations`),
    /** Writes to the board's /json/cfg — confirm with the user first. */
    applyRecommendation: (mac: string, id: string) => post(`/api/boards/${mac}/recommendations/${id}/apply`),
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
    setMarkers: (id: number, markers: Marker[]) => req<Profile>('PUT', `/api/profiles/${id}/markers`, markers),
    /** Start the real show on the projector at a position (activates the profile). */
    playFrom: (id: number, from_s: number) => post(`/api/profiles/${id}/play_from`, { from_s }),
    /** Download URL (Content-Disposition attachment). */
    exportUrl: (id: number) => `/api/profiles/${id}/export`,
    /** 422 with a message mentioning the video when it can't be matched by filename: retry with media_id. */
    import: (data: ProfileExport, media_id?: number | null) => post<Profile>('/api/profiles/import', { data, media_id: media_id ?? null }),
  },
  show: {
    trigger: () => post<{ ok: boolean; message: string }>('/api/show/trigger'),
    play: (from_s = 0) => post('/api/show/play', { from_s }),
    stop: () => post('/api/show/stop'),
    seek: (t: number) => post('/api/show/seek', { t }),
    pause: (on: boolean) => post('/api/show/pause', { on }),
    next: () => post('/api/show/next'),
    deactivate: () => post('/api/show/deactivate'),
    blackout: (on: boolean) => post('/api/blackout', { on }),
    /** Live audition: report preview playhead; call on play/pause/seek and every ~1 s. */
    audition: (profile_id: number, t: number, playing: boolean, muted: string[] = []) =>
      post<{ ok: boolean; message: string }>('/api/audition', { profile_id, t, playing, muted }),
    auditionStop: () => post('/api/audition/stop'),
    testPattern: (on: boolean) => post('/api/show/test_pattern', { on }),
  },
  scene: {
    get: () => req<SceneState>('GET', '/api/scene'),
    skip: () => post<SceneState>('/api/scene/skip'),
    /** null clears the pin. */
    pin: (profile_id: number | null) => post<SceneState>('/api/scene/pin', { profile_id }),
  },
  moments: {
    list: () => req<Moment[]>('GET', '/api/moments'),
    state: () => req<MomentState>('GET', '/api/moments/state'),
    create: (m: MomentIn) => post<Moment>('/api/moments', m),
    update: (id: number, m: MomentIn) => req<Moment>('PUT', `/api/moments/${id}`, m),
    remove: (id: number) => req<{ ok: boolean }>('DELETE', `/api/moments/${id}`),
    /** ids in new order. */
    reorder: (ids: number[]) => req<{ ok: boolean }>('PUT', '/api/moments/order', ids),
    /** variant: test a specific variant (ignores cooldown). */
    play: (id: number, variant?: number) => post<MomentState>(`/api/moments/${id}/play`, variant == null ? {} : { variant }),
    stop: () => post<MomentState>('/api/moments/stop'),
  },
  sounds: {
    /** Soundboard: plays on the fx player; allowed under show lock. */
    play: (media_id: number, volume?: number) => post('/api/sounds/play', volume == null ? { media_id } : { media_id, volume }),
    stop: () => post('/api/sounds/stop'),
  },
  templates: {
    list: () => req<SavedTemplate[]>('GET', '/api/templates'),
    create: (name: string, cues: TemplateCue[]) => post<SavedTemplate>('/api/templates', { name, cues }),
    remove: (id: number) => req<{ ok: boolean }>('DELETE', `/api/templates/${id}`),
  },
  display: {
    profiles: () => req<DisplayProfile[]>('GET', '/api/display/profiles'),
    createProfile: (name: string, geometry: DisplayGeometry) => post<DisplayProfile>('/api/display/profiles', { name, geometry }),
    updateProfile: (id: number, p: { name?: string; geometry?: DisplayGeometry }) =>
      req<DisplayProfile>('PUT', `/api/display/profiles/${id}`, p),
    /** Server clears every reference to it. */
    deleteProfile: (id: number) => req<{ ok: boolean }>('DELETE', `/api/display/profiles/${id}`),
    state: () => req<DisplayState>('GET', '/api/display/state'),
    /** Enter/leave setup mode or switch source. 409 during blackout. */
    setup: (on: boolean, source?: DisplaySource) => post<DisplayState>('/api/display/setup', source ? { on, source } : { on }),
    /** Apply without saving; server coalesces. */
    live: (geometry: DisplayGeometry) => post('/api/display/live', { geometry }),
    seek: (t: number) => post('/api/display/seek', { t }),
    patterns: () => req<PatternInfo[]>('GET', '/api/display/patterns'),
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
    onsets: (id: number) => req<Onsets>('GET', `/api/media/${id}/onsets`),
    transcode: (id: number) => post(`/api/media/${id}/transcode`),
    blackCheck: (id: number) => req<BlackCheck>('GET', `/api/media/${id}/black_check`),
    setDisplayProfile: (id: number, display_profile_id: number | null) =>
      req<{ id: number; display_profile_id: number | null }>('PATCH', `/api/media/${id}`, { display_profile_id }),
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
  auth: {
    status: () => req<{ pin_enabled: boolean }>('GET', '/api/auth/status'),
    login: (pin: string) => post('/api/auth/login', { pin }),
    logout: () => post('/api/auth/logout'),
    /** null/empty clears the PIN. */
    setPin: (pin: string | null) => req<{ pin_enabled: boolean }>('PUT', '/api/auth/pin', { pin }),
    getTriggerToken: () => req<{ token: string | null }>('GET', '/api/auth/trigger_token'),
    setTriggerToken: (token: string | null) => req<{ token: string | null }>('PUT', '/api/auth/trigger_token', { token }),
  },
  backup: {
    downloadUrl: '/api/backup',
    /** Server restarts after a successful restore; reload the page a few seconds later. */
    restore: async (file: File): Promise<{ ok: boolean; notes: string[] }> => {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch('/api/backup/restore', { method: 'POST', body: fd })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new ApiError(j?.detail ?? `${r.status}`, r.status)
      return j
    },
  },
}
