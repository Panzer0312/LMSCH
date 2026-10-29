import { describe, expect, it } from 'vitest'
import { apply3, IDENTITY, inverseWarp, normalize, resetGroup, squareToQuad, videoRect, warpQuad } from './geometry'

const close = (a: [number, number], b: [number, number]) => {
  expect(a[0]).toBeCloseTo(b[0], 9)
  expect(a[1]).toBeCloseTo(b[1], 9)
}

describe('homography', () => {
  it('identity corners map to identity', () => {
    const inv = inverseWarp(IDENTITY)
    close(apply3(inv, 0.3, 0.7), [0.3, 0.7])
  })

  it('maps unit corners onto the keystoned quad and back', () => {
    const g = normalize({ corners: [[0.1, 0.05], [-0.07, 0.02], [-0.02, -0.1], [0.04, -0.03]] })
    const q = warpQuad(g)
    const m = squareToQuad(q)
    const inv = inverseWarp(g)
    const base: [number, number][] = [[0, 0], [1, 0], [1, 1], [0, 1]]
    base.forEach((p, i) => {
      close(apply3(m, p[0], p[1]), q[i])
      close(apply3(inv, q[i][0], q[i][1]), p)
    })
  })
})

describe('videoRect', () => {
  it('16:9 source fills the output', () => {
    expect(videoRect(IDENTITY, 1920, 1080)).toEqual({ x: 0, y: 0, w: 1, h: 1 })
  })

  it('4:3 source is pillarboxed and centered', () => {
    const r = videoRect(IDENTITY, 1440, 1080)
    expect(r.h).toBe(1)
    expect(r.w).toBeCloseTo(0.75, 9)
    expect(r.x).toBeCloseTo(0.125, 9)
  })

  it('rotation 90 swaps the aspect', () => {
    const r = videoRect({ ...IDENTITY, rotate: 90 }, 1920, 1080)
    expect(r.h).toBe(1)
    expect(r.w).toBeCloseTo((9 / 16) / (16 / 9), 9)
  })
})

describe('resetGroup', () => {
  it('keeps other groups', () => {
    const g = normalize({ zoom: 1, brightness: 10, corners: [[0.1, 0], [0, 0], [0, 0], [0, 0]] })
    const f = resetGroup(g, 'frame')
    expect(f.zoom).toBe(0)
    expect(f.brightness).toBe(10)
    expect(f.corners[0][0]).toBe(0.1)
    expect(resetGroup(g, 'keystone').zoom).toBe(1)
  })
})
