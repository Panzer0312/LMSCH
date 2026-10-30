import { describe, it, expect } from 'vitest'
import { ALL_LANE, lanesOf, lightLanes, newSoundCue, overlapKeys, SOUND_LANE, SOUND_LANE_DEF, soundEnd, targetFromLanes, type Lane, type TLCue } from './model'
import { toSpecs } from './ribbon'
import { clipLanes, deleteRange, fromClip, insertTime, parseClip, rippleShift, toClip } from './timeops'

const light = (key: string, start_s: number, duration_s: number | null): TLCue =>
  ({ key, start_s, duration_s, target: { all: true, macs: [] }, state: { bri: 128 }, transition_ms: null })
const sound = (key: string, start_s: number, duration_s: number | null = null, media_id = 7): TLCue =>
  ({ ...newSoundCue(start_s, media_id), key, duration_s })

describe('sound lane model', () => {
  it('puts sound cues on the sound lane only', () => {
    expect(lanesOf(sound('s', 1))).toEqual([SOUND_LANE])
    expect(lanesOf(light('l', 1, null))).toEqual([ALL_LANE])
  })
  it('new sound cue has default spec and plays to media end', () => {
    const c = newSoundCue(2.5, 3)
    expect(c.kind).toBe('sound')
    expect(c.duration_s).toBeNull()
    expect(c.sound).toEqual({ media_id: 3, volume: 1, fade_in_ms: 0, fade_out_ms: 0, loop: false })
    expect(c.target).toEqual({ all: true, macs: [] })
    expect(soundEnd(c, new Map([[3, { duration_s: 4 }]]))).toBe(6.5)
    expect(soundEnd({ ...c, duration_s: 1 }, new Map())).toBe(3.5)
  })
  it('sound lane is never a light target', () => {
    expect(targetFromLanes([SOUND_LANE])).toEqual({ all: true, macs: [] })
    expect(targetFromLanes([SOUND_LANE, 'aa'])).toEqual({ all: false, macs: ['aa'] })
    const lanes: Lane[] = [{ kind: 'all', id: ALL_LANE, label: 'All' }, { kind: 'board', id: 'aa', label: 'A' }, SOUND_LANE_DEF]
    expect(lightLanes(lanes).map((l) => l.id)).toEqual([ALL_LANE, 'aa'])
  })
  it('light resolve and overlap checks ignore sound cues', () => {
    const cues = [light('a', 0, 5), sound('s', 1, 2), light('b', 6, 1)]
    expect(toSpecs(cues)).toHaveLength(2)
    expect(overlapKeys(cues, ['aa']).size).toBe(0)
  })
})

describe('time ops with sound cues', () => {
  it('ripple and insert time move sound cues', () => {
    const cues = [light('a', 0, 1), sound('s', 5)]
    expect(rippleShift(cues, [], 2, 3).cues[1].start_s).toBe(8)
    expect(insertTime(cues, [], 1, 2).cues[1].start_s).toBe(7)
  })
  it('delete range shifts and trims sound cues', () => {
    const r = deleteRange([sound('s', 5), sound('t', 0, 4)], [], 2, 3)
    expect(r.cues.map((c) => [c.start_s, c.duration_s])).toEqual([[4, null], [0, 3]])
    expect(r.cues.every((c) => c.kind === 'sound')).toBe(true)
  })
  it('copy/paste keeps sound spec and skips lane remap', () => {
    const s = { ...sound('s', 2, 1.5), sound: { media_id: 9, volume: 0.8, fade_in_ms: 100, fade_out_ms: 200, loop: true } }
    const clip = parseClip(JSON.stringify(toClip([s, { ...light('l', 3, 1), target: { all: false, macs: ['bb'] } }])))
    expect(clip).not.toBeNull()
    expect(clipLanes(clip!)).toEqual(['bb'])
    const out = fromClip(clip!, 10, { bb: null })
    expect(out).toHaveLength(1)
    expect(out[0]).toMatchObject({ kind: 'sound', start_s: 10, duration_s: 1.5, sound: s.sound })
  })
  it('rejects a sound clip cue without a valid spec', () => {
    const bad = { v: 1, cues: [{ offset_s: 0, duration_s: null, lanes: [SOUND_LANE], state: {}, transition_ms: null, kind: 'sound', sound: { media_id: 'x' } }] }
    expect(parseClip(JSON.stringify(bad))).toBeNull()
  })
})
