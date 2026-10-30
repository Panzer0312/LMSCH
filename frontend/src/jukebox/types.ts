/* Jukebox wire types. Mirror the shared spec exactly; the backend serializes these shapes. */

export interface JukeTrack {
  uri: string
  title: string
  artist: string
  album: string
  art_url: string | null
  duration_ms: number
  explicit: boolean
}

export interface JukeGuestRef { id: string; name: string }

export interface JukeEntry extends JukeTrack {
  id: number
  guest: JukeGuestRef
  added_at: number
  bumped: boolean
}

export interface JukeNow extends JukeTrack {
  progress_ms: number
  /** Epoch seconds (hub clock) when progress_ms was sampled. */
  at: number
  is_playing: boolean
  entry_id: number | null
  requested_by: JukeGuestRef | null
  votes: number
  votes_needed: number
}

export interface JukeSettings {
  accepting: boolean
  per_guest_limit: number
  allow_explicit: boolean
  fallback_uri: string | null
  volume: number
  skip_vote_ratio: number
  max_track_min: number
}

export interface JukeHistoryItem {
  title: string
  artist: string
  art_url: string | null
  guest: JukeGuestRef | null
  played_at: number
}

export interface JukeboxState {
  spotify: {
    client_id_set: boolean
    connected: boolean
    account: string | null
    device_name: string
    device_found: boolean
    redirect_uri: string
    error: string | null
  }
  now: JukeNow | null
  queue: JukeEntry[]
  history: JukeHistoryItem[]
  paused: 'admin' | 'show' | null
  settings: JukeSettings
  guests_active: number
  admin_id: string | null
  port80: boolean
}

export interface JukeGuest extends JukeGuestRef {
  banned: boolean
  last_seen: number
  pending: number
  is_admin: boolean
}

export type JukeRole = 'admin' | 'guest'

export interface JoinResult { token: string; guest: JukeGuestRef; role: JukeRole }

export type SearchResult = JukeTrack & { queued: boolean }

export interface SpotifyDevice { id: string; name: string; type: string; is_active: boolean }
