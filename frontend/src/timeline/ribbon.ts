/** Resolved-state helpers for the lane header preview and the ribbon under each board lane. */
import type { Board, Cue, LightState } from '../types'
import { Timeline, merged } from './resolve'
import type { CueSpec, ResolveState } from './resolve'

export const OFF_COLOR = '#16121c'
const PRESET_COLOR = '#8a8697'
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

export interface Look { color: string; hatched: boolean; label: string }
export interface Names { effects: string[]; presets: Record<string, string> }

/** Visual summary of a resolved state. Effects and presets are drawn hatched. */
export function look(st: ResolveState, names: Names): Look {
  const c = st.colors?.[0]
  if (st.on === false || st.bri === 0) return { color: OFF_COLOR, hatched: false, label: 'Off' }
  if (st.preset != null) {
    return {
      color: c ? `rgb(${c[0]},${c[1]},${c[2]})` : PRESET_COLOR,
      hatched: true,
      label: names.presets[String(st.preset)] ?? `Preset ${st.preset}`,
    }
  }
  const fx = st.fx ?? 0
  const bri = st.bri == null ? 1 : Math.max(0.15, st.bri / 255)
  const color = c ? `rgb(${Math.round(c[0] * bri)},${Math.round(c[1] * bri)},${Math.round(c[2] * bri)})` : DEFAULT_COLOR
  return { color, hatched: fx > 0, label: fx > 0 ? (names.effects[fx] ?? `fx ${fx}`) : 'Solid' }
}

export function lookAt(ctx: ResolveCtx, board: Board, t: number): Look {
  const [, st] = ctx.timeline.stateAt(board.mac, t, boardBase(ctx.base, ctx.overrides, board.mac))
  return look(st, board)
}

/** A stretch of constant look, in seconds. */
export interface Run { t0: number; t1: number; color: string; hatched: boolean; label: string }

/**
 * Runs from cue boundaries: the state only changes at a cue start/end, so evaluate once per
 * segment (at its midpoint) and merge equal neighbours. O(cues) per board, independent of zoom.
 */
export function ribbonRuns(ctx: ResolveCtx, mac: string, names: Names, duration: number): Run[] {
  const total = Math.max(duration, 1)
  const base = boardBase(ctx.base, ctx.overrides, mac)
  const pts = new Set<number>([0, total])
  for (const c of ctx.timeline.byBoard.get(mac) ?? []) {
    pts.add(Math.min(Math.max(c.start, 0), total))
    if (c.duration != null) pts.add(Math.min(Math.max(c.start + c.duration, 0), total))
  }
  const ts = [...pts].sort((a, b) => a - b)
  const runs: Run[] = []
  for (let i = 0; i + 1 < ts.length; i++) {
    const [, st] = ctx.timeline.stateAt(mac, (ts[i] + ts[i + 1]) / 2, base)
    const l = look(st, names)
    const last = runs[runs.length - 1]
    if (last && last.color === l.color && last.hatched === l.hatched && last.label === l.label) last.t1 = ts[i + 1]
    else runs.push({ t0: ts[i], t1: ts[i + 1], ...l })
  }
  return runs
}
