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
}

const defined = (s: ResolveState): ResolveState =>
  Object.fromEntries(Object.entries(s).filter(([, v]) => v != null))

/** Overlay `other` on `base`. A preset in `other` replaces everything; else non-null fields win. */
export function merged(base: ResolveState, other: ResolveState | null): ResolveState {
  if (other == null) return base
  if (other.preset != null) return defined(other)
  return { ...defined(base), ...defined(other) }
}

const endOf = (c: CueSpec) => (c.duration == null ? null : c.start + c.duration)
const hits = (c: CueSpec, mac: string) => c.targets == null || c.targets.includes(mac)

export class Timeline {
  byBoard: Map<string, CueSpec[]>

  constructor(cues: CueSpec[], macs: string[]) {
    const ordered = [...cues].sort((a, b) => a.start - b.start || a.id - b.id)
    this.byBoard = new Map(macs.map((m) => [m, ordered.filter((c) => hits(c, m))]))
  }

  active(mac: string, t: number): CueSpec | null {
    const cues = this.byBoard.get(mac)
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

  /** (cue id or null for base, effective state) */
  stateAt(mac: string, t: number, base: ResolveState): [number | null, ResolveState] {
    const cue = this.active(mac, t)
    if (!cue) return [null, base]
    let st = merged(base, cue.state)
    if (cue.transition_ms != null) st = { ...st, transition_ms: cue.transition_ms }
    return [cue.id, st]
  }

  /** Pairs of overlapping cue ids per board. */
  overlaps(): Map<string, [number, number][]> {
    const out = new Map<string, [number, number][]>()
    for (const [mac, cues] of this.byBoard) {
      const pairs: [number, number][] = []
      for (let i = 0; i < cues.length; i++) {
        const a = cues[i]
        const aEnd = endOf(a)
        for (const b of cues.slice(i + 1)) {
          if (aEnd != null && b.start >= aEnd) break
          if (aEnd != null && b.start < aEnd) pairs.push([a.id, b.id])
        }
      }
      if (pairs.length) out.set(mac, pairs)
    }
    return out
  }
}
