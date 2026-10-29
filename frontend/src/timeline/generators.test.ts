import { describe, it, expect } from 'vitest'
import { GENERATORS, defaults, fxIndex, type Generator, type ParamValue } from './generators'

const EFFECTS = ['Solid', 'Blink', 'Candle', 'Fire 2012', 'Lightning', 'Strobe']

function gen(id: string): Generator {
  const g = GENERATORS.find((x) => x.id === id)
  if (!g) throw new Error(`missing generator ${id}`)
  return g
}

function n(v: ParamValue | undefined): number {
  if (typeof v !== 'number') throw new Error('expected number param')
  return v
}

// Strobe has no duration param; its span is count flashes at the given rate.
function totalOf(g: Generator, p: Record<string, ParamValue>): number {
  if (g.id === 'strobe') return n(p.count) / n(p.rate)
  return n(p.duration)
}

describe('generators', () => {
  it('heartbeat 80 bpm 10 s gives 13 beats spaced 0.75 s', () => {
    const g = gen('heartbeat')
    const cues = g.generate({ ...defaults(g), bpm: 80, duration: 10 }, EFFECTS)
    const lubs = cues.filter((c) => c.state.bri === 255)
    expect(lubs).toHaveLength(13)
    expect(lubs[0].offset_s).toBe(0)
    expect(lubs[1].offset_s - lubs[0].offset_s).toBeCloseTo(0.75, 6)
    expect(cues[1].offset_s).toBeCloseTo(0.25, 6)
  })

  it('lightning is deterministic per seed', () => {
    const g = gen('lightning')
    const a = g.generate({ ...defaults(g), seed: 7 }, EFFECTS)
    const b = g.generate({ ...defaults(g), seed: 7 }, EFFECTS)
    const c = g.generate({ ...defaults(g), seed: 8 }, EFFECTS)
    expect(a).toEqual(b)
    expect(a).not.toEqual(c)
  })

  it('fxIndex matches case-insensitively in name order', () => {
    expect(fxIndex(EFFECTS, ['fire flicker', 'FIRE 2012', 'candle'])).toBe(3)
    expect(fxIndex(EFFECTS, ['Nope'])).toBeNull()
  })

  it('fire picks effect index when available', () => {
    const g = gen('fire')
    expect(g.generate(defaults(g), EFFECTS)[0].state.fx).toBe(3)
    expect(g.generate(defaults(g), [])[0].state.fx).toBeUndefined()
  })

  for (const g of GENERATORS) {
    it(`${g.id} defaults yield sorted cues within duration`, () => {
      const p = defaults(g)
      const cues = g.generate(p, EFFECTS)
      const total = totalOf(g, p)
      expect(cues.length).toBeGreaterThan(0)
      for (let i = 0; i < cues.length; i++) {
        const c = cues[i]
        expect(c.offset_s).toBeGreaterThanOrEqual(0)
        expect(c.offset_s + (c.duration_s ?? 0)).toBeLessThanOrEqual(total + 1e-6)
        expect(Math.round(c.offset_s * 1000) / 1000).toBe(c.offset_s)
        if (i > 0) expect(c.offset_s).toBeGreaterThanOrEqual(cues[i - 1].offset_s)
      }
    })
  }
})
