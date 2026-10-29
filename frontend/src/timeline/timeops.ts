/**
 * Timeline time operations (ripple, insert/delete time), timecode formatting and clipboard.
 * Pure functions: inputs are never mutated.
 */
import type { Cue, Marker } from '../types'
import { ALL_LANE, lanesOf, newKey, round3, targetFromLanes, type TLCue } from './model'

type Doc = { cues: TLCue[]; markers: Marker[] }

const shiftT = (t: number, delta: number) => Math.max(0, round3(t + delta))

/** Shift cues/markers starting at or after `from` by `delta`. With `keys`, only those cues move. */
export function rippleShift(cues: TLCue[], markers: Marker[], from: number, delta: number, keys?: Set<string>): Doc {
  return {
    cues: cues.map((c) =>
      c.start_s >= from && (!keys || keys.has(c.key)) ? { ...c, start_s: shiftT(c.start_s, delta) } : c),
    markers: keys ? markers : markers.map((m) => (m.t >= from ? { ...m, t: shiftT(m.t, delta) } : m)),
  }
}

/** Insert `len` seconds at `t`; cues spanning `t` grow so their tail stays aligned. */
export function insertTime(cues: TLCue[], markers: Marker[], t: number, len: number): Doc {
  const shifted = rippleShift(cues, markers, t, len)
  return {
    cues: shifted.cues.map((c) =>
      c.duration_s != null && c.start_s < t && c.start_s + c.duration_s > t
        ? { ...c, duration_s: round3(c.duration_s + len) }
        : c),
    markers: shifted.markers,
  }
}

/** Remove [a,b) and close the gap. */
export function deleteRange(cues: TLCue[], markers: Marker[], a: number, b: number): Doc {
  if (b <= a) return { cues, markers }
  const len = b - a
  const outCues: TLCue[] = []
  for (const c of cues) {
    const end = c.duration_s == null ? null : c.start_s + c.duration_s
    if (c.start_s >= b) {
      outCues.push({ ...c, start_s: shiftT(c.start_s, -len) })
    } else if (c.start_s >= a) {
      // Starts inside the gap: drop unless its tail survives past b; the tail then starts at a.
      if (end != null && end > b) outCues.push({ ...c, start_s: round3(a), duration_s: round3(end - b) })
    } else if (end != null && end > b) {
      outCues.push({ ...c, duration_s: round3(end - c.start_s - len) })
    } else if (end != null && end > a) {
      outCues.push({ ...c, duration_s: round3(a - c.start_s) })
    } else {
      outCues.push(c)
    }
  }
  const outMarkers: Marker[] = []
  for (const m of markers) {
    if (m.t >= b) outMarkers.push({ ...m, t: shiftT(m.t, -len) })
    else if (m.t < a) outMarkers.push(m)
  }
  return { cues: outCues, markers: outMarkers }
}

const pad2 = (n: number) => String(n).padStart(2, '0')

/** mm:ss.ff with ff = frame within the second at `fps`. */
export function timecode(t: number, fps: number): string {
  const v = Math.max(0, round3(t))
  const whole = Math.floor(v)
  const ff = Math.min(Math.max(0, Math.ceil(fps) - 1), Math.floor((v - whole) * fps + 1e-6))
  return `${pad2(Math.floor(whole / 60))}:${pad2(whole % 60)}.${pad2(ff)}`
}

export const CLIP_KEY = 'spookhub.clipboard'

/** lanes: ALL_LANE or board macs. */
export interface ClipCue {
  offset_s: number
  duration_s: number | null
  lanes: string[]
  state: Cue['state']
  transition_ms: number | null
  label?: string | null
  color?: string | null
}
export interface Clip { v: 1; cues: ClipCue[] }

export function toClip(cues: TLCue[]): Clip {
  const t0 = cues.length ? Math.min(...cues.map((c) => c.start_s)) : 0
  return {
    v: 1,
    cues: cues.map((c) => ({
      offset_s: round3(c.start_s - t0),
      duration_s: c.duration_s == null ? null : round3(c.duration_s),
      lanes: [...lanesOf(c)],
      state: { ...c.state },
      transition_ms: c.transition_ms,
      label: c.label ?? null,
      color: c.color ?? null,
    })),
  }
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isNumOrNull = (v: unknown) => v === null || isNum(v)
const isOptStr = (v: unknown) => v === undefined || v === null || typeof v === 'string'

function parseClipCue(v: unknown): ClipCue | null {
  if (!isObj(v)) return null
  const { offset_s, duration_s, lanes, state, transition_ms, label, color } = v
  if (!isNum(offset_s) || !isNumOrNull(duration_s) || !isNumOrNull(transition_ms)) return null
  if (!Array.isArray(lanes) || !lanes.every((l) => typeof l === 'string')) return null
  if (!isObj(state) || !isOptStr(label) || !isOptStr(color)) return null
  return {
    offset_s,
    duration_s: duration_s as number | null,
    lanes: lanes as string[],
    state: state as Cue['state'],
    transition_ms: transition_ms as number | null,
    label: (label as string | null | undefined) ?? null,
    color: (color as string | null | undefined) ?? null,
  }
}

/** Parse clipboard JSON; null on anything that is not a v1 clip. */
export function parseClip(raw: string | null): Clip | null {
  if (raw == null) return null
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return null
  }
  if (!isObj(data) || data.v !== 1 || !Array.isArray(data.cues)) return null
  const cues: ClipCue[] = []
  for (const c of data.cues) {
    const p = parseClipCue(c)
    if (!p) return null
    cues.push(p)
  }
  return { v: 1, cues }
}

/** Distinct board lanes referenced by the clip (ALL lane excluded). */
export function clipLanes(clip: Clip): string[] {
  const out: string[] = []
  for (const c of clip.cues) for (const l of c.lanes) if (l !== ALL_LANE && !out.includes(l)) out.push(l)
  return out
}

/** Materialize a clip at `at`. Lanes missing from `remap` keep their id; null drops them. */
export function fromClip(clip: Clip, at: number, remap: Record<string, string | null>): TLCue[] {
  const out: TLCue[] = []
  for (const c of clip.cues) {
    const lanes: string[] = []
    for (const l of c.lanes) {
      const to = l === ALL_LANE ? ALL_LANE : l in remap ? remap[l] : l
      if (to != null && !lanes.includes(to)) lanes.push(to)
    }
    if (!lanes.length) continue
    out.push({
      key: newKey(),
      start_s: Math.max(0, round3(at + c.offset_s)),
      duration_s: c.duration_s == null ? null : round3(c.duration_s),
      target: targetFromLanes(lanes),
      state: { ...c.state },
      transition_ms: c.transition_ms,
      label: c.label ?? null,
      color: c.color ?? null,
    })
  }
  return out
}
