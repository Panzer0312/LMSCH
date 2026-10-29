import { useEffect, useMemo, useRef, useState } from 'react'
import { CUE_TEMPLATES } from './templates'

export interface GenPoint { t: number; strength?: number }

export function GenerateDialog({ title, points, threshold, range, lanes, defaultLane, onGenerate, onClose }: {
  title: string
  points: GenPoint[]
  threshold: number | null
  range: { a: number; b: number }
  lanes: { id: string; label: string }[]
  defaultLane: string
  onGenerate: (r: { times: number[]; templateId: string; laneId: string; durationOverride: number | null }) => void
  onClose: () => void
}) {
  const [templateId, setTemplateId] = useState(CUE_TEMPLATES[0]?.id ?? '')
  const [laneId, setLaneId] = useState(lanes.some((l) => l.id === defaultLane) ? defaultLane : (lanes[0]?.id ?? ''))
  const [thr, setThr] = useState(threshold ?? 0)
  const [aStr, setAStr] = useState(String(Math.round(range.a * 1000) / 1000))
  const [bStr, setBStr] = useState(String(Math.round(range.b * 1000) / 1000))
  const [durStr, setDurStr] = useState('')

  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose })
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); closeRef.current() } }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  const template = CUE_TEMPLATES.find((t) => t.id === templateId)
  const a = Number(aStr)
  const b = Number(bStr)
  const rangeOk = aStr.trim() !== '' && bStr.trim() !== '' && Number.isFinite(a) && Number.isFinite(b) && a <= b
  const durTrim = durStr.trim()
  const durNum = Number(durTrim)
  const durOk = durTrim === '' || (Number.isFinite(durNum) && durNum > 0)
  const durationOverride = durTrim === '' || !durOk ? null : durNum

  const times = useMemo(() => {
    if (!rangeOk) return []
    return points
      .filter((p) => p.t >= a && p.t <= b && (threshold == null || (p.strength ?? 0) >= thr))
      .map((p) => p.t)
      .sort((x, y) => x - y)
  }, [points, rangeOk, a, b, threshold, thr])

  const canGenerate = times.length > 0 && !!template && laneId !== '' && durOk

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title}
        className="card max-h-[90vh] w-full max-w-md space-y-3 overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <h2 className="text-base font-semibold">{title}</h2>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="label">Template</span>
            <select className="input" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              {CUE_TEMPLATES.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="label">Lane</span>
            <select className="input" value={laneId} onChange={(e) => setLaneId(e.target.value)}>
              {lanes.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
            </select>
          </label>
        </div>

        {threshold != null && (
          <label className="block">
            <span className="label">Strength threshold {thr.toFixed(2)}</span>
            <input type="range" min={0} max={1} step={0.01} className="w-full accent-pumpkin" value={thr}
              onChange={(e) => setThr(Number(e.target.value))} />
          </label>
        )}

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="label">From (s)</span>
            <input type="number" min={0} step={0.1} className="input" value={aStr} onChange={(e) => setAStr(e.target.value)} />
          </label>
          <label className="block">
            <span className="label">To (s)</span>
            <input type="number" min={0} step={0.1} className="input" value={bStr} onChange={(e) => setBStr(e.target.value)} />
          </label>
        </div>

        <label className="block">
          <span className="label">Duration override (s)</span>
          <input type="number" min={0} step={0.1} className="input" value={durStr}
            placeholder={template?.duration_s == null ? 'template: hold' : `template: ${template.duration_s}`}
            onChange={(e) => setDurStr(e.target.value)} />
        </label>

        {!rangeOk && <p className="text-sm text-blood">Invalid range.</p>}
        {!durOk && <p className="text-sm text-blood">Duration must be a positive number or blank.</p>}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm text-mute">{times.length} {times.length === 1 ? 'cue' : 'cues'}</span>
          <div className="flex gap-2">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={!canGenerate}
              onClick={() => onGenerate({ times, templateId, laneId, durationOverride })}>
              Generate
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
