import { useEffect, useMemo, useRef, useState } from 'react'
import type { RGB, SavedTemplate } from '../types'
import { GENERATORS, defaults, type GenCue, type Generator, type ParamDef, type ParamValue } from './generators'

const hex = (c: RGB) => '#' + c.map((v) => v.toString(16).padStart(2, '0')).join('')
const rgb = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB

type Sel = { kind: 'gen'; id: string } | { kind: 'saved'; id: number }

function clamp(def: ParamDef, n: number): number {
  let v = def.kind === 'int' ? Math.round(n) : n
  if (def.min != null) v = Math.max(def.min, v)
  if (def.max != null) v = Math.min(def.max, v)
  return v
}

/** Local draft so partial input ("1.", "") doesn't snap back; out-of-range values clamp on blur. */
function ParamNum({ def, value, onCommit }: { def: ParamDef; value: number; onCommit: (v: number) => void }) {
  const [text, setText] = useState(String(value))
  const inRange = (n: number) => (def.min == null || n >= def.min) && (def.max == null || n <= def.max)
  return (
    <input
      type="number" className="input" min={def.min} max={def.max} step={def.step ?? (def.kind === 'int' ? 1 : 0.1)} value={text}
      onChange={(e) => {
        const t = e.target.value
        setText(t)
        const n = Number(t)
        if (t !== '' && Number.isFinite(n) && inRange(n)) onCommit(clamp(def, n))
      }}
      onBlur={() => {
        const n = Number(text)
        const v = text !== '' && Number.isFinite(n) ? clamp(def, n) : value
        setText(String(v))
        if (v !== value) onCommit(v)
      }}
    />
  )
}

function savedToGen(t: SavedTemplate): GenCue[] {
  return t.cues.map((c) => ({
    offset_s: c.offset_s,
    duration_s: c.duration_s,
    state: c.state,
    transition_ms: c.transition_ms,
    ...(c.label ? { label: c.label } : {}),
    color: c.color ?? null,
    lane: c.lane,
  }))
}

function totalLength(cues: GenCue[]): number {
  return cues.reduce((m, c) => Math.max(m, c.offset_s + (c.duration_s ?? 0)), 0)
}

export function TemplateDialog({ effects, saved, onPreview, onInsert, onDeleteSaved, onClose }: {
  effects: string[]
  saved: SavedTemplate[]
  onPreview: (cues: GenCue[] | null) => void
  onInsert: (cues: GenCue[]) => void
  onDeleteSaved: (id: number) => void
  onClose: () => void
}) {
  const [rawSel, setSel] = useState<Sel | null>(GENERATORS[0] ? { kind: 'gen', id: GENERATORS[0].id } : null)
  // Per-generator params so switching back and forth keeps edits.
  const [params, setParams] = useState<Record<string, Record<string, ParamValue>>>({})

  // A deleted saved template falls back to the first generator.
  const sel: Sel | null = rawSel?.kind === 'saved' && !saved.some((t) => t.id === rawSel.id)
    ? (GENERATORS[0] ? { kind: 'gen', id: GENERATORS[0].id } : null)
    : rawSel
  const gen: Generator | undefined = sel?.kind === 'gen' ? GENERATORS.find((g) => g.id === sel.id) : undefined
  const tpl: SavedTemplate | undefined = sel?.kind === 'saved' ? saved.find((t) => t.id === sel.id) : undefined
  const genParams = gen ? params[gen.id] : undefined

  const values = useMemo(() => (gen ? { ...defaults(gen), ...genParams } : null), [gen, genParams])
  const cues = useMemo<GenCue[]>(() => {
    if (gen && values) return gen.generate(values, effects)
    if (tpl) return savedToGen(tpl)
    return []
  }, [gen, values, tpl, effects])

  // Parent callbacks are usually inline; keep them in refs so effects only fire on real changes.
  const previewRef = useRef(onPreview)
  const closeRef = useRef(onClose)
  useEffect(() => {
    previewRef.current = onPreview
    closeRef.current = onClose
  })

  useEffect(() => { previewRef.current(cues.length ? cues : null) }, [cues])
  useEffect(() => () => previewRef.current(null), [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // Capture + stop so the editor's own Escape (clear selection) doesn't also fire.
      e.stopPropagation()
      e.preventDefault()
      closeRef.current()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])

  const setParam = (key: string, v: ParamValue) => {
    if (!gen) return
    const id = gen.id
    setParams((p) => ({ ...p, [id]: { ...p[id], [key]: v } }))
  }

  const itemCls = (active: boolean) =>
    `w-full truncate rounded-lg px-3 py-2 text-left text-sm ${active ? 'bg-pumpkin/20 text-ink' : 'text-mute hover:bg-panel-2'}`

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-2 sm:p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div role="dialog" aria-modal="true" aria-label="Insert template"
        className="card flex max-h-[92vh] w-full max-w-3xl flex-col gap-4 overflow-y-auto">
        <h3 className="font-semibold">Insert template</h3>

        <div className="grid gap-4 md:grid-cols-[13rem_1fr]">
          <div className="space-y-3">
            <div>
              <span className="label">Generators</span>
              <div className="space-y-1">
                {GENERATORS.map((g) => (
                  <button key={g.id} type="button" className={itemCls(sel?.kind === 'gen' && sel.id === g.id)}
                    onClick={() => setSel({ kind: 'gen', id: g.id })}>
                    {g.name}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <span className="label">Saved</span>
              {saved.length === 0 && <p className="px-3 text-sm text-mute">None yet.</p>}
              <div className="space-y-1">
                {saved.map((t) => (
                  <div key={t.id} className="flex items-center gap-1">
                    <button type="button" className={itemCls(sel?.kind === 'saved' && sel.id === t.id)}
                      onClick={() => setSel({ kind: 'saved', id: t.id })}>
                      {t.name}
                    </button>
                    <button type="button" className="shrink-0 rounded px-2 py-1 text-mute hover:text-blood"
                      aria-label={`Delete ${t.name}`} title="Delete" onClick={() => onDeleteSaved(t.id)}>
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="space-y-4">
            {gen && values && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {gen.params.map((d) => {
                  const v = values[d.key] ?? d.default
                  return (
                    <div key={d.key}>
                      <span className="label">{d.label}{d.unit ? ` (${d.unit})` : ''}</span>
                      {d.kind === 'color' ? (
                        <input type="color" className="h-9 w-full cursor-pointer rounded-lg border border-line bg-bg"
                          value={Array.isArray(v) ? hex(v) : '#000000'}
                          onChange={(e) => setParam(d.key, rgb(e.target.value))} />
                      ) : (
                        <ParamNum key={`${gen.id}:${d.key}`} def={d} value={typeof v === 'number' ? v : 0}
                          onCommit={(n) => setParam(d.key, n)} />
                      )}
                    </div>
                  )
                })}
              </div>
            )}
            {tpl && <p className="text-sm">{tpl.name}</p>}
            {!gen && !tpl && <p className="text-sm text-mute">Pick a generator or saved template.</p>}
            <p className="text-sm text-mute">
              {cues.length} cue{cues.length === 1 ? '' : 's'} · {totalLength(cues).toFixed(2)} s total
            </p>
          </div>
        </div>

        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={!cues.length}
            onClick={() => { onInsert(cues); onClose() }}>
            Insert
          </button>
        </div>
      </div>
    </div>
  )
}
