import { useMemo, useState } from 'react'
import { api } from '../api'
import type { Board, Cue, LightState, Profile } from '../types'
import { LightStateEditor } from './LightStateEditor'
import { ErrorText } from './ui'
import { useAction } from '../lib'

interface Row { key: number; cue: Cue }

const strip = (c: Cue): Cue => ({
  start_s: c.start_s, duration_s: c.duration_s, target: c.target, state: c.state, transition_ms: c.transition_ms,
})

const sortCues = (cues: Cue[]) => [...cues].sort((a, b) => a.start_s - b.start_s)

let nextKey = 0
const toRows = (cues: Cue[]): Row[] => sortCues(cues).map((c) => ({ key: nextKey++, cue: strip(c) }))

function summary(s: LightState, boards: Board[]): string {
  if (s.preset != null) {
    const name = boards.find((b) => b.presets[String(s.preset)])?.presets[String(s.preset)]
    return `Preset ${s.preset}${name ? ` (${name})` : ''}`
  }
  const parts: string[] = []
  if (s.on === false) parts.push('off')
  if (s.fx != null) parts.push(`fx ${boards.find((b) => b.effects[s.fx!])?.effects[s.fx] ?? s.fx}`)
  if (s.pal != null) parts.push(`pal ${boards.find((b) => b.palettes[s.pal!])?.palettes[s.pal] ?? s.pal}`)
  if (s.bri != null) parts.push(`bri ${s.bri}`)
  if (s.colors?.length) parts.push('●color')
  return parts.length ? parts.join(' · ') : '(no change)'
}

