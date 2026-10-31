/**
 * Board group (room) helpers. Same rules as backend spookhub/boards/groups.py: groups resolve to
 * their members at use time; board override keys beat `group:<id>` keys; between groups the later
 * one in sort order wins.
 */
import { useMemo } from 'react'
import type { BoardGroup, BoardSelection, CueTarget, LightState } from './types'

export const GROUP_KEY = 'group:'
export const groupKey = (id: number) => `${GROUP_KEY}${id}`
export function parseGroupKey(key: string): number | null {
  if (!key.startsWith(GROUP_KEY)) return null
  const n = Number(key.slice(GROUP_KEY.length))
  return Number.isInteger(n) ? n : null
}

/** Members of the given groups, deduplicated; unknown ids add nothing. */
export function groupMacs(ids: number[] | undefined, groups: BoardGroup[]): string[] {
  const out = new Set<string>()
  for (const id of ids ?? []) for (const m of groups.find((g) => g.id === id)?.macs ?? []) out.add(m)
  return [...out]
}

/** MACs a selection covers (explicit boards + group members); `all` = every adopted board. */
export function selectionMacs(sel: BoardSelection, groups: BoardGroup[], adopted: string[]): string[] {
  if (sel.all) return adopted
  const set = new Set([...sel.macs, ...groupMacs(sel.groups, groups)])
  return adopted.filter((m) => set.has(m))
}

/** Whole-board MACs a cue hits; null = all selected boards. */
export function targetMacs(t: CueTarget, groups: BoardGroup[]): string[] | null {
  if (t.all) return null
  return [...new Set([...t.macs, ...groupMacs(t.groups, groups)])]
}

function merge(a: LightState | undefined, b: LightState): LightState {
  if (!a || b.preset != null) return b
  return { ...a, ...Object.fromEntries(Object.entries(b).filter(([, v]) => v != null)) }
}

/** Overrides keyed by MAC only, with group keys expanded. */
export function resolveOverrides(raw: Record<string, LightState>, groups: BoardGroup[]): Record<string, LightState> {
  const out: Record<string, LightState> = {}
  for (const g of groups) {
    const st = raw[groupKey(g.id)]
    if (st) for (const m of g.macs) out[m] = merge(out[m], st)
  }
  for (const [k, st] of Object.entries(raw)) if (parseGroupKey(k) == null) out[k] = merge(out[k], st)
  return out
}

/** Groups referenced by id that are deleted or have no adopted member (same rule as preflight). */
export function brokenGroups(ids: number[] | undefined, groups: BoardGroup[], adopted: string[]): number[] {
  return (ids ?? []).filter((id) => !groups.find((g) => g.id === id)?.macs.some((m) => adopted.includes(m)))
}

/** Memoized resolveOverrides for render paths (timeline ribbon rebuilds on identity change). */
export function useResolvedOverrides(raw: Record<string, LightState>, groups: BoardGroup[]): Record<string, LightState> {
  return useMemo(() => resolveOverrides(raw, groups), [raw, groups])
}
