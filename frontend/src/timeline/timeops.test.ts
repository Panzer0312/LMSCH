import { describe, it, expect } from 'vitest'
import type { Marker } from '../types'
import { ALL_LANE, type TLCue } from './model'
import { clipLanes, deleteRange, fromClip, insertTime, parseClip, rippleShift, timecode, toClip } from './timeops'

const cue = (key: string, start_s: number, duration_s: number | null, macs: string[] | null = null): TLCue => ({
  key,
  start_s,
  duration_s,
  target: macs ? { all: false, macs } : { all: true, macs: [] },
  state: { bri: 128 },
  transition_ms: null,
})
const marker = (id: string, t: number): Marker => ({ id, t, label: id, color: null })
const byKey = (cues: TLCue[], k: string) => cues.find((c) => c.key === k)

describe('rippleShift', () => {
  it('realigns following items after a 2 s trim', () => {
    const cues = [cue('a', 0, 3), cue('b', 5, 2), cue('c', 8, null)]
    const markers = [marker('m1', 1), marker('m2', 6)]
    const r = rippleShift(cues, markers, 5, -2)
    expect(r.cues.map((c) => c.start_s)).toEqual([0, 3, 6])
    expect(r.markers.map((m) => m.t)).toEqual([1, 4])
    expect(cues[1].start_s).toBe(5)
  })
  it('clamps at 0 and with keys leaves markers and other cues alone', () => {
    const cues = [cue('a', 1, 1), cue('b', 2, 1)]
    const markers = [marker('m', 3)]
    const r = rippleShift(cues, markers, 0, -1.5, new Set(['a']))
    expect(byKey(r.cues, 'a')?.start_s).toBe(0)
    expect(byKey(r.cues, 'b')?.start_s).toBe(2)
    expect(r.markers).toBe(markers)
  })
})

describe('insertTime', () => {
  it('shifts later items and extends spanning cue', () => {
    const r = insertTime([cue('span', 2, 4), cue('after', 7, 1), cue('before', 0, 1)], [marker('m', 5)], 4, 1.5)
    expect(byKey(r.cues, 'span')).toMatchObject({ start_s: 2, duration_s: 5.5 })
    expect(byKey(r.cues, 'after')?.start_s).toBe(8.5)
    expect(byKey(r.cues, 'before')).toMatchObject({ start_s: 0, duration_s: 1 })
    expect(r.markers[0].t).toBe(6.5)
  })
})

describe('deleteRange', () => {
  const cues = [cue('inside', 3, 1), cue('after', 8, 1), cue('trim', 1, 3), cue('wide', 0, 10), cue('early', 0, 1)]
  const markers = [marker('in', 4), marker('out', 9), marker('pre', 1)]
  const r = deleteRange(cues, markers, 2, 6)
  it('removes cues and markers inside', () => {
    expect(byKey(r.cues, 'inside')).toBeUndefined()
    expect(r.markers.map((m) => m.id)).toEqual(['out', 'pre'])
  })
  it('shifts items after the range', () => {
    expect(byKey(r.cues, 'after')?.start_s).toBe(4)
    expect(r.markers[0].t).toBe(5)
  })
  it('trims straddling cues', () => {
    expect(byKey(r.cues, 'trim')).toMatchObject({ start_s: 1, duration_s: 1 })
    expect(byKey(r.cues, 'wide')).toMatchObject({ start_s: 0, duration_s: 6 })
    expect(byKey(r.cues, 'early')).toMatchObject({ start_s: 0, duration_s: 1 })
  })
})

describe('timecode', () => {
  it('formats mm:ss.ff', () => {
    expect(timecode(61.5, 25)).toBe('01:01.12')
    expect(timecode(0, 30)).toBe('00:00.00')
  })
})

describe('clipboard', () => {
  it('round-trips with relative timing', () => {
    const src = [cue('a', 4, 2, ['m1', 'm2']), cue('b', 6.5, null)]
    const clip = parseClip(JSON.stringify(toClip(src)))
    expect(clip).not.toBeNull()
    expect(clipLanes(clip!)).toEqual(['m1', 'm2'])
    const out = fromClip(clip!, 10, {})
    expect(out.map((c) => c.start_s)).toEqual([10, 12.5])
    expect(out[0]).toMatchObject({ duration_s: 2, target: { all: false, macs: ['m1', 'm2'] } })
    expect(out[1].target).toEqual({ all: true, macs: [] })
    expect(out[0].key).not.toBe('a')
  })
  it('remap to null drops cues with no lanes left', () => {
    const clip = toClip([cue('a', 0, 1, ['m1']), cue('b', 1, 1, ['m1', 'm2']), cue('c', 2, 1)])
    const out = fromClip(clip, 0, { m1: null, m2: 'x' })
    expect(out).toHaveLength(2)
    expect(out[0].target).toEqual({ all: false, macs: ['x'] })
    expect(out[1].target.all).toBe(true)
    expect(clip.cues[2].lanes).toEqual([ALL_LANE])
  })
  it('rejects garbage', () => {
    expect(parseClip('x')).toBeNull()
    expect(parseClip(null)).toBeNull()
    expect(parseClip('{"v":1,"cues":[{"offset_s":"a"}]}')).toBeNull()
  })
})
