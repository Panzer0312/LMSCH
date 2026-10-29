export type RGB = [number, number, number]

export interface LightState {
  preset?: number | null
  on?: boolean | null
  bri?: number | null
  fx?: number | null
  pal?: number | null
  colors?: RGB[] | null
  sx?: number | null
  ix?: number | null
  transition_ms?: number | null
}

export type BoardStatus = 'unknown' | 'online' | 'degraded' | 'offline'

export interface Board {
  mac: string
  ip: string
  name: string
  adopted: boolean
  firmware: string
  led_count: number
  rssi: number | null
  signal: number | null
  last_seen: number | null // unix seconds
  status: BoardStatus
  latency_ms: number | null
  offset_ms: number
  live: { on?: boolean; bri?: number; fx?: number; pal?: number; col?: number[][]; ps?: number }
  effects: string[]
  palettes: string[]
  /** WLED /json/fxdata strings, same index as effects; may be empty. */
  fxdata: string[]
  segments: Segment[]
  presets: Record<string, string> // id -> name
}

export type ProfileType = 'light' | 'video'
export type PlaybackMode = 'once' | 'loop' | 'idle_trigger'

export interface BoardSelection { all: boolean; macs: string[] }
/** segs: per-board WLED segment ids; a mac listed there is hit only on those segments. */
export interface CueTarget { all: boolean; macs: string[]; segs?: Record<string, number[]> | null }
export interface Segment { id: number; name: string; start: number; stop: number }

export interface Cue {
  id?: number
  start_s: number
  duration_s: number | null
  target: CueTarget
  state: LightState
  transition_ms: number | null
  label?: string | null
  color?: string | null // '#rrggbb'
}

export interface Marker { id: string; t: number; label: string; color: string | null }

export interface Onset { t: number; strength: number } // strength 0..1
export interface Onsets { rate: number; onsets: Onset[]; bpm: number | null }

/** Saved multi-cue template (server). lane: all = ALL lane, first = first target lane, same = paste lane. */
export interface TemplateCue {
  offset_s: number
  duration_s: number | null
  lane: 'all' | 'first' | 'same'
  state: LightState
  transition_ms: number | null
  label?: string | null
  color?: string | null
}
export interface SavedTemplate { id: number; name: string; cues: TemplateCue[]; created_at: string }

export interface ProfileIn {
  name: string
  type: ProfileType
  selection: BoardSelection
  base_state: LightState
  overrides: Record<string, LightState>
  media_id: number | null
  idle_media_id: number | null
  playback_mode: PlaybackMode
  cooldown_s: number
  /** Omit on update to leave stored markers untouched. */
  markers?: Marker[] | null
  /** Per-show display profile; wins over media and global default. */
  display_profile_id?: number | null
}

export interface Profile extends ProfileIn { id: number; sort: number; cues: Cue[]; markers: Marker[] }

export interface Media {
  id: number
  filename: string
  size: number
  duration_s: number
  width: number
  height: number
  codec: string
  used_by: string[]
  needs_transcode: boolean // not H.264/HEVC or above 1080p
  transcode: { state: 'running' | 'failed'; progress: number; error: string | null } | null
  /** Display aspect w/h after rotation. */
  aspect?: number
  display_profile_id?: number | null
}

export type Phase = 'none' | 'lights' | 'idle' | 'playing' | 'paused' | 'blackout'

export interface ShowState {
  phase: Phase
  blackout: boolean
  test_pattern: boolean
  profile: { id: number; name: string; type: ProfileType; mode: PlaybackMode } | null
  position: number | null
  duration: number | null
  cooldown_remaining: number
  audition: { profile_id: number; playing: boolean } | null
  player: { ready: boolean; path: string | null; time_pos: number | null; duration: number | null; paused: boolean; eof: boolean }
  last_trigger: LastTrigger | null
  setup_busy: boolean
  calibrating: boolean
}

export interface LastTrigger { ts: number; source: string; accepted: boolean; message: string }
export interface Version { commit: string; installed_at?: string }

export type CheckStatus = 'ok' | 'warn' | 'fail'
export interface PreflightCheck { id: string; label: string; status: CheckStatus; detail: string; fix: string | null }
export interface Preflight { checks: PreflightCheck[]; worst: CheckStatus }

/** GET /api/state and the WS snapshot payload. */
export interface AppState {
  boards: Board[]
  show: ShowState
  settings: Settings
  events: ShowEvent[]
  version: Version
  preflight?: Preflight
  display?: DisplayState
}

export interface DrmOutput { name: string; card: number; status: string; modes: string[]; enabled: boolean }
export interface AudioDevice { name: string; description: string }

export interface EventRow {
  id: number
  ts: number
  level: 'info' | 'warn' | 'error'
  kind: string
  message: string
  data: Record<string, unknown>
}
export interface EventStats { triggers: number; by_hour: { hour_ts: number; count: number }[] }

export interface FallbackResult { mac: string; name: string; ok: boolean; error: string | null }

export interface Settings {
  sync_offset_ms: number
  master_brightness: number // 0..1
  health_interval_s: number
  default_transition_ms: number
  log_level: string
  mpv_vo: string | null
  mpv_gpu_context: string | null
  mpv_drm_connector: string | null
  mpv_drm_mode: string | null
  mpv_audio_device: string | null
  master_volume: number
  volume_cap: number
  brightness_cap: number
  audio_delay_s: number
  show_lock: boolean
  active_profile_id: number | null
  /** Global default display profile ("Projector setup"). */
  display_profile_id?: number | null
}

export interface ShowEvent { ts: number; level: 'info' | 'warn' | 'error'; message: string; mac?: string }

export interface ThumbsMeta { count: number; interval: number; tile_w: number; tile_h: number }
export interface Waveform { rate: number; peaks: number[] } // peaks per second, 0..1

export interface Recommendation { id: string; title: string; ok: boolean; detail: string; fixable: boolean }

/** Contents of a profile export file (GET /api/profiles/:id/export). */
export interface ProfileExport {
  format: 'spookhub-profile'
  version: number
  profile: Omit<ProfileIn, 'media_id' | 'idle_media_id'>
  cues: Cue[]
  media_filename: string | null
  idle_media_filename: string | null
}

/** Projector geometry; mirrors backend DisplayGeometry. Corners order TL, TR, BR, BL. */
export interface DisplayGeometry {
  zoom: number // log2, -2..2
  pan_x: number
  pan_y: number
  scale_x: number
  scale_y: number
  align_x: number
  align_y: number
  rotate: 0 | 90 | 180 | 270
  aspect: number | null
  crop: { x: number; y: number; w: number; h: number }
  brightness: number
  contrast: number
  gamma: number
  saturation: number
  corners: [number, number][] // 4 × [dx, dy], fractions of output size
  mask: { top: number; right: number; bottom: number; left: number; feather: number }
  flip_h: boolean
  flip_v: boolean
}

export interface DisplayProfile { id: number; name: string; geometry: DisplayGeometry }

export type DisplaySource = { kind: 'pattern'; id: string } | { kind: 'media'; id: number } | { kind: 'idle' }

/** GET /api/display/state and WS type `display`. `geometry` is what mpv shows now. */
export interface DisplayState {
  setup: boolean
  source: DisplaySource | null
  live: DisplayGeometry | null
  resolved_profile_id: number | null
  geometry: DisplayGeometry
  /** False when mpv runs a vo without user-shader support; keystone/mask/flip are then ignored. */
  warp_supported: boolean
}

export interface PatternInfo { id: string; label: string; kind: 'image' | 'video' }

export interface BlackCheck { ok: boolean; black_level: number; sampled: number; message: string }
