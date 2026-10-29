/** Resolved-state helpers for the lane header preview and the ribbon under each board lane. */
import type { Board, Cue, LightState } from '../types'
import { Timeline, merged } from './resolve'
import type { CueSpec, ResolveState } from './resolve'

export const OFF_COLOR = '#16121c'
const DEFAULT_COLOR = '#e8e4f0'

export interface ResolveCtx {
  timeline: Timeline
  base: LightState
  overrides: Record<string, LightState>
}

/** Per-board base = profile base overlaid with that board's override (same rule as the backend). */
export function boardBase(base: LightState, overrides: Record<string, LightState>, mac: string): ResolveState {
  return merged(base, overrides[mac] ?? null)
}

export function toSpecs(cues: Cue[] & { key?: string }[]): CueSpec[] {
  return cues.map((c, i) => ({
    id: i,
    start: c.start_s,
    duration: c.duration_s,
    targets: c.target.all ? null : c.target.macs,
    state: c.state,
    transition_ms: c.transition_ms,
  }))
}

export interface Look { color: string; fx: number; label: string }

/** Visual summary of a resolved state. fx > 0 means a moving effect (drawn hatched). */
export function look(st: ResolveState, board: Board | undefined): Look {
  if (st.on === false) return { color: OFF_COLOR, fx: 0, label: 'Off' }
  if (st.preset != null) {
    const c = st.colors?.[0]
    return { color: c ? `rgb(${c[0]},${c[1]},${c[2]})` : DEFAULT_COLOR, fx: 1, label: board?.presets[String(st.preset)] ?? `Preset ${st.preset}` }
  }
  const c = st.colors?.[0]
  const fx = st.fx ?? 0
  const bri = st.bri == null ? 1 : Math.max(0.15, st.bri / 255)
  const color = c ? `rgb(${Math.round(c[0] * bri)},${Math.round(c[1] * bri)},${Math.round(c[2] * bri)})` : DEFAULT_COLOR
  return { color, fx, label: fx > 0 ? (board?.effects[fx] ?? `fx ${fx}`) : 'Solid' }
}

export function lookAt(ctx: ResolveCtx, board: Board, t: number): Look {
  const [, st] = ctx.timeline.stateAt(board.mac, t, boardBase(ctx.base, ctx.overrides, board.mac))
  return look(st, board)
}

export interface Run { x: number; w: number; color: string; hatched: boolean; label: string }

/** Sample the resolved state every `stepPx` and merge equal neighbours into runs. */
export function ribbonRuns(ctx: ResolveCtx, board: Board, duration: number, pxPerSec: number, stepPx = 4): Run[] {
  const total = Math.max(duration, 1) * pxPerSec
  const base = boardBase(ctx.base, ctx.overrides, board.mac)
  const runs: Run[] = []
  let cur: (Run & { key: string }) | null = null
  for (let x = 0; x < total; x += stepPx) {
    const [, st] = ctx.timeline.stateAt(board.mac, x / pxPerSec, base)
    const l = look(st, board)
    const key = `${l.color}|${l.fx > 0}|${l.label}`
    if (cur && cur.key === key) {
      cur.w = x + stepPx - cur.x
    } else {
      cur = { key, x, w: stepPx, color: l.color, hatched: l.fx > 0, label: l.label }
      runs.push(cur)
    }
  }
  return runs
}
