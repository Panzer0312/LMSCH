import { createContext, useContext, useSyncExternalStore } from 'react'
import type { Board, DisplayState, Preflight, SceneState, Settings, ShowEvent, ShowState, Version } from './types'

export interface Live {
  connected: boolean
  boards: Record<string, Board>
  show: ShowState | null
  settings: Settings | null
  events: ShowEvent[]
  /** Bumped when backend says profiles/media changed, so pages can refetch. */
  profilesRev: number
  mediaRev: number
  boardsRev: number
  /** Bumped on WS `display_profiles_changed` (create/edit/delete/import). */
  displayProfilesRev: number
  /** media id -> transcode progress 0..1 (live, from WS) */
  transcodes: Record<number, number>
  preflight: Preflight | null
  version: Version | null
  scene: SceneState | null
}

export const initial: Live = {
  connected: false, boards: {}, show: null, settings: null, events: [],
  profilesRev: 0, mediaRev: 0, boardsRev: 0, displayProfilesRev: 0, transcodes: {},
  preflight: null, version: null, scene: null,
}
export const Ctx = createContext<Live>(initial)

export interface Msg { type: string; data: any }

export function reduce(s: Live, m: Msg): Live {
  switch (m.type) {
    case 'snapshot':
      return {
        ...s,
        boards: Object.fromEntries((m.data.boards as Board[]).map((b) => [b.mac, b])),
        show: m.data.show, settings: m.data.settings, events: m.data.events.slice(-20),
        preflight: m.data.preflight, version: m.data.version, scene: m.data.scene ?? null,
      }
    case 'board':
      return { ...s, boards: { ...s.boards, [m.data.mac]: m.data } }
    case 'board_removed': {
      const b = { ...s.boards }
      delete b[m.data.mac]
      return { ...s, boards: b }
    }
    case 'boards_changed': return { ...s, boardsRev: s.boardsRev + 1 }
    case 'show': return { ...s, show: m.data }
    case 'settings': return { ...s, settings: m.data }
    case 'preflight': return { ...s, preflight: m.data }
    case 'scene': return { ...s, scene: m.data }
    case 'trigger_seen': return s.show ? { ...s, show: { ...s.show, last_trigger: m.data } } : s
    case 'event': return { ...s, events: [...s.events, m.data].slice(-20) }
    case 'profiles_changed': return { ...s, profilesRev: s.profilesRev + 1 }
    case 'display_profiles_changed': return { ...s, displayProfilesRev: s.displayProfilesRev + 1 }
    case 'media_changed': return { ...s, mediaRev: s.mediaRev + 1 }
    case 'transcode': return { ...s, transcodes: { ...s.transcodes, [m.data.id]: m.data.progress } }
    default: return s
  }
}

export const useLive = () => useContext(Ctx)
export const useAdoptedBoards = () =>
  Object.values(useLive().boards).filter((b) => b.adopted).sort((a, b) => a.name.localeCompare(b.name))

/*
 * Display state lives outside the Live context: the hub publishes it ~30 Hz while a corner
 * is dragged, and putting it in the context would re-render every useLive() consumer.
 */
let display: DisplayState | null = null
const displayListeners = new Set<() => void>()

export const getDisplay = () => display

export function setDisplay(d: DisplayState | null) {
  if (d === display) return
  display = d
  displayListeners.forEach((l) => l())
}

const subscribeDisplay = (l: () => void) => {
  displayListeners.add(l)
  return () => { displayListeners.delete(l) }
}

/** Latest projector geometry/setup state; null until the first WS message or GET. */
export const useDisplay = () => useSyncExternalStore(subscribeDisplay, getDisplay)
