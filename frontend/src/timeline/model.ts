/**
 * Timeline editor model: client-side cue type, undo/redo history, snapping, overlap detection.
 * Pure logic, shared by TimelineView (rendering + pointer editing) and TimelineEditor (page).
 */
import { useCallback, useState } from 'react'
import type { Board, BoardStatus, Cue, CueTarget, LightState, Marker, Onset } from '../types'

/** Lane id of the "All selected boards" lane (always the first lane). */
export const ALL_LANE = '__all__'

/** Cue with a stable client key (server ids change on every save). */
export interface TLCue extends Cue { key: string }

/** `sound` (phase 4) and `device` (phase 5) kinds extend this union. */
export interface Lane { kind: 'all' | 'board' | 'segment'; id: string; label: string; status?: BoardStatus; mac?: string; seg?: number }

/** Segment lanes are keyed `mac#seg` so lane ids stay plain strings everywhere else. */
export const segLaneId = (mac: string, seg: number) => `${mac}#${seg}`
export function parseLaneId(id: string): { mac: string; seg: number | null } {
  const i = id.lastIndexOf('#')
  return i < 0 ? { mac: id, seg: null } : { mac: id.slice(0, i), seg: Number(id.slice(i + 1)) }
}

/** Cue target for a set of lane ids (ALL lane wins over everything else). */
export function targetFromLanes(lanes: string[]): CueTarget {
  if (lanes.includes(ALL_LANE)) return { all: true, macs: [] }
  const macs: string[] = []
  const segs: Record<string, number[]> = {}
  for (const id of lanes) {
    const { mac, seg } = parseLaneId(id)
    if (seg == null) { if (!macs.includes(mac)) macs.push(mac) } else if (!(segs[mac] ?? []).includes(seg)) segs[mac] = [...(segs[mac] ?? []), seg]
  }
  return Object.keys(segs).length ? { all: false, macs, segs } : { all: false, macs }
}

let seq = 0
export const newKey = () => `c${Date.now().toString(36)}${(seq++).toString(36)}`

export const fromServer = (cues: Cue[]): TLCue[] => cues.map((c) => ({ ...c, key: newKey() }))
export const toServer = (cues: TLCue[]): Cue[] =>
  [...cues]
    .sort((a, b) => a.start_s - b.start_s)
    .map(({ key: _k, id: _id, ...c }) => ({ ...c, start_s: round3(c.start_s), duration_s: c.duration_s == null ? null : round3(c.duration_s) }))

export const round3 = (n: number) => Math.round(n * 1000) / 1000

/** Lanes a cue is drawn on: the ALL lane, or each targeted board and segment lane. */
export const lanesOf = (c: Pick<Cue, 'target'>): string[] => {
  if (c.target.all) return [ALL_LANE]
  const segs = c.target.segs
  if (!segs) return c.target.macs
  return [...c.target.macs.filter((m) => !(m in segs)), ...Object.entries(segs).flatMap(([m, ids]) => ids.map((i) => segLaneId(m, i)))]
}

/** Boards a cue affects (for overlap checks), given the profile's selected boards. */
const boardsOf = (c: Cue, macs: string[]) => (c.target.all ? macs : c.target.macs.filter((m) => macs.includes(m)))

/**
 * Keys of cues that overlap another cue on at least one board. Same rule as the backend:
 * a cue with a duration overlaps any cue starting on a shared board inside its window.
 */
export function overlapKeys(cues: TLCue[], macs: string[]): Set<string> {
  const out = new Set<string>()
  const sorted = [...cues].sort((a, b) => a.start_s - b.start_s)
  for (let i = 0; i < sorted.length; i++) {
    const a = sorted[i]
    if (a.duration_s == null) continue
    const end = a.start_s + a.duration_s
    const aBoards = boardsOf(a, macs)
    for (let j = i + 1; j < sorted.length && sorted[j].start_s < end; j++) {
      const b = sorted[j]
      if (boardsOf(b, macs).some((m) => aBoards.includes(m))) {
        out.add(a.key)
        out.add(b.key)
      }
    }
  }
  return out
}

/** Snap targets: starts/ends of other cues plus the playhead. */
export function snapCandidates(cues: TLCue[], exclude: Set<string>, playhead: number): number[] {
  const pts = [0, playhead]
  for (const c of cues) {
    if (exclude.has(c.key)) continue
    pts.push(c.start_s)
    if (c.duration_s != null) pts.push(c.start_s + c.duration_s)
  }
  return pts
}

/** Snap t to the nearest candidate within `threshold` seconds, else return t. */
export function snap(t: number, candidates: number[], threshold: number): number {
  let best = t
  let dist = threshold
  for (const p of candidates) {
    const d = Math.abs(p - t)
    if (d < dist) { dist = d; best = p }
  }
  return best
}

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** Undoable editor document: cues and markers share one history so undo order stays intuitive. */
export interface Doc { cues: TLCue[]; markers: Marker[] }

