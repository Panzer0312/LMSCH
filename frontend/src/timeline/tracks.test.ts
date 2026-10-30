import { describe, it, expect } from 'vitest'
import type { Cue } from '../types'
import { cuesOfTrack, LoopClock, mergeTrack, trackOf } from './tracks'

const cue = (start_s: number, track?: 'main' | 'idle'): Cue =>
  ({ start_s, duration_s: null, target: { all: true, macs: [] }, state: { bri: 1 }, transition_ms: null, ...(track ? { track } : {}) })

describe('cue tracks', () => {
  it('treats a missing track as main', () => {
    expect(trackOf(cue(0))).toBe('main')
    expect(trackOf(cue(0, 'idle'))).toBe('idle')
  })
  it('filters cues by track', () => {
    const all = [cue(0), cue(1, 'idle'), cue(2, 'main'), cue(3, 'idle')]
    expect(cuesOfTrack(all, 'main').map((c) => c.start_s)).toEqual([0, 2])
    expect(cuesOfTrack(all, 'idle').map((c) => c.start_s)).toEqual([1, 3])
  })
  it('replaces one track and keeps the other on save', () => {
    const all = [cue(0), cue(1, 'idle'), cue(3, 'idle')]
    const out = mergeTrack(all, 'idle', [cue(5)])
    expect(out.map((c) => [c.start_s, trackOf(c)])).toEqual([[5, 'idle'], [0, 'main']])
    const main = mergeTrack(all, 'main', [])
    expect(main.map((c) => c.start_s)).toEqual([1, 3])
  })
})

describe('LoopClock', () => {
  it('runs, wraps at the loop length, pauses and seeks', () => {
    let now = 100
    const c = new LoopClock(4, () => now)
    const seen: string[] = []
    c.listen({ onPlay: () => seen.push('play'), onPause: () => seen.push('pause'), onSeeked: () => seen.push('seek') })
    expect(c.paused).toBe(true)
    void c.play()
    now += 5
    expect(c.currentTime).toBeCloseTo(1)
    c.pause()
    now += 10
    expect(c.currentTime).toBeCloseTo(1)
    c.currentTime = 3.5
    expect(c.currentTime).toBe(3.5)
    c.setLength(2)
    expect(c.currentTime).toBeCloseTo(1.5)
    expect(seen).toEqual(['play', 'pause', 'seek'])
  })
})
