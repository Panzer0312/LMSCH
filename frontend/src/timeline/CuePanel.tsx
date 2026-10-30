import { useState } from 'react'
import { api } from '../api'
import { LightStateEditor } from '../components/LightStateEditor'
import { ErrorText } from '../components/ui'
import { fmtTime, useAction } from '../lib'
import type { Board, Media, SoundSpec } from '../types'
import { isSound, lanesOf, parseLaneId, round3, segLaneId, stateLabel, targetFromLanes, type Lane, type TLCue } from './model'

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

const MIN_DUR = 0.05

export function CuePanel({ cue, boards, audio = [], onChange, onDelete, onDuplicate, readOnly, fps = 25 }: {
  cue: TLCue
  boards: Board[]
  /** Audio media for the sound cue picker. */
  audio?: Media[]
  lanes?: Lane[]
  onChange: (next: TLCue) => void
  onDelete: () => void
  onDuplicate: () => void
  readOnly?: boolean
  fps?: number
}) {
  const [capSel, setCapSel] = useState('')
  const { busy, error, run } = useAction()
  const nameOf = (mac: string) => boards.find((b) => b.mac === mac)?.name ?? mac
  const extraMacs = cue.target.macs.filter((m) => !boards.some((b) => b.mac === m))
  const capMac = boards.some((b) => b.mac === capSel)
    ? capSel
    : (cue.target.all ? undefined : cue.target.macs.find((m) => boards.some((b) => b.mac === m))) ?? boards[0]?.mac ?? ''
  const effects = boards.find((b) => b.effects.length)?.effects ?? []
  const fxBoard = cue.target.all
    ? boards.find((b) => b.fxdata.length > 0)
    : cue.target.macs.map((m) => boards.find((b) => b.mac === m)).find((b) => b != null)
  const frame = 1 / (fps > 0 ? fps : 25)
  const nudges: [string, number][] = [['−1f', -frame], ['−0.1', -0.1], ['+0.1', 0.1], ['+1f', frame]]

  if (isSound(cue)) {
    return <SoundCuePanel cue={cue} audio={audio} nudges={nudges} readOnly={readOnly}
      onChange={onChange} onDelete={onDelete} onDuplicate={onDuplicate} />
  }

  if (readOnly) {
    return (
      <div className="card space-y-2 text-sm">
        <h3 className="font-semibold">Cue</h3>
        <p>Start: {fmtTime(cue.start_s)} ({cue.start_s.toFixed(2)} s)</p>
        <p>Duration: {cue.duration_s == null ? 'hold until next cue' : `${cue.duration_s.toFixed(2)} s`}</p>
        <p>Transition: {cue.transition_ms == null ? 'default' : `${cue.transition_ms} ms`}</p>
        <p>Target: {cue.target.all ? 'All selected boards' : lanesOf(cue).map((id) => {
          const { mac, seg } = parseLaneId(id)
          return seg == null ? nameOf(mac) : `${nameOf(mac)} seg ${seg}`
        }).join(', ')}</p>
        <p>State: {stateLabel(cue, effects)}</p>
        <p className="text-mute">Editing works on desktop.</p>
      </div>
    )
  }

  // Targets are edited as a lane-id set: a board lane and its segment lanes are mutually exclusive.
  const cur = lanesOf(cue)
  const setLanes = (ids: string[]) => { if (ids.length) onChange({ ...cue, target: targetFromLanes(ids) }) }
  const toggleMac = (mac: string, on: boolean) => {
    const rest = cur.filter((id) => parseLaneId(id).mac !== mac)
    setLanes(on ? [...rest, mac] : rest)
  }
  const toggleSeg = (mac: string, seg: number, on: boolean) => {
    const id = segLaneId(mac, seg)
    const rest = cur.filter((x) => x !== id && x !== mac)
    setLanes(on ? [...rest, id] : rest)
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
        <div className="col-span-2">
          <span className="label">Label</span>
          <input className="input" value={cue.label ?? ''} placeholder="(none)"
            onChange={(e) => onChange({ ...cue, label: e.target.value === '' ? null : e.target.value })} />
        </div>
        <div className="col-span-2">
          <span className="label">Color</span>
          <div className="flex items-center gap-2">
            <input type="color" className="h-9 w-12 cursor-pointer rounded border border-line bg-transparent"
              value={cue.color ?? '#808080'} onChange={(e) => onChange({ ...cue, color: e.target.value })} />
            <span className="text-xs text-mute">{cue.color ?? 'auto'}</span>
            <button type="button" className="btn" disabled={cue.color == null} onClick={() => onChange({ ...cue, color: null })}>Clear</button>
          </div>
        </div>
        <div>
          <span className="label">Start (s)</span>
          <NumField value={cue.start_s} step={0.001} onCommit={(v) => v != null && onChange({ ...cue, start_s: round3(v) })} />
          <div className="mt-1 flex flex-wrap gap-1">
            {nudges.map(([lbl, d]) => (
              <button key={lbl} type="button" className="btn px-2 py-1 text-xs"
                onClick={() => onChange({ ...cue, start_s: round3(Math.max(0, cue.start_s + d)) })}>{lbl}</button>
            ))}
          </div>
        </div>
        <div>
          <span className="label">Duration (s)</span>
          <NumField value={cue.duration_s} step={0.001} min={MIN_DUR} placeholder="hold" disabled={cue.duration_s == null}
            onCommit={(v) => onChange({ ...cue, duration_s: v == null ? null : round3(Math.max(MIN_DUR, v)) })} />
          {cue.duration_s != null && (
            <div className="mt-1 flex flex-wrap gap-1">
              {nudges.map(([lbl, d]) => (
                <button key={lbl} type="button" className="btn px-2 py-1 text-xs"
                  onClick={() => cue.duration_s != null && onChange({ ...cue, duration_s: round3(Math.max(MIN_DUR, cue.duration_s + d)) })}>{lbl}</button>
              ))}
            </div>
          )}
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
              const on = cur.includes(b.mac)
              return (
                <div key={b.mac}>
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={on} disabled={on && cur.length === 1}
                      onChange={(e) => toggleMac(b.mac, e.target.checked)} />
                    {b.name}
                  </label>
                  {b.segments.length > 1 && (
                    <div className="flex flex-wrap gap-x-3 pl-5 text-xs text-mute">
                      {b.segments.map((sg) => {
                        const sOn = cur.includes(segLaneId(b.mac, sg.id))
                        return (
                          <label key={sg.id} className="flex items-center gap-1">
                            <input type="checkbox" checked={sOn} disabled={sOn && cur.length === 1}
                              onChange={(e) => toggleSeg(b.mac, sg.id, e.target.checked)} />
                            {sg.name || `Seg ${sg.id}`}
                          </label>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
            {extraMacs.map((m) => (
              <label key={m} className="flex items-center gap-2 text-mute">
                <input type="checkbox" checked disabled={cur.length === 1} onChange={() => toggleMac(m, false)} />
                {m} (not in selection)
              </label>
            ))}
          </div>
        )}
      </div>

      <LightStateEditor value={cue.state} boards={boards} fxdata={fxBoard?.fxdata} onChange={(state) => onChange({ ...cue, state })} />

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

const DEFAULT_SOUND: SoundSpec = { media_id: 0, volume: 1, fade_in_ms: 0, fade_out_ms: 0, loop: false }

function SoundCuePanel({ cue, audio, nudges, readOnly, onChange, onDelete, onDuplicate }: {
  cue: TLCue
  audio: Media[]
  nudges: [string, number][]
  readOnly?: boolean
  onChange: (next: TLCue) => void
  onDelete: () => void
  onDuplicate: () => void
}) {
  const snd = cue.sound ?? DEFAULT_SOUND
  const media = audio.find((m) => m.id === snd.media_id)
  const setSound = (patch: Partial<SoundSpec>) => onChange({ ...cue, sound: { ...snd, ...patch } })
  const toEnd = media ? `to end (${media.duration_s.toFixed(2)} s)` : 'to end'

  if (readOnly) {
    return (
      <div className="card space-y-2 text-sm">
        <h3 className="font-semibold">Sound cue</h3>
        <p>Sound: {media?.filename ?? `media #${snd.media_id}`}</p>
        <p>Start: {fmtTime(cue.start_s)} ({cue.start_s.toFixed(2)} s)</p>
        <p>Duration: {cue.duration_s == null ? toEnd : `${cue.duration_s.toFixed(2)} s`}</p>
        <p>Volume: {Math.round(snd.volume * 100)} % · fade {snd.fade_in_ms}/{snd.fade_out_ms} ms{snd.loop ? ' · loop' : ''}</p>
        <p className="text-mute">Editing works on desktop.</p>
      </div>
    )
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">Sound cue</h3>
        <div className="flex gap-2">
          <button type="button" className="btn" onClick={onDuplicate}>Duplicate</button>
          <button type="button" className="btn btn-danger" onClick={onDelete}>Delete</button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <span className="label">Sound</span>
          <select className="input" value={snd.media_id} onChange={(e) => setSound({ media_id: Number(e.target.value) })}>
            {!media && <option value={snd.media_id}>{`media #${snd.media_id} (missing)`}</option>}
            {audio.map((m) => <option key={m.id} value={m.id}>{m.filename}</option>)}
          </select>
        </div>
        <div className="col-span-2">
          <span className="label">Label</span>
          <input className="input" value={cue.label ?? ''} placeholder="(none)"
            onChange={(e) => onChange({ ...cue, label: e.target.value === '' ? null : e.target.value })} />
        </div>
        <div className="col-span-2">
          <span className="label">Color</span>
          <div className="flex items-center gap-2">
            <input type="color" className="h-9 w-12 cursor-pointer rounded border border-line bg-transparent"
              value={cue.color ?? '#4fc3f7'} onChange={(e) => onChange({ ...cue, color: e.target.value })} />
            <span className="text-xs text-mute">{cue.color ?? 'auto'}</span>
            <button type="button" className="btn" disabled={cue.color == null} onClick={() => onChange({ ...cue, color: null })}>Clear</button>
          </div>
        </div>
        <div>
          <span className="label">Start (s)</span>
          <NumField value={cue.start_s} step={0.001} onCommit={(v) => v != null && onChange({ ...cue, start_s: round3(v) })} />
          <div className="mt-1 flex flex-wrap gap-1">
            {nudges.map(([lbl, d]) => (
              <button key={lbl} type="button" className="btn px-2 py-1 text-xs"
                onClick={() => onChange({ ...cue, start_s: round3(Math.max(0, cue.start_s + d)) })}>{lbl}</button>
            ))}
          </div>
        </div>
        <div>
          <span className="label">Duration (s)</span>
          <NumField value={cue.duration_s} step={0.001} min={MIN_DUR} placeholder={toEnd}
            onCommit={(v) => onChange({ ...cue, duration_s: v == null ? null : round3(Math.max(MIN_DUR, v)) })} />
          <p className="mt-1 text-xs text-mute">Empty = play to the end.</p>
        </div>
        <div className="col-span-2">
          <span className="label">Volume {Math.round(snd.volume * 100)} %</span>
          <input type="range" className="w-full" min={0} max={1.3} step={0.01} value={snd.volume}
            onChange={(e) => setSound({ volume: Number(e.target.value) })} />
        </div>
        <div>
          <span className="label">Fade in (ms)</span>
          <NumField value={snd.fade_in_ms} step={50} placeholder="0"
            onCommit={(v) => setSound({ fade_in_ms: Math.round(v ?? 0) })} />
        </div>
        <div>
          <span className="label">Fade out (ms)</span>
          <NumField value={snd.fade_out_ms} step={50} placeholder="0"
            onCommit={(v) => setSound({ fade_out_ms: Math.round(v ?? 0) })} />
        </div>
        <label className="col-span-2 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={snd.loop} onChange={(e) => setSound({ loop: e.target.checked })} />
          Loop (set a duration to stop it)
        </label>
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
