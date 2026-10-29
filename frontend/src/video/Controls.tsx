import { useState, type KeyboardEvent, type ReactNode } from 'react'
import type { DisplayGeometry } from '../types'
import { Toggle } from '../components/ui'
import { clamp, resetGroup, type Group } from './geometry'

const decimals = (step: number) => Math.max(0, Math.ceil(-Math.log10(step)))
const roundTo = (v: number, step: number) => Number(v.toFixed(decimals(step)))

/** Arrow keys nudge by `step`, Shift by `fine`. Returns true when handled. */
function nudge(e: KeyboardEvent, value: number, step: number, fine: number, set: (v: number) => void): boolean {
  const d = e.shiftKey ? fine : step
  const dir = e.key === 'ArrowUp' || e.key === 'ArrowRight' ? 1 : e.key === 'ArrowDown' || e.key === 'ArrowLeft' ? -1 : 0
  if (!dir) return false
  e.preventDefault()
  set(value + dir * d)
  return true
}

export function NumberRow({ label, value, min, max, step, fine, unit, disabled, onChange }: {
  label: string; value: number; min: number; max: number; step: number; fine?: number; unit?: string
  disabled?: boolean; onChange: (v: number) => void
}) {
  // Draft text only while the field has focus, so typing "-0." is possible.
  const [draft, setDraft] = useState<string | null>(null)
  const f = fine ?? step / 10
  const set = (v: number) => onChange(roundTo(clamp(v, min, max), Math.min(step, f)))
  const fmt = (v: number) => String(roundTo(v, Math.min(step, f)))
  const commit = (txt: string) => {
    const n = Number(txt)
    if (txt.trim() !== '' && Number.isFinite(n)) set(n)
  }
  return (
    <div className="space-y-1 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate">{label}</span>
        <span className="flex items-center gap-1">
          <input type="text" inputMode="decimal" aria-label={label} disabled={disabled}
            className="w-20 rounded-md border border-line bg-bg px-2 py-0.5 text-right font-mono text-xs outline-none focus:border-ghost"
            value={draft ?? fmt(value)}
            onFocus={() => setDraft(fmt(value))}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={(e) => { if (e.target.value !== fmt(value)) commit(e.target.value); setDraft(null) }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { commit(e.currentTarget.value); setDraft(null); e.currentTarget.blur(); return }
              nudge(e, value, step, f, (v) => { set(v); setDraft(fmt(clamp(v, min, max))) })
            }} />
          {unit && <span className="w-4 text-xs text-mute">{unit}</span>}
        </span>
      </div>
      <input type="range" aria-label={`${label} slider`} className="w-full accent-pumpkin" min={min} max={max} step={Math.min(step, f)}
        value={value} disabled={disabled}
        onChange={(e) => set(Number(e.target.value))}
        onKeyDown={(e) => { nudge(e, value, step, f, set) }} />
    </div>
  )
}

function GroupCard({ title, onReset, children }: { title: string; onReset: () => void; children: ReactNode }) {
  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">{title}</h3>
        <button type="button" className="btn px-2 py-1 text-xs" onClick={onReset}>Reset</button>
      </div>
      {children}
    </section>
  )
}

const CORNER_NAMES = ['1 top-left', '2 top-right', '3 bottom-right', '4 bottom-left']

