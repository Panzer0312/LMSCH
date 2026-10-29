/**
 * Client mirror of backend/spookhub/show/resolve.py (Timeline + LightState.merged).
 * Semantics must stay identical; backend/tests/fixtures/resolve/*.json are run by both sides.
 */

export type RGB = [number, number, number]

export interface ResolveState {
  preset?: number | null
  on?: boolean | null
  bri?: number | null
  fx?: number | null
  pal?: number | null
  colors?: RGB[] | null
  sx?: number | null
  ix?: number | null
  transition_ms?: number | null
}

export interface CueSpec {
  id: number
  start: number
  duration: number | null
  /** null = all selected boards */
  targets: string[] | null
  state: ResolveState
  transition_ms: number | null
  /** mac -> segment ids. A mac listed here is hit on those segments only, never as a whole. */
  segs?: Record<string, number[]> | null
}

/** Change-detection key for one board: [whole-board cue id, [[seg, cue id], ...]]. */
export type BoardKey = [number | null, [number, number][]]

const defined = (s: ResolveState): ResolveState =>
  Object.fromEntries(Object.entries(s).filter(([, v]) => v != null))

/** Overlay `other` on `base`. A preset in `other` replaces everything; else non-null fields win. */
export function merged(base: ResolveState, other: ResolveState | null): ResolveState {
  if (other == null) return base
  if (other.preset != null) return defined(other)
  return { ...defined(base), ...defined(other) }
}

const endOf = (c: CueSpec) => (c.duration == null ? null : c.start + c.duration)
const segsFor = (c: CueSpec, mac: string): number[] | null =>
  c.segs != null && Object.prototype.hasOwnProperty.call(c.segs, mac) ? c.segs[mac] : null
/** Whole-board hit. */
const hits = (c: CueSpec, mac: string) =>
  segsFor(c, mac) == null && (c.targets == null || c.targets.includes(mac))
/** Segment hit: the cue's own segment target, or a whole-board hit on mac. */
const hitsSeg = (c: CueSpec, mac: string, seg: number) => {
  const s = segsFor(c, mac)
  return s != null ? s.includes(seg) : hits(c, mac)
}

const apply = (cue: CueSpec | null, base: ResolveState): [number | null, ResolveState] => {
  if (!cue) return [null, { ...base }]
  let st = merged(base, cue.state)
  if (cue.transition_ms != null) st = { ...st, transition_ms: cue.transition_ms }
  return [cue.id, st]
}

function activeIn(cues: CueSpec[] | undefined, t: number): CueSpec | null {
  // NaN: no cue is active. (Python's bisect would treat NaN as "after all cues"; resolve.py is
  // only ever fed finite positions, so the guard is TS-only and not part of the shared fixtures.)
  if (Number.isNaN(t)) return null
  if (!cues || cues.length === 0) return null
  // Last cue with start <= t (bisect_right - 1).
  let lo = 0
  let hi = cues.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (cues[mid].start <= t) lo = mid + 1
    else hi = mid
  }
  const i = lo - 1
  if (i < 0) return null
  const latest = cues[i]
  const latestEnd = endOf(latest)
  if (latestEnd == null || t < latestEnd) return latest
  // Open-ended cues before the latest are superseded; only a running duration cue takes over.
  for (let j = i - 1; j >= 0; j--) {
    const e = endOf(cues[j])
    if (e != null && t < e) return cues[j]
  }
  return null
}

function overlapPairs(cues: CueSpec[]): [number, number][] {
  const pairs: [number, number][] = []
  for (let i = 0; i < cues.length; i++) {
    const a = cues[i]
    const aEnd = endOf(a)
    for (const b of cues.slice(i + 1)) {
      if (aEnd != null && b.start >= aEnd) break
      if (aEnd != null && b.start < aEnd) pairs.push([a.id, b.id])
    }
  }
  return pairs
}

const segKey = (mac: string, seg: number) => `${mac}:${seg}`

