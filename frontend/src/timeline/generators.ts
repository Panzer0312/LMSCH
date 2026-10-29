// Parametric cue generators for the timeline editor. Add new generators by appending to GENERATORS.
import type { LightState, RGB } from '../types'

export type ParamValue = number | RGB

export interface ParamDef {
  key: string
  label: string
  kind: 'number' | 'int' | 'color'
  min?: number
  max?: number
  step?: number
  default: ParamValue
  unit?: string
}

export interface GenCue {
  offset_s: number
  duration_s: number | null
  state: LightState
  transition_ms: number | null
  label?: string
  /** Only set by saved templates. */
  color?: string | null
  lane?: 'all' | 'first' | 'same'
}

export interface Generator {
  id: string
  name: string
  params: ParamDef[]
  generate: (p: Record<string, ParamValue>, effects: string[]) => GenCue[]
}

const RED: RGB = [255, 0, 0]
const BLUE: RGB = [0, 40, 255]
const WHITE: RGB = [255, 255, 255]

export function defaults(g: Generator): Record<string, ParamValue> {
  return Object.fromEntries(g.params.map((d) => [d.key, d.default]))
}

export function fxIndex(effects: string[], names: string[]): number | null {
  const lower = effects.map((e) => e.toLowerCase())
  for (const name of names) {
    const idx = lower.indexOf(name.toLowerCase())
    if (idx >= 0) return idx
  }
  return null
}

