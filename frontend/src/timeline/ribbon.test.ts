import { describe, expect, it } from 'vitest'
import { Timeline } from './resolve'
import type { CueSpec } from './resolve'
import { ribbonRuns, OFF_COLOR } from './ribbon'
import type { ResolveCtx } from './ribbon'

const names = { effects: ['Solid', 'Blink'], presets: { '3': 'Storm' } }
const cue = (id: number, start: number, duration: number | null, state: CueSpec['state'], targets: string[] | null = null): CueSpec =>
  ({ id, start, duration, targets, state, transition_ms: null })
const ctx = (cues: CueSpec[]): ResolveCtx => ({
  timeline: new Timeline(cues, ['a', 'b']),
  base: { on: true, colors: [[10, 20, 30]] },
  overrides: {},
})

describe('ribbonRuns', () => {
  it('is one base run without cues', () => {
    const r = ribbonRuns(ctx([]), 'a', names, 10)
    expect(r).toHaveLength(1)
    expect([r[0].t0, r[0].t1]).toEqual([0, 10])
  })

  it('splits at cue boundaries and returns to base after a duration cue', () => {
    const r = ribbonRuns(ctx([cue(1, 2, 3, { colors: [[255, 0, 0]] })]), 'a', names, 10)
    expect(r.map((x) => [x.t0, x.t1])).toEqual([[0, 2], [2, 5], [5, 10]])
    expect(r[1].color).toBe('rgb(255,0,0)')
    expect(r[0].color).toBe(r[2].color)
  })

  it('merges neighbours with equal look', () => {
    const r = ribbonRuns(ctx([cue(1, 2, 1, { colors: [[10, 20, 30]] })]), 'a', names, 10)
    expect(r).toHaveLength(1)
  })

  it('captures a cue far shorter than a pixel', () => {
    const r = ribbonRuns(ctx([cue(1, 4, 0.001, { on: false })]), 'a', names, 10)
    expect(r.some((x) => x.color === OFF_COLOR && x.t1 - x.t0 < 0.01)).toBe(true)
  })

  it('only uses cues targeting the board; labels effects and presets', () => {
    const cs = ctx([cue(1, 1, 1, { fx: 1 }, ['b']), cue(2, 5, 1, { preset: 3 }, ['a'])])
    const a = ribbonRuns(cs, 'a', names, 10)
    expect(a.find((x) => x.label === 'Storm')?.hatched).toBe(true)
    expect(a.some((x) => x.label === 'Blink')).toBe(false)
    expect(ribbonRuns(cs, 'b', names, 10).find((x) => x.label === 'Blink')?.hatched).toBe(true)
  })
})
