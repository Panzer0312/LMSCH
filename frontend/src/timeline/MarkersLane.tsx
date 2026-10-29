import { useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import type { Marker } from '../types'
import { clamp, round3, setDragging } from './model'

export const MARKERS_H = 20
const DEFAULT_COLOR = '#facc15'

interface Drag {
  pointerId: number
  x0: number
  pps: number
  anchor: string
  base: Marker[]
  orig: Map<string, number>
  latest: Marker[] | null
}

interface Props {
  markers: Marker[]
  duration: number
  pxPerSec: number
  selected: Set<string>
  readOnly?: boolean
  snap: (t: number, excludeId: string) => number
  onSelect: (ids: Set<string>) => void
  onChange: (markers: Marker[], commit: boolean) => void
  onRename: (id: string, label: string) => void
  onSeek: (t: number) => void
}

export function MarkersLane({
  markers, duration, pxPerSec, selected, readOnly = false, snap, onSelect, onChange, onRename, onSeek,
}: Props) {
  const editable = !readOnly
  const drag = useRef<Drag | null>(null)
  const [editing, setEditing] = useState<{ id: string; value: string } | null>(null)
  // Escape and Enter both unmount the input; this stops the trailing blur from committing twice.
  const editDone = useRef(false)

  const onLaneClick = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return
    onSeek(clamp((e.clientX - e.currentTarget.getBoundingClientRect().left) / pxPerSec, 0, duration))
  }

  const onDown = (e: ReactPointerEvent<HTMLDivElement>, m: Marker) => {
    if (e.button !== 0) return
    e.stopPropagation()
    const toggle = e.shiftKey || e.ctrlKey || e.metaKey
    let next: Set<string>
    if (toggle) {
      next = new Set(selected)
      if (next.has(m.id)) next.delete(m.id)
      else next.add(m.id)
    } else if (!selected.has(m.id)) {
      next = new Set([m.id])
    } else {
      next = selected
    }
    if (next !== selected) onSelect(next)
    if (!editable || !next.has(m.id)) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const orig = new Map<string, number>()
    for (const x of markers) if (next.has(x.id)) orig.set(x.id, x.t)
    setDragging(true)
    drag.current = { pointerId: e.pointerId, x0: e.clientX, pps: pxPerSec, anchor: m.id, base: markers, orig, latest: null }
  }

  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.pointerId !== e.pointerId) return
    const o = d.orig.get(d.anchor)
    if (o === undefined) return
    const raw = clamp(o + (e.clientX - d.x0) / d.pps, 0, duration)
    const delta = (e.altKey ? raw : clamp(snap(raw, d.anchor), 0, duration)) - o
    const next = d.base.map((x) => {
      const ot = d.orig.get(x.id)
      return ot === undefined ? x : { ...x, t: round3(clamp(ot + delta, 0, duration)) }
    })
    d.latest = next.some((x, i) => x.t !== d.base[i].t) ? next : null
    onChange(next, false)
  }

  const onUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.pointerId !== e.pointerId) return
    drag.current = null
    setDragging(false)
    if (d.latest) onChange(d.latest, true)
  }

  const onDouble = (e: ReactMouseEvent<HTMLDivElement>, m: Marker) => {
    e.stopPropagation()
    if (!editable) return
    editDone.current = false
    setEditing({ id: m.id, value: m.label })
  }

  const commitEdit = () => {
    if (editDone.current || !editing) return
    editDone.current = true
    const m = markers.find((x) => x.id === editing.id)
    const label = editing.value.trim()
    if (m && label !== m.label) onRename(editing.id, label)
    setEditing(null)
  }

  const onEditKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    e.stopPropagation()
    if (e.key === 'Enter') commitEdit()
    else if (e.key === 'Escape') {
      editDone.current = true
      setEditing(null)
    }
  }

  return (
    <div
      className="relative select-none border-b border-line bg-panel"
      style={{ width: duration * pxPerSec, height: MARKERS_H }}
      onClick={onLaneClick}
    >
      {markers.map((m) => {
        const col = m.color ?? DEFAULT_COLOR
        const isSel = selected.has(m.id)
        const isEditing = editing?.id === m.id
        return (
          <div
            key={m.id}
            className={`absolute top-0 flex h-full items-start ${isSel ? 'z-10' : ''} ${
              editable ? 'cursor-grab touch-none active:cursor-grabbing' : 'cursor-pointer'
            }`}
            style={{ left: m.t * pxPerSec }}
            title={m.label}
            onPointerDown={(e) => onDown(e, m)}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerCancel={onUp}
            onClick={(e) => e.stopPropagation()}
            onDoubleClick={(e) => onDouble(e, m)}
          >
            <div className="pointer-events-none absolute inset-y-0 left-0 w-px" style={{ backgroundColor: col }} />
            <div
              className={`pointer-events-none mt-0.5 h-2.5 w-2 ${isSel ? 'brightness-125' : 'opacity-80'}`}
              style={{ backgroundColor: col, clipPath: 'polygon(0 0, 100% 50%, 0 100%)' }}
            />
            {isEditing ? (
              <input
                autoFocus
                className="ml-0.5 h-4 w-28 rounded-sm border border-line bg-bg px-1 text-[10px] text-ink outline-none"
                value={editing.value}
                onChange={(e) => setEditing({ id: m.id, value: e.target.value })}
                onKeyDown={onEditKey}
                onBlur={commitEdit}
                onPointerDown={(e) => e.stopPropagation()}
                onDoubleClick={(e) => e.stopPropagation()}
              />
            ) : (
              <span
                className={`pointer-events-none ml-0.5 whitespace-nowrap rounded-sm px-0.5 text-[10px] leading-4 ${
                  isSel ? 'text-ink ring-1 ring-pumpkin' : 'text-mute'
                }`}
                style={isSel ? { backgroundColor: `color-mix(in srgb, ${col} 30%, transparent)` } : undefined}
              >
                {m.label}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}
