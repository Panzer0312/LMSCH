import { useEffect, useRef, useState } from 'react'
import { ALL_LANE } from './model'

const DROP = '__drop__'

export function RemapDialog({ sources, targets, onApply, onClose }: {
  sources: { id: string; label: string }[]
  targets: { id: string; label: string }[]
  onApply: (remap: Record<string, string | null>) => void
  onClose: () => void
}) {
  const [choice, setChoice] = useState<Record<string, string>>(() => {
    const firstBoard = targets.find((t) => t.id !== ALL_LANE)?.id
    const init: Record<string, string> = {}
    for (const s of sources) {
      init[s.id] = targets.some((t) => t.id === s.id) ? s.id : (firstBoard ?? DROP)
    }
    return init
  })

  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose })
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); closeRef.current() } }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  const apply = () => {
    const remap: Record<string, string | null> = {}
    for (const s of sources) {
      const v = choice[s.id] ?? DROP
      remap[s.id] = v === DROP ? null : v
    }
    onApply(remap)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Remap pasted lanes"
        className="card max-h-[90vh] w-full max-w-md space-y-3 overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <h2 className="text-base font-semibold">Remap pasted lanes</h2>
        <p className="text-sm text-mute">Pasted cues target boards not in this profile. Pick a lane for each, or drop it.</p>

        <div className="space-y-2">
          {sources.map((s) => (
            <label key={s.id} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
              <span className="min-w-0 truncate font-mono text-sm sm:w-40 sm:shrink-0" title={s.id}>{s.label}</span>
              <select className="input" value={choice[s.id] ?? DROP}
                onChange={(e) => { const v = e.target.value; setChoice((c) => ({ ...c, [s.id]: v })) }}>
                {targets.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                <option value={DROP}>Drop</option>
              </select>
            </label>
          ))}
        </div>

        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={apply}>Paste</button>
        </div>
      </div>
    </div>
  )
}
