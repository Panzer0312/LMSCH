import { describe, expect, it } from 'vitest'
import { Timeline, type CueSpec, type ResolveState } from './resolve'

interface Fixture {
  name: string
  macs: string[]
  base: ResolveState
  cues: CueSpec[]
  expect_overlaps?: Record<string, [number, number][]>
  expect_seg_overlaps?: Record<string, [number, number][]>
  probes: {
    mac: string
    t: number
    seg?: number
    expect_cue: number | null
    expect_state: ResolveState
    expect_segs?: Record<string, number>
  }[]
}

const files = import.meta.glob<Fixture>('../../../backend/tests/fixtures/resolve/*.json', {
  eager: true,
  import: 'default',
})
const fixtures = Object.values(files)
const segFiles = import.meta.glob<Fixture>(
  '../../../backend/tests/fixtures/resolve/segments/*.json',
  { eager: true, import: 'default' },
)
const segFixtures = Object.values(segFiles)

describe('resolve fixtures (shared with pytest)', () => {
  it('loads at least 10 fixtures', () => expect(fixtures.length).toBeGreaterThanOrEqual(10))
  it('loads at least 4 segment fixtures', () => expect(segFixtures.length).toBeGreaterThanOrEqual(4))
  for (const f of [...fixtures, ...segFixtures]) {
    it(f.name, () => {
      const tl = new Timeline(f.cues, f.macs)
      for (const p of f.probes) {
        const [id, st] =
          p.seg == null ? tl.stateAt(p.mac, p.t, f.base) : tl.segStateAt(p.mac, p.seg, p.t, f.base)
        const msg = JSON.stringify(p)
        expect(id, msg).toBe(p.expect_cue)
        expect(st, msg).toStrictEqual(p.expect_state)
        if (p.expect_segs) {
          const [[boardId, segIds], boardState, segStates] = tl.boardAt(p.mac, p.t, f.base)
          expect(boardId, msg).toBe(id)
          expect(boardState, msg).toStrictEqual(st)
          expect(Object.fromEntries(segIds.map(([s, c]) => [String(s), c])), msg).toStrictEqual(
            p.expect_segs,
          )
          expect([...segStates.keys()].sort((a, b) => a - b), msg).toStrictEqual(
            Object.keys(p.expect_segs).map(Number).sort((a, b) => a - b),
          )
        }
      }
      if (f.expect_overlaps) {
        expect(Object.fromEntries(tl.overlaps())).toStrictEqual(f.expect_overlaps)
      }
      if (f.expect_seg_overlaps) {
        expect(Object.fromEntries(tl.segOverlaps())).toStrictEqual(f.expect_seg_overlaps)
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