export class Timeline {
  byBoard: Map<string, CueSpec[]>
  /** Keyed "mac:seg"; only segments some cue targets, in (mac, seg) order. */
  bySeg: Map<string, CueSpec[]>
  segsOf: Map<string, number[]>

  constructor(cues: CueSpec[], macs: string[]) {
    const ordered = [...cues].sort((a, b) => a.start - b.start || a.id - b.id)
    this.byBoard = new Map(macs.map((m) => [m, ordered.filter((c) => hits(c, m))]))
    // Only segments some cue targets get their own index; the rest follow the board.
    const seen = new Set<string>()
    const keys: [string, number][] = []
    for (const c of ordered) {
      if (c.segs == null) continue
      for (const m of macs) {
        for (const sg of segsFor(c, m) ?? []) {
          const k = segKey(m, sg)
          if (!seen.has(k)) {
            seen.add(k)
            keys.push([m, sg])
          }
        }
      }
    }
    keys.sort(([ma, sa], [mb, sb]) => (ma < mb ? -1 : ma > mb ? 1 : sa - sb))
    this.bySeg = new Map()
    this.segsOf = new Map()
    for (const [m, sg] of keys) {
      this.bySeg.set(segKey(m, sg), ordered.filter((c) => hitsSeg(c, m, sg)))
      const list = this.segsOf.get(m)
      if (list) list.push(sg)
      else this.segsOf.set(m, [sg])
    }
  }

  active(mac: string, t: number): CueSpec | null {
    return activeIn(this.byBoard.get(mac), t)
  }

  /** Winning cue for one segment: a whole-board cue or a cue for this segment. */
  activeSeg(mac: string, seg: number, t: number): CueSpec | null {
    const cues = this.bySeg.get(segKey(mac, seg))
    if (cues === undefined) return this.active(mac, t)
    return activeIn(cues, t)
  }

  /** (cue id or null for base, effective state) */
  stateAt(mac: string, t: number, base: ResolveState): [number | null, ResolveState] {
    return apply(this.active(mac, t), base)
  }

  /** stateAt for one segment; equals stateAt when no segment cue wins. */
  segStateAt(mac: string, seg: number, t: number, base: ResolveState): [number | null, ResolveState] {
    return apply(this.activeSeg(mac, seg, t), base)
  }

  /** Whole-board state plus the states of segments whose own segment cue wins. */
  boardAt(
    mac: string,
    t: number,
    base: ResolveState,
  ): [BoardKey, ResolveState, Map<number, ResolveState>] {
    const [cueId, state] = this.stateAt(mac, t, base)
    const segs = new Map<number, ResolveState>()
    const segIds: [number, number][] = []
    for (const sg of this.segsOf.get(mac) ?? []) {
      const cue = this.activeSeg(mac, sg, t)
      if (cue == null || hits(cue, mac)) continue
      segs.set(sg, apply(cue, base)[1])
      segIds.push([sg, cue.id])
    }
    return [[cueId, segIds], state, segs]
  }

  /** Pairs of overlapping cue ids per board. */
  overlaps(): Map<string, [number, number][]> {
    const out = new Map<string, [number, number][]>()
    for (const [mac, cues] of this.byBoard) {
      const pairs = overlapPairs(cues)
      if (pairs.length) out.set(mac, pairs)
    }
    return out
  }

  /** Overlapping pairs per targeted segment, keyed "mac:seg"; whole-board pairs omitted. */
  segOverlaps(): Map<string, [number, number][]> {
    const out = new Map<string, [number, number][]>()
    for (const [k, cues] of this.bySeg) {
      const mac = k.slice(0, k.lastIndexOf(':'))
      const whole = new Set(overlapPairs(this.byBoard.get(mac) ?? []).map(([a, b]) => `${a},${b}`))
      const pairs = overlapPairs(cues).filter(([a, b]) => !whole.has(`${a},${b}`))
      if (pairs.length) out.set(k, pairs)
    }
    return out
  }
}
