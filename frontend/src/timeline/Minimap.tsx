import { useEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import type { Marker } from '../types'
import { clamp, cueColor } from './model'
import type { TLCue } from './model'

interface Props {
  duration: number
  cues: TLCue[]
  markers: Marker[]
  playhead: number
  viewStart: number
  viewSpan: number
  onScrollTo: (t: number) => void
}

export function Minimap({ duration, cues, markers, playhead, viewStart, viewSpan, onScrollTo }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  // Grab offset (seconds) between the pointer and the window's left edge, kept for the whole drag.
  const drag = useRef<{ pointerId: number; offset: number } | null>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w !== undefined) setWidth(w)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const dur = Math.max(duration, 1e-6)
  const scale = width / dur
  const x = (t: number) => clamp(t, 0, duration) * scale
  const maxStart = Math.max(0, duration - viewSpan)

  const timeAt = (clientX: number, el: Element) => {
    const r = el.getBoundingClientRect()
    return r.width > 0 ? clamp(((clientX - r.left) / r.width) * duration, 0, duration) : 0
  }

  const onDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const t = timeAt(e.clientX, e.currentTarget)
    const inside = t >= viewStart && t <= viewStart + viewSpan
    const offset = inside ? t - viewStart : viewSpan / 2
    drag.current = { pointerId: e.pointerId, offset }
    if (!inside) onScrollTo(clamp(t - offset, 0, maxStart))
  }

  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.pointerId !== e.pointerId) return
    onScrollTo(clamp(timeAt(e.clientX, e.currentTarget) - d.offset, 0, maxStart))
  }

  const onUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === e.pointerId) drag.current = null
  }

  const boxLeft = x(viewStart)
  const boxW = Math.max(4, Math.min(width - boxLeft, viewSpan * scale))

  return (
    <div
      ref={ref}
      className="relative h-6 w-full cursor-pointer touch-none select-none overflow-hidden rounded-md border border-line bg-panel"
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      {width > 0 && (
        <>
          {cues.map((c) => (
            <div
              key={c.key}
              className="pointer-events-none absolute top-1 bottom-1 w-0.5 opacity-80"
              style={{ left: x(c.start_s), backgroundColor: c.color ?? cueColor(c) }}
            />
          ))}
          {markers.map((m) => (
            <div
              key={m.id}
              className="pointer-events-none absolute inset-y-0 w-px bg-yellow-400"
              style={{ left: x(m.t) }}
            />
          ))}
          <div
            className="pointer-events-none absolute inset-y-0 rounded-sm border border-ink/50 bg-ink/10"
            style={{ left: boxLeft, width: boxW }}
          />
          <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-red-500" style={{ left: x(playhead) }} />
        </>
      )}
    </div>
  )
}
