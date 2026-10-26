/**
 * Timeline editor model: client-side cue type, undo/redo history, snapping, overlap detection.
 * Pure logic, shared by TimelineView (rendering + pointer editing) and TimelineEditor (page).
 */
import { useCallback, useState } from 'react'
import type { BoardStatus, Cue } from '../types'

/** Lane id of the "All selected boards" lane (always the first lane). */
export const ALL_LANE = '__all__'

/** Cue with a stable client key (server ids change on every save). */
export interface TLCue extends Cue { key: string }

export interface Lane { id: string; label: string; status?: BoardStatus }

let seq = 0
export const newKey = () => `c${Date.now().toString(36)}${(seq++).toString(36)}`

export const fromServer = (cues: Cue[]): TLCue[] => cues.map((c) => ({ ...c, key: newKey() }))
export const toServer = (cues: TLCue[]): Cue[] =>
  [...cues]
    .sort((a, b) => a.start_s - b.start_s)
    .map(({ key: _k, id: _id, ...c }) => ({ ...c, start_s: round3(c.start_s), duration_s: c.duration_s == null ? null : round3(c.duration_s) }))

export const round3 = (n: number) => Math.round(n * 1000) / 1000

/** Lanes a cue is drawn on: the ALL lane, or each targeted board lane. */
export const lanesOf = (c: Cue): string[] => (c.target.all ? [ALL_LANE] : c.target.macs)

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

export interface History {
  doc: TLCue[]
  /**
   * Update the document. transient=true (during a drag) replaces the present without creating
   * an undo step; the next non-transient set records one step from the last committed state.
   */
  set: (doc: TLCue[], transient?: boolean) => void
  undo: () => void
  redo: () => void
  reset: (doc: TLCue[]) => void
  canUndo: boolean
  canRedo: boolean
}

interface HState { past: TLCue[][]; base: TLCue[]; present: TLCue[]; future: TLCue[][] }
const LIMIT = 200

export function useHistory(initial: TLCue[]): History {
  const [h, setH] = useState<HState>({ past: [], base: initial, present: initial, future: [] })
  const set = useCallback((doc: TLCue[], transient = false) => {
    setH((s) => transient
      ? { ...s, present: doc }
      : { past: [...s.past, s.base].slice(-LIMIT), base: doc, present: doc, future: [] })
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
  const reset = useCallback((doc: TLCue[]) => setH({ past: [], base: doc, present: doc, future: [] }), [])
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

/** CSS color for a cue block: its first color, else a neutral accent. */
export function cueColor(c: Cue): string {
  const col = c.state.colors?.[0]
  if (c.state.on === false) return '#333'
  return col ? `rgb(${col[0]},${col[1]},${col[2]})` : '#b49cff'
}

/** Drag-in-progress flag: the page suppresses edit shortcuts while a drag owns a snapshot. */
let dragging = false
export const setDragging = (on: boolean) => { dragging = on }
export const isDragging = () => dragging