/**
 * transient (true): replace the present without an undo step (drag preview); the next commit records
 * one step from the last committed state. 'amend': replace the last committed step in place, used to
 * coalesce a burst of panel edits (slider drags, typing) into one undo step.
 */
export type SetMode = boolean | 'amend'

export interface History<T> {
  doc: T
  set: (doc: T, mode?: SetMode) => void
  undo: () => void
  redo: () => void
  reset: (doc: T) => void
  canUndo: boolean
  canRedo: boolean
}

interface HState<T> { past: T[]; base: T; present: T; future: T[] }
const LIMIT = 200

export function useHistory<T>(initial: T): History<T> {
  const [h, setH] = useState<HState<T>>({ past: [], base: initial, present: initial, future: [] })
  const set = useCallback((doc: T, mode: SetMode = false) => {
    setH((s) => {
      if (mode === true) return { ...s, present: doc }
      if (mode === 'amend' && s.past.length) return { ...s, base: doc, present: doc, future: [] }
      return { past: [...s.past, s.base].slice(-LIMIT), base: doc, present: doc, future: [] }
    })
  }, [])
  const undo = useCallback(() => {
    setH((s) => {
      if (!s.past.length) return s
      const prev = s.past[s.past.length - 1]
      return { past: s.past.slice(0, -1), base: prev, present: prev, future: [s.base, ...s.future] }
    })
  }, [])
  const redo = useCallback(() => {
    setH((s) => {
      if (!s.future.length) return s
      const [next, ...rest] = s.future
      return { past: [...s.past, s.base], base: next, present: next, future: rest }
    })
  }, [])
  const reset = useCallback((doc: T) => setH({ past: [], base: doc, present: doc, future: [] }), [])
  return { doc: h.present, set, undo, redo, reset, canUndo: h.past.length > 0, canRedo: h.future.length > 0 }
}

/** Props contract for the timeline canvas component. */
export interface TimelineViewProps {
  duration: number
  cues: TLCue[]
  lanes: Lane[] // lanes[0] is ALL_LANE
  selected: Set<string>
  playhead: number
  pxPerSec: number
  mediaId: number | null
  /** Boards, profile base and overrides drive the lane preview and ribbon. */
  boards?: Board[]
  base?: LightState
  overrides?: Record<string, LightState>
  overlaps: Set<string>
  readOnly?: boolean
  snapEnabled: boolean
  onSeek: (t: number) => void
  onSelect: (keys: Set<string>) => void
  /** commit=false while dragging (live preview), commit=true once on pointer release. */
  onChange: (cues: TLCue[], commit: boolean) => void
  onZoom: (pxPerSec: number) => void
  /** Double-click on empty lane space. */
  onAddAt: (t: number, laneId: string) => void
  markers?: Marker[]
  selectedMarkers?: Set<string>
  onMarkerSelect?: (ids: Set<string>) => void
  onMarkersChange?: (markers: Marker[], commit: boolean) => void
  onMarkerRename?: (id: string, label: string) => void
  /** Onsets already filtered by the threshold; drawn as ticks on the waveform. */
  onsets?: Onset[]
  /** Extra snap points (markers, onsets) on top of cue edges and the playhead. */
  snapExtra?: number[]
  loop?: { a: number; b: number } | null
  fps?: number
  muted?: Set<string>
  solo?: Set<string>
  onToggleMute?: (laneId: string) => void
  onToggleSolo?: (laneId: string) => void
  /** Bump `n` to zoom/scroll so [start, end] fills the visible width. */
  viewReq?: { start: number; end: number; n: number } | null
  /** Board macs whose segment sub-lanes are shown. */
  expanded?: Set<string>
  onToggleExpand?: (mac: string) => void
}

/** Short human summary of a cue's light state for block labels. */
export function stateLabel(c: Cue, effects: string[]): string {
  const s = c.state
  if (s.preset != null) return `Preset ${s.preset}`
  const parts: string[] = []
  if (s.on === false) return 'Off'
  if (s.fx != null) parts.push(effects[s.fx] ?? `fx ${s.fx}`)
  if (s.bri != null) parts.push(`${Math.round((s.bri / 255) * 100)}%`)
  return parts.join(' · ') || 'State'
}

/** CSS color for a cue block: custom color, else its first light color, else a neutral accent. */
export function cueColor(c: Cue): string {
  if (c.color) return c.color
  const col = c.state.colors?.[0]
  if (c.state.on === false) return '#333'
  return col ? `rgb(${col[0]},${col[1]},${col[2]})` : '#b49cff'
}

/** Drag-in-progress flag: the page suppresses edit shortcuts while a drag owns a snapshot. */
let dragging = false
export const setDragging = (on: boolean) => { dragging = on }
export const isDragging = () => dragging