export function Controls({ g, onChange }: { g: DisplayGeometry; onChange: (g: DisplayGeometry) => void }) {
  const set = <K extends keyof DisplayGeometry>(k: K, v: DisplayGeometry[K]) => onChange({ ...g, [k]: v })
  const reset = (grp: Group) => onChange(resetGroup(g, grp))
  const setCrop = (k: keyof DisplayGeometry['crop'], v: number) => {
    const c = { ...g.crop, [k]: v }
    // Keep the crop window inside the source.
    if (k === 'x') c.w = Math.min(c.w, 1 - v)
    if (k === 'y') c.h = Math.min(c.h, 1 - v)
    if (k === 'w') c.x = Math.min(c.x, 1 - v)
    if (k === 'h') c.y = Math.min(c.y, 1 - v)
    set('crop', c)
  }
  const setMask = (k: keyof DisplayGeometry['mask'], v: number) => set('mask', { ...g.mask, [k]: v })
  const setCorner = (i: number, axis: 0 | 1, v: number) => {
    const c = g.corners.map((p) => [...p] as [number, number])
    c[i][axis] = v
    set('corners', c)
  }

  return (
    <div className="grid gap-3 md:grid-cols-2">
      <GroupCard title="Frame" onReset={() => reset('frame')}>
        <NumberRow label="Zoom (log2)" value={g.zoom} min={-2} max={2} step={0.01} fine={0.001} onChange={(v) => set('zoom', v)} />
        <NumberRow label="Pan X" value={g.pan_x} min={-1} max={1} step={0.005} fine={0.0005} onChange={(v) => set('pan_x', v)} />
        <NumberRow label="Pan Y" value={g.pan_y} min={-1} max={1} step={0.005} fine={0.0005} onChange={(v) => set('pan_y', v)} />
        <NumberRow label="Stretch X" value={g.scale_x} min={0.25} max={4} step={0.01} fine={0.001} onChange={(v) => set('scale_x', v)} />
        <NumberRow label="Stretch Y" value={g.scale_y} min={0.25} max={4} step={0.01} fine={0.001} onChange={(v) => set('scale_y', v)} />
        <NumberRow label="Align X" value={g.align_x} min={-1} max={1} step={0.01} fine={0.001} onChange={(v) => set('align_x', v)} />
        <NumberRow label="Align Y" value={g.align_y} min={-1} max={1} step={0.01} fine={0.001} onChange={(v) => set('align_y', v)} />
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>Rotate</span>
          {([0, 90, 180, 270] as const).map((r) => (
            <button key={r} type="button" className={`btn px-2 py-1 text-xs ${g.rotate === r ? 'border-pumpkin text-pumpkin' : ''}`}
              onClick={() => set('rotate', r)}>{r}°</button>
          ))}
        </div>
        <div className="space-y-1">
          <Toggle checked={g.aspect !== null} onChange={(on) => set('aspect', on ? 16 / 9 : null)} label="Override aspect" />
          {g.aspect !== null && (
            <NumberRow label="Aspect (w/h)" value={g.aspect} min={0.2} max={10} step={0.01} fine={0.001} onChange={(v) => set('aspect', v)} />
          )}
        </div>
        <p className="label pt-1">Crop source</p>
        <NumberRow label="Crop left" value={g.crop.x} min={0} max={0.99} step={0.005} fine={0.0005} onChange={(v) => setCrop('x', v)} />
        <NumberRow label="Crop top" value={g.crop.y} min={0} max={0.99} step={0.005} fine={0.0005} onChange={(v) => setCrop('y', v)} />
        <NumberRow label="Crop width" value={g.crop.w} min={0.01} max={1} step={0.005} fine={0.0005} onChange={(v) => setCrop('w', v)} />
        <NumberRow label="Crop height" value={g.crop.h} min={0.01} max={1} step={0.005} fine={0.0005} onChange={(v) => setCrop('h', v)} />
        <div className="flex flex-wrap gap-4">
          <Toggle checked={g.flip_h} onChange={(v) => set('flip_h', v)} label="Flip H (rear projection)" />
          <Toggle checked={g.flip_v} onChange={(v) => set('flip_v', v)} label="Flip V (ceiling mount)" />
        </div>
      </GroupCard>

      <GroupCard title="Keystone" onReset={() => reset('keystone')}>
        <p className="text-xs text-mute">Drag the numbered handles in the preview. Strong digital keystone blurs; fix what you can with the projector lens/position first.</p>
        {CORNER_NAMES.map((n, i) => (
          <div key={n} className="grid grid-cols-2 gap-2">
            <NumberRow label={`${n} X`} value={g.corners[i][0]} min={-0.5} max={0.5} step={0.005} fine={0.0005} onChange={(v) => setCorner(i, 0, v)} />
            <NumberRow label={`${n} Y`} value={g.corners[i][1]} min={-0.5} max={0.5} step={0.005} fine={0.0005} onChange={(v) => setCorner(i, 1, v)} />
          </div>
        ))}
      </GroupCard>

      <GroupCard title="Mask" onReset={() => reset('mask')}>
        <NumberRow label="Top" value={g.mask.top} min={0} max={0.5} step={0.005} fine={0.0005} onChange={(v) => setMask('top', v)} />
        <NumberRow label="Right" value={g.mask.right} min={0} max={0.5} step={0.005} fine={0.0005} onChange={(v) => setMask('right', v)} />
        <NumberRow label="Bottom" value={g.mask.bottom} min={0} max={0.5} step={0.005} fine={0.0005} onChange={(v) => setMask('bottom', v)} />
        <NumberRow label="Left" value={g.mask.left} min={0} max={0.5} step={0.005} fine={0.0005} onChange={(v) => setMask('left', v)} />
        <NumberRow label="Feather" value={g.mask.feather} min={0} max={0.5} step={0.005} fine={0.0005} onChange={(v) => setMask('feather', v)} />
      </GroupCard>

      <GroupCard title="Image" onReset={() => reset('image')}>
        <NumberRow label="Brightness" value={g.brightness} min={-100} max={100} step={1} fine={1} onChange={(v) => set('brightness', Math.round(v))} />
        <NumberRow label="Contrast" value={g.contrast} min={-100} max={100} step={1} fine={1} onChange={(v) => set('contrast', Math.round(v))} />
        <NumberRow label="Gamma" value={g.gamma} min={-100} max={100} step={1} fine={1} onChange={(v) => set('gamma', Math.round(v))} />
        <NumberRow label="Saturation" value={g.saturation} min={-100} max={100} step={1} fine={1} onChange={(v) => set('saturation', Math.round(v))} />
      </GroupCard>
    </div>
  )
}
