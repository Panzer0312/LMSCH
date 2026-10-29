import { memo, useMemo } from 'react'
import { ribbonRuns } from './ribbon'
import type { Names, ResolveCtx } from './ribbon'

const HATCH = 'repeating-linear-gradient(135deg, rgba(0,0,0,0.45) 0 2px, transparent 2px 6px)'

const sameList = (a: string[], b: string[]) => a === b || (a.length === b.length && a.every((v, i) => v === b[i]))
const sameMap = (a: Record<string, string>, b: Record<string, string>) => {
  if (a === b) return true
  const ka = Object.keys(a)
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k])
}

interface Props extends Names {
  ctx: ResolveCtx
  mac: string
  duration: number
  pxPerSec: number
  height: number
}

/** Resolved color over time for one board. Playhead ticks and unrelated WS board updates never re-render it. */
export const Ribbon = memo(function Ribbon({ ctx, mac, effects, presets, duration, pxPerSec, height }: Props) {
  const runs = useMemo(
    () => ribbonRuns(ctx, mac, { effects, presets }, duration),
    [ctx, mac, effects, presets, duration],
  )
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 overflow-hidden" style={{ height }}>
      {runs.map((r) => {
        const w = (r.t1 - r.t0) * pxPerSec
        return (
          <div
            key={r.t0}
            className="absolute inset-y-0 overflow-hidden whitespace-nowrap text-[8px] leading-[8px] text-white/80"
            style={{ left: r.t0 * pxPerSec, width: w, backgroundColor: r.color, backgroundImage: r.hatched ? HATCH : undefined }}
            title={r.label}
          >
            {r.hatched && w > 40 ? r.label : null}
          </div>
        )
      })}
    </div>
  )
}, (a, b) => a.ctx === b.ctx && a.mac === b.mac && a.duration === b.duration && a.pxPerSec === b.pxPerSec
  && a.height === b.height && sameList(a.effects, b.effects) && sameMap(a.presets, b.presets))
