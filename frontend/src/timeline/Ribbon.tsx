import { memo, useMemo } from 'react'
import type { Board } from '../types'
import { ribbonRuns } from './ribbon'
import type { ResolveCtx } from './ribbon'

const HATCH = 'repeating-linear-gradient(135deg, rgba(0,0,0,0.45) 0 2px, transparent 2px 6px)'

/** Resolved color over time for one board. Memoized: playhead changes never re-render it. */
export const Ribbon = memo(function Ribbon({ ctx, board, duration, pxPerSec, height }: {
  ctx: ResolveCtx
  board: Board
  duration: number
  pxPerSec: number
  height: number
}) {
  const runs = useMemo(() => ribbonRuns(ctx, board, duration, pxPerSec), [ctx, board, duration, pxPerSec])
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 overflow-hidden" style={{ height }}>
      {runs.map((r) => (
        <div
          key={r.x}
          className="absolute inset-y-0 overflow-hidden whitespace-nowrap text-[8px] leading-[8px] text-white/80"
          style={{ left: r.x, width: r.w, backgroundColor: r.color, backgroundImage: r.hatched ? HATCH : undefined }}
          title={r.label}
        >
          {r.hatched && r.w > 40 ? r.label : null}
        </div>
      ))}
    </div>
  )
})
