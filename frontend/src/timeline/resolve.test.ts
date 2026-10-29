import { describe, expect, it } from 'vitest'
import { Timeline, type CueSpec, type ResolveState } from './resolve'

interface Fixture {
  name: string
  macs: string[]
  base: ResolveState
  cues: CueSpec[]
  expect_overlaps?: Record<string, [number, number][]>
  probes: { mac: string; t: number; expect_cue: number | null; expect_state: ResolveState }[]
}

const files = import.meta.glob<Fixture>('../../../backend/tests/fixtures/resolve/*.json', {
  eager: true,
  import: 'default',
})
const fixtures = Object.values(files)

describe('resolve fixtures (shared with pytest)', () => {
  it('loads at least 10 fixtures', () => expect(fixtures.length).toBeGreaterThanOrEqual(10))
  for (const f of fixtures) {
    it(f.name, () => {
      const tl = new Timeline(f.cues, f.macs)
      for (const p of f.probes) {
        const [id, st] = tl.stateAt(p.mac, p.t, f.base)
        expect(id, JSON.stringify(p)).toBe(p.expect_cue)
        expect(st, JSON.stringify(p)).toStrictEqual(p.expect_state)
      }
      if (f.expect_overlaps) {
        expect(Object.fromEntries(tl.overlaps())).toStrictEqual(f.expect_overlaps)
      }
    })
  }
  it('NaN position resolves to base', () => {
    const tl = new Timeline([{ id: 1, start: 0, duration: null, targets: null, state: { fx: 1 }, transition_ms: null }], ['aa'])
    expect(tl.stateAt('aa', NaN, { on: true })).toStrictEqual([null, { on: true }])
  })
})

describe('overlaps', () => {
  it('reports pairs inside a duration window', () => {
    const c = (id: number, start: number, duration: number | null): CueSpec => ({
      id, start, duration, targets: null, state: {}, transition_ms: null,
    })
    const tl = new Timeline([c(1, 0, 5), c(2, 2, null), c(3, 6, 1)], ['aa'])
    expect(tl.overlaps().get('aa')).toEqual([[1, 2]])
  })
})