export function CueList({ profile, boards, onSaved }: { profile: Profile; boards: Board[]; onSaved: (p: Profile) => void }) {
  const [rows, setRows] = useState<Row[]>(() => toRows(profile.cues))
  const [prevCues, setPrevCues] = useState(profile.cues)
  const [editing, setEditing] = useState<number | null>(null)
  const [saved, setSaved] = useState(false)
  const { busy, error, run } = useAction()

  if (profile.cues !== prevCues) {
    setPrevCues(profile.cues)
    setRows(toRows(profile.cues))
    setEditing(null)
  }

  const original = useMemo(() => JSON.stringify(sortCues(profile.cues).map(strip)), [profile.cues])
  const dirty = JSON.stringify(rows.map((r) => r.cue)) !== original

  const allMacs = boards.map((b) => b.mac)
  const macsOf = (c: Cue) => (c.target.all ? allMacs : c.target.macs)

  const errors = new Map<number, string>()
  for (const r of rows) {
    const c = r.cue
    if (!isFinite(c.start_s) || c.start_s < 0) errors.set(r.key, 'Start must be ≥ 0')
    else if (c.duration_s != null && (!isFinite(c.duration_s) || c.duration_s <= 0)) errors.set(r.key, 'Duration must be > 0')
  }

  const overlaps = new Map<number, string>()
  for (const a of rows) {
    const d = a.cue.duration_s
    if (d == null || !(d > 0)) continue
    const aMacs = macsOf(a.cue)
    for (const b of rows) {
      if (b.key === a.key) continue
      if (b.cue.start_s < a.cue.start_s || b.cue.start_s >= a.cue.start_s + d) continue
      const bMacs = macsOf(b.cue)
      if (aMacs.some((m) => bMacs.includes(m))) {
        overlaps.set(b.key, `Overlaps cue at ${a.cue.start_s}s on a shared board`)
      }
    }
  }

  const update = (key: number, patch: Partial<Cue>) => {
    setSaved(false)
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, cue: { ...r.cue, ...patch } } : r)))
  }

  const add = () => {
    setSaved(false)
    const last = rows.reduce((m, r) => Math.max(m, isFinite(r.cue.start_s) ? r.cue.start_s : 0), -1)
    const cue: Cue = {
      start_s: last < 0 ? 0 : last + 1, duration_s: null, target: { all: true, macs: [] },
      state: { on: true, bri: 255 }, transition_ms: null,
    }
    setRows((rs) => [...rs, { key: nextKey++, cue }])
  }

  const duplicate = (key: number) => {
    setSaved(false)
    setRows((rs) => {
      const i = rs.findIndex((r) => r.key === key)
      if (i < 0) return rs
      const copy: Row = { key: nextKey++, cue: structuredClone(rs[i].cue) }
      return [...rs.slice(0, i + 1), copy, ...rs.slice(i + 1)]
    })
  }

  const remove = (key: number) => {
    setSaved(false)
    setRows((rs) => rs.filter((r) => r.key !== key))
    if (editing === key) setEditing(null)
  }

  const save = async () => {
    const cues = sortCues(rows.map((r) => strip(r.cue)))
    const p = await run(() => api.profiles.setCues(profile.id, cues))
    if (p) {
      setSaved(true)
      onSaved(p)
    }
  }

  const numOrNull = (v: string) => (v === '' ? null : Number(v))

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">
          Cues <span className="text-sm font-normal text-mute">({rows.length})</span>
          {dirty && <span className="ml-2 text-sm font-normal text-pumpkin">● unsaved</span>}
          {!dirty && saved && <span className="ml-2 text-sm font-normal text-slime">Saved</span>}
        </h2>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn" onClick={add}>Add cue</button>
          <button type="button" className="btn btn-primary" disabled={busy || !dirty || errors.size > 0} onClick={save}>
            {busy ? 'Saving…' : 'Save cues'}
          </button>
        </div>
      </div>
      <ErrorText error={error} />

      {rows.length === 0 && <p className="text-sm text-mute">No cues yet.</p>}

      <div className="space-y-2">
        {rows.map((r) => {
          const c = r.cue
          return (
            <div key={r.key} className="card space-y-3 p-3">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <div>
                  <span className="label">Start (s)</span>
                  <input type="number" min={0} step={0.1} className="input"
                    value={isFinite(c.start_s) ? c.start_s : ''}
                    onChange={(e) => update(r.key, { start_s: e.target.value === '' ? NaN : Number(e.target.value) })} />
                </div>
                <div>
                  <span className="label">Duration (s)</span>
                  <input type="number" min={0} step={0.1} className="input" placeholder="hold"
                    value={c.duration_s ?? ''}
                    onChange={(e) => update(r.key, { duration_s: numOrNull(e.target.value) })} />
                </div>
                <div>
                  <span className="label">Transition (ms)</span>
                  <input type="number" min={0} step={100} className="input" placeholder="default"
                    value={c.transition_ms ?? ''}
                    onChange={(e) => update(r.key, { transition_ms: numOrNull(e.target.value) })} />
                </div>
                <div className="col-span-2 sm:col-span-1">
                  <span className="label">Target</span>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={c.target.all}
                      onChange={(e) => update(r.key, { target: { ...c.target, all: e.target.checked } })} />
                    All selected boards
                  </label>
                </div>
              </div>

              {!c.target.all && (
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {boards.length === 0 && <span className="text-sm text-mute">No boards selected in profile.</span>}
                  {boards.map((b) => (
                    <label key={b.mac} className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={c.target.macs.includes(b.mac)}
                        onChange={(e) => update(r.key, {
                          target: {
                            ...c.target,
                            macs: e.target.checked ? [...c.target.macs, b.mac] : c.target.macs.filter((m) => m !== b.mac),
                          },
                        })} />
                      {b.name}
                    </label>
                  ))}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-sm text-mute">{summary(c.state, boards)}</span>
                <button type="button" className={`btn ${editing === r.key ? 'border-ghost' : ''}`}
                  onClick={() => setEditing(editing === r.key ? null : r.key)}>
                  {editing === r.key ? 'Done' : 'Edit'}
                </button>
                <button type="button" className="btn" onClick={() => duplicate(r.key)}>Duplicate</button>
                <button type="button" className="btn btn-danger" onClick={() => remove(r.key)}>Delete</button>
              </div>

              {editing === r.key && (
                <div className="rounded-lg border border-line bg-panel-2 p-3">
                  <LightStateEditor boards={boards} value={c.state} onChange={(s) => update(r.key, { state: s })} />
                </div>
              )}

              {errors.has(r.key) && <p className="text-sm text-blood">{errors.get(r.key)}</p>}
              {overlaps.has(r.key) && <p className="text-xs text-pumpkin">{overlaps.get(r.key)}</p>}
            </div>
          )
        })}
      </div>
    </section>
  )
}
