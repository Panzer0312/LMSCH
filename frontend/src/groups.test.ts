import { describe, expect, it } from 'vitest'
import { groupMacs, parseGroupKey, resolveOverrides, selectionMacs, targetMacs } from './groups'
import type { BoardGroup } from './types'

const G: BoardGroup[] = [
  { id: 1, name: 'Dining', color: '#ff0', sort: 0, macs: ['a', 'b', 'c'] },
  { id: 2, name: 'Hall', color: '#f00', sort: 1, macs: ['c', 'd'] },
]

describe('groups', () => {
  it('expands groups to members', () => {
    expect(groupMacs([1, 2, 9], G)).toEqual(['a', 'b', 'c', 'd'])
    expect(selectionMacs({ all: false, macs: ['x'], groups: [2] }, G, ['a', 'c', 'd', 'x'])).toEqual(['c', 'd', 'x'])
    expect(selectionMacs({ all: true, macs: [] }, G, ['a'])).toEqual(['a'])
    expect(targetMacs({ all: true, macs: [], groups: [1] }, G)).toBeNull()
    expect(targetMacs({ all: false, macs: [], groups: [9] }, G)).toEqual([])
  })
  it('board key beats group key, later group wins', () => {
    const out = resolveOverrides({ 'group:1': { bri: 10, colors: [[255, 255, 0]] }, 'group:2': { colors: [[255, 0, 0]] }, c: { bri: 99 } }, G)
    expect(out.a).toEqual({ bri: 10, colors: [[255, 255, 0]] })
    expect(out.c).toEqual({ bri: 99, colors: [[255, 0, 0]] })
  })
  it('parses keys', () => {
    expect(parseGroupKey('group:3')).toBe(3)
    expect(parseGroupKey('aa:bb')).toBeNull()
  })
})

describe('timeline group lanes', async () => {
  const { isMacMuted, lanesOf, overlapKeys, targetFromLanes } = await import('./timeline/model')
  const { toSpecs } = await import('./timeline/ribbon')
  const lanes = [
    { kind: 'all' as const, id: '__all__', label: 'All' },
    { kind: 'group' as const, id: 'group:2', label: 'Hall', macs: ['c', 'd'] },
    { kind: 'board' as const, id: 'a', label: 'A' },
  ]
  it('group lane round-trips through the cue target', () => {
    const t = targetFromLanes(['group:2', 'a'])
    expect(t).toEqual({ all: false, macs: ['a'], groups: [2] })
    expect(lanesOf({ target: t })).toEqual(['group:2', 'a'])
  })
  it('overlaps and ribbon count group membership', () => {
    const cue = (key: string, start: number, target: object) =>
      ({ key, start_s: start, duration_s: 5, target, state: {}, transition_ms: null }) as never
    const cues = [cue('x', 0, { all: false, macs: ['c'] }), cue('y', 1, { all: false, macs: [], groups: [2] })]
    expect(overlapKeys(cues, ['a', 'c', 'd'], G)).toEqual(new Set(['x', 'y']))
    expect(toSpecs(cues, G)[1].targets).toEqual(['c', 'd'])
  })
  it('mute and solo through a group lane', () => {
    expect(isMacMuted('c', lanes, new Set(['group:2']), new Set())).toBe(true)
    expect(isMacMuted('a', lanes, new Set(), new Set(['group:2']))).toBe(true)
    expect(isMacMuted('d', lanes, new Set(), new Set(['group:2']))).toBe(false)
  })
})