function num(p: Record<string, ParamValue>, key: string, fallback: number): number {
  const v = p[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback
}

function col(p: Record<string, ParamValue>, key: string, fallback: RGB): RGB {
  const v = p[key]
  return Array.isArray(v) ? v : fallback
}

const r3 = (x: number) => Math.round(x * 1000) / 1000

// Rounding can push the last cue a hair past the end; the tolerance keeps it, anything beyond is dropped.
function finish(cues: GenCue[], total: number): GenCue[] {
  return cues
    .map((c) => ({
      ...c,
      offset_s: r3(c.offset_s),
      duration_s: c.duration_s === null ? null : r3(c.duration_s),
      transition_ms: c.transition_ms === null ? null : Math.round(c.transition_ms),
    }))
    .filter((c) => c.offset_s >= 0 && c.offset_s + (c.duration_s ?? 0) <= total + 1e-3) // offsets are rounded to ms, so allow ms slack
    .sort((a, b) => a.offset_s - b.offset_s)
}

// mulberry32: tiny seeded PRNG so the same seed always reproduces the same storm.
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const heartbeat: Generator = {
  id: 'heartbeat',
  name: 'Heartbeat',
  params: [
    // Max 160 bpm keeps the "dub" (ends 0.35 s after the beat) from overlapping the next beat.
    { key: 'bpm', label: 'BPM', kind: 'int', min: 30, max: 160, step: 1, default: 80, unit: 'bpm' },
    { key: 'duration', label: 'Duration', kind: 'number', min: 1, max: 120, step: 0.5, default: 10, unit: 's' },
    { key: 'color', label: 'Color', kind: 'color', default: RED },
  ],
  generate: (p) => {
    const bpm = Math.max(1, num(p, 'bpm', 80))
    const duration = num(p, 'duration', 10)
    const color = col(p, 'color', RED)
    const interval = 60 / bpm
    const beats = Math.floor((duration * bpm) / 60)
    const cues: GenCue[] = []
    for (let i = 0; i < beats; i++) {
      const t = i * interval
      cues.push({ offset_s: t, duration_s: 0.12, transition_ms: 30, label: 'lub', state: { on: true, bri: 255, colors: [color] } })
      cues.push({ offset_s: t + 0.25, duration_s: 0.1, transition_ms: 30, label: 'dub', state: { on: true, bri: 180, colors: [color] } })
    }
    return finish(cues, duration)
  },
}

const breathe: Generator = {
  id: 'breathe',
  name: 'Breathe',
  params: [
    { key: 'period', label: 'Period', kind: 'number', min: 0.5, max: 30, step: 0.5, default: 4, unit: 's' },
    { key: 'duration', label: 'Duration', kind: 'number', min: 1, max: 300, step: 0.5, default: 12, unit: 's' },
    { key: 'color', label: 'Color', kind: 'color', default: RED },
    { key: 'min_bri', label: 'Min brightness', kind: 'int', min: 0, max: 255, step: 1, default: 20 },
    { key: 'max_bri', label: 'Max brightness', kind: 'int', min: 0, max: 255, step: 1, default: 255 },
  ],
  generate: (p) => {
    const half = Math.max(0.05, num(p, 'period', 4)) / 2
    const duration = num(p, 'duration', 12)
    const color = col(p, 'color', RED)
    const lo = num(p, 'min_bri', 20)
    const hi = num(p, 'max_bri', 255)
    const n = Math.floor(duration / half + 1e-9)
    const cues: GenCue[] = []
    for (let i = 0; i < n; i++) {
      const up = i % 2 === 0
      cues.push({
        offset_s: i * half,
        duration_s: half,
        transition_ms: half * 1000,
        label: up ? 'in' : 'out',
        state: { on: true, bri: up ? hi : lo, colors: [color] },
      })
    }
    return finish(cues, duration)
  },
}

const fire: Generator = {
  id: 'fire',
  name: 'Fire',
  params: [
    { key: 'duration', label: 'Duration', kind: 'number', min: 1, max: 300, step: 0.5, default: 10, unit: 's' },
    { key: 'intensity', label: 'Intensity', kind: 'int', min: 0, max: 255, step: 1, default: 160 },
  ],
  generate: (p, effects) => {
    const duration = num(p, 'duration', 10)
    const state: LightState = { on: true, bri: 255, colors: [[255, 100, 0], [255, 40, 0]], ix: num(p, 'intensity', 160) }
    const fx = fxIndex(effects, ['Fire 2012', 'Fire Flicker', 'Candle'])
    if (fx !== null) state.fx = fx
    return finish([{ offset_s: 0, duration_s: duration, transition_ms: null, label: 'fire', state }], duration)
  },
}

const police: Generator = {
  id: 'police',
  name: 'Police',
  params: [
    { key: 'duration', label: 'Duration', kind: 'number', min: 0.5, max: 120, step: 0.5, default: 8, unit: 's' },
    { key: 'rate', label: 'Rate', kind: 'number', min: 0.25, max: 10, step: 0.25, default: 2, unit: 'Hz' },
  ],
  generate: (p) => {
    const duration = num(p, 'duration', 8)
    const step = 1 / (2 * Math.max(0.01, num(p, 'rate', 2)))
    const n = Math.floor(duration / step + 1e-9)
    const cues: GenCue[] = []
    for (let i = 0; i < n; i++) {
      const red = i % 2 === 0
      cues.push({
        offset_s: i * step,
        duration_s: step,
        transition_ms: 0,
        label: red ? 'red' : 'blue',
        state: { on: true, bri: 255, colors: [red ? RED : BLUE], transition_ms: 0 },
      })
    }
    return finish(cues, duration)
  },
}

const strobe: Generator = {
  id: 'strobe',
  name: 'Strobe',
  params: [
    { key: 'count', label: 'Flashes', kind: 'int', min: 1, max: 200, step: 1, default: 8 },
    { key: 'rate', label: 'Rate', kind: 'number', min: 0.5, max: 25, step: 0.5, default: 10, unit: 'Hz' },
    { key: 'color', label: 'Color', kind: 'color', default: WHITE },
  ],
  generate: (p) => {
    const count = Math.max(0, Math.floor(num(p, 'count', 8)))
    const period = 1 / Math.max(0.01, num(p, 'rate', 10))
    const half = period / 2
    const color = col(p, 'color', WHITE)
    const cues: GenCue[] = []
    for (let i = 0; i < count; i++) {
      const t = i * period
      cues.push({ offset_s: t, duration_s: half, transition_ms: 0, label: 'on', state: { on: true, bri: 255, colors: [color], transition_ms: 0 } })
      cues.push({ offset_s: t + half, duration_s: half, transition_ms: 0, label: 'off', state: { on: false, transition_ms: 0 } })
    }
    return finish(cues, count * period)
  },
}

const creep: Generator = {
  id: 'creep',
  name: 'Creep',
  params: [
    { key: 'duration', label: 'Duration', kind: 'number', min: 0.5, max: 300, step: 0.5, default: 8, unit: 's' },
    { key: 'from_bri', label: 'From brightness', kind: 'int', min: 0, max: 255, step: 1, default: 5 },
    { key: 'to_bri', label: 'To brightness', kind: 'int', min: 0, max: 255, step: 1, default: 255 },
    { key: 'color', label: 'Color', kind: 'color', default: RED },
  ],
  generate: (p) => {
    const duration = num(p, 'duration', 8)
    const color = col(p, 'color', RED)
    return finish(
      [
        // The start level needs its own instant so the ramp cue doesn't tie with it on the same lane.
        { offset_s: 0, duration_s: 0.05, transition_ms: 0, label: 'from', state: { on: true, bri: num(p, 'from_bri', 5), colors: [color], transition_ms: 0 } },
        { offset_s: 0.05, duration_s: duration - 0.05, transition_ms: (duration - 0.05) * 1000, label: 'to', state: { on: true, bri: num(p, 'to_bri', 255), colors: [color] } },
      ],
      duration,
    )
  },
}

// Worst case per flash: 3 sub-flashes of 120 ms on + 150 ms gap.
const LIGHTNING_SPAN = 3 * (0.12 + 0.15)

const lightning: Generator = {
  id: 'lightning',
  name: 'Lightning',
  params: [
    { key: 'duration', label: 'Duration', kind: 'number', min: 1, max: 120, step: 0.5, default: 6, unit: 's' },
    { key: 'flashes', label: 'Flashes', kind: 'int', min: 1, max: 50, step: 1, default: 4 },
    { key: 'seed', label: 'Seed', kind: 'int', min: 0, max: 99999, step: 1, default: 1 },
  ],
  generate: (p) => {
    const duration = num(p, 'duration', 6)
    const flashes = Math.max(0, Math.floor(num(p, 'flashes', 4)))
    const rand = mulberry32(Math.floor(num(p, 'seed', 1)))
    const room = Math.max(0, duration - LIGHTNING_SPAN)
    const cues: GenCue[] = []
    for (let f = 0; f < flashes; f++) {
      let t = rand() * room
      const subs = 1 + Math.floor(rand() * 3)
      for (let s = 0; s < subs; s++) {
        const on = 0.04 + rand() * 0.08
        const gap = 0.05 + rand() * 0.1
        cues.push({ offset_s: t, duration_s: on, transition_ms: 0, label: 'flash', state: { on: true, bri: 255, colors: [WHITE], transition_ms: 0 } })
        cues.push({ offset_s: t + on, duration_s: null, transition_ms: 0, label: 'off', state: { on: false, transition_ms: 0 } })
        t += on + gap
      }
    }
    return finish(cues, duration)
  },
}

export const GENERATORS: Generator[] = [heartbeat, breathe, fire, police, strobe, creep, lightning]
