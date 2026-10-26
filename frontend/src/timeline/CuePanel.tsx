import { useState } from 'react'
import { api } from '../api'
import { LightStateEditor } from '../components/LightStateEditor'
import { ErrorText } from '../components/ui'
import { fmtTime, useAction } from '../lib'
import type { Board } from '../types'
import { round3, stateLabel, type Lane, type TLCue } from './model'

const fmt = (v: number | null) => (v == null ? '' : String(v))

/** Number input with a local draft so partial input ("1.", "") doesn't snap back. Blank commits null. */
function NumField({ value, onCommit, step, min = 0, placeholder, disabled }: {
  value: number | null
  onCommit: (v: number | null) => void
  step: number
  min?: number
  placeholder?: string
  disabled?: boolean
}) {
  const [prev, setPrev] = useState(value)
  const [text, setText] = useState(fmt(value))
  if (value !== prev) {
    setPrev(value)
    if ((text === '' ? null : Number(text)) !== value) setText(fmt(value))
  }
  return (
    <input
      type="number" className="input" step={step} min={min} placeholder={placeholder} disabled={disabled} value={text}
      onChange={(e) => {
        const t = e.target.value
        setText(t)
        if (t === '') { onCommit(null); return }
        const n = Number(t)
        if (Number.isFinite(n) && n >= min) onCommit(n)
      }}
    />
  )
}

export function CuePanel({ cue, boards, onChange, onDelete, onDuplicate, readOnly }: {
  cue: TLCue
  boards: Board[]
  lanes?: Lane[]
  onChange: (next: TLCue) => void
  onDelete: () => void
  onDuplicate: () => void
  readOnly?: boolean
}) {
  const [capSel, setCapSel] = useState('')
  const { busy, error, run } = useAction()
  const nameOf = (mac: string) => boards.find((b) => b.mac === mac)?.name ?? mac
  const extraMacs = cue.target.macs.filter((m) => !boards.some((b) => b.mac === m))
  const capMac = boards.some((b) => b.mac === capSel)
    ? capSel
    : (cue.target.all ? undefined : cue.target.macs.find((m) => boards.some((b) => b.mac === m))) ?? boards[0]?.mac ?? ''
  const effects = boards.find((b) => b.effects.length)?.effects ?? []

  if (readOnly) {
    return (
      <div className="card space-y-2 text-sm">
        <h3 className="font-semibold">Cue</h3>
        <p>Start: {fmtTime(cue.start_s)} ({cue.start_s.toFixed(2)} s)</p>
        <p>Duration: {cue.duration_s == null ? 'hold until next cue' : `${cue.duration_s.toFixed(2)} s`}</p>
        <p>Transition: {cue.transition_ms == null ? 'default' : `${cue.transition_ms} ms`}</p>
        <p>Target: {cue.target.all ? 'All selected boards' : cue.target.macs.map(nameOf).join(', ')}</p>
        <p>State: {stateLabel(cue, effects)}</p>
        <p className="text-mute">Editing works on desktop.</p>
      </div>
    )
  }

  const toggleMac = (mac: string, on: boolean) => {
    const macs = on ? [...cue.target.macs, mac] : cue.target.macs.filter((m) => m !== mac)
    if (macs.length) onChange({ ...cue, target: { all: false, macs } })
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">Cue</h3>
        <div className="flex gap-2">
          <button type="button" className="btn" onClick={onDuplicate}>Duplicate</button>
          <button type="button" className="btn btn-danger" onClick={onDelete}>Delete</button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <span className="label">Start (s)</span>
          <NumField value={cue.start_s} step={0.01} onCommit={(v) => v != null && onChange({ ...cue, start_s: round3(v) })} />
        </div>
        <div>
          <span className="label">Duration (s)</span>
          <NumField value={cue.duration_s} step={0.01} placeholder="hold" disabled={cue.duration_s == null}
            onCommit={(v) => onChange({ ...cue, duration_s: v == null ? null : round3(v) })} />
          <label className="mt-1 flex items-center gap-2 text-xs text-mute">
            <input type="checkbox" checked={cue.duration_s == null}
              onChange={(e) => onChange({ ...cue, duration_s: e.target.checked ? null : 1 })} /> hold
          </label>
        </div>
        <div className="col-span-2">
          <span className="label">Cue transition (ms)</span>
          <NumField value={cue.transition_ms} step={50} placeholder="default"
            onCommit={(v) => onChange({ ...cue, transition_ms: v == null ? null : Math.round(v) })} />
        </div>
      </div>

      <div className="space-y-1 text-sm">
        <span className="label">Target</span>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={cue.target.all} disabled={cue.target.all && boards.length === 0}
            onChange={(e) => onChange({ ...cue, target: e.target.checked ? { all: true, macs: [] } : { all: false, macs: boards.map((b) => b.mac) } })} />
          All selected boards
        </label>
        {!cue.target.all && (
          <div className="space-y-1 pl-5">
            {boards.map((b) => {
              const on = cue.target.macs.includes(b.mac)
              return (
                <label key={b.mac} className="flex items-center gap-2">
                  <input type="checkbox" checked={on} disabled={on && cue.target.macs.length === 1}
                    onChange={(e) => toggleMac(b.mac, e.target.checked)} />
                  {b.name}
                </label>
              )
            })}
            {extraMacs.map((m) => (
              <label key={m} className="flex items-center gap-2 text-mute">
                <input type="checkbox" checked disabled={cue.target.macs.length === 1} onChange={() => toggleMac(m, false)} />
                {m} (not in selection)
              </label>
            ))}
          </div>
        )}
      </div>

      <LightStateEditor value={cue.state} boards={boards} onChange={(state) => onChange({ ...cue, state })} />

      <div className="space-y-1">
        <span className="label">Capture from board</span>
        <div className="flex gap-2">
          <select className="input" value={capMac} onChange={(e) => setCapSel(e.target.value)} disabled={!boards.length}>
            {boards.map((b) => <option key={b.mac} value={b.mac}>{b.name}</option>)}
          </select>
          <button type="button" className="btn shrink-0" disabled={busy || !capMac}
            onClick={() => run(async () => { const state = await api.boards.capture(capMac); onChange({ ...cue, state }) })}>
            Capture
          </button>
        </div>
        <ErrorText error={error} />
      </div>
    </div>
  )
}

export function MultiPanel({ count, onDelete, onDuplicate, onNudge, readOnly }: {
  count: number
  onDelete: () => void
  onDuplicate: () => void
  onNudge: (delta: number) => void
  readOnly?: boolean
}) {
  return (
    <div className="card space-y-3">
      <h3 className="font-semibold">{count} cues selected</h3>
      {readOnly ? (
        <p className="text-sm text-mute">Editing works on desktop.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn" onClick={() => onNudge(-0.1)}>−0.1 s</button>
            <button type="button" className="btn" onClick={() => onNudge(0.1)}>+0.1 s</button>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn" onClick={onDuplicate}>Duplicate all</button>
            <button type="button" className="btn btn-danger" onClick={onDelete}>Delete all</button>
          </div>
        </>
      )}
    </div>
  )
}
