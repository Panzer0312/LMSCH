import type { Board, LightState, RGB } from '../types'

const hex = (c: RGB) => '#' + c.map((v) => v.toString(16).padStart(2, '0')).join('')
const rgb = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB

/**
 * Edits a LightState: preset OR explicit fields. Effect/palette/preset names come from the
 * given boards (first board that has the list); ids are WLED indices, shared across stock builds.
 */
export function LightStateEditor({ value, onChange, boards }: {
  value: LightState
  onChange: (v: LightState) => void
  boards: Board[]
}) {
  const ref = boards.find((b) => b.effects.length) ?? boards[0]
  const effects = ref?.effects ?? []
  const palettes = ref?.palettes ?? []
  const presets = boards.find((b) => Object.keys(b.presets).length)?.presets ?? {}
  const usePreset = value.preset != null
  const set = (patch: Partial<LightState>) => onChange({ ...value, ...patch })
  const colors = value.colors ?? []

  return (
    <div className="space-y-3">
      <div className="flex gap-2 text-sm">
        <button type="button" className={`btn ${!usePreset ? 'border-ghost' : ''}`} onClick={() => set({ preset: null })}>Explicit</button>
        <button type="button" className={`btn ${usePreset ? 'border-ghost' : ''}`} onClick={() => set({ preset: Number(Object.keys(presets)[0] ?? 1) })}>Preset</button>
      </div>

      {usePreset ? (
        <div>
          <span className="label">Preset</span>
          <select className="input" value={value.preset ?? ''} onChange={(e) => set({ preset: Number(e.target.value) })}>
            {Object.entries(presets).map(([id, n]) => <option key={id} value={id}>{id}: {n}</option>)}
            {!(String(value.preset) in presets) && <option value={value.preset ?? ''}>#{value.preset}</option>}
          </select>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2 flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={value.on ?? true} onChange={(e) => set({ on: e.target.checked })} /> On
            </label>
            <div className="flex-1">
              <span className="label">Brightness {value.bri ?? '–'}</span>
              <input type="range" min={0} max={255} className="w-full accent-pumpkin" value={value.bri ?? 128} onChange={(e) => set({ bri: Number(e.target.value) })} />
            </div>
          </div>
          <div>
            <span className="label">Effect</span>
            <select className="input" value={value.fx ?? ''} onChange={(e) => set({ fx: e.target.value === '' ? null : Number(e.target.value) })}>
              <option value="">(keep)</option>
              {effects.map((n, i) => <option key={i} value={i}>{n}</option>)}
            </select>
          </div>
          <div>
            <span className="label">Palette</span>
            <select className="input" value={value.pal ?? ''} onChange={(e) => set({ pal: e.target.value === '' ? null : Number(e.target.value) })}>
              <option value="">(keep)</option>
              {palettes.map((n, i) => <option key={i} value={i}>{n}</option>)}
            </select>
          </div>
          <div>
            <span className="label">Speed {value.sx ?? '–'}</span>
            <input type="range" min={0} max={255} className="w-full accent-ghost" value={value.sx ?? 128} onChange={(e) => set({ sx: Number(e.target.value) })} />
          </div>
          <div>
            <span className="label">Intensity {value.ix ?? '–'}</span>
            <input type="range" min={0} max={255} className="w-full accent-ghost" value={value.ix ?? 128} onChange={(e) => set({ ix: Number(e.target.value) })} />
          </div>
          <div className="col-span-2">
            <span className="label">Colors</span>
            <div className="flex items-center gap-2">
              {[0, 1, 2].map((i) => (
                <input
                  key={i}
                  type="color"
                  className="h-9 w-12 cursor-pointer rounded border border-line bg-transparent"
                  value={colors[i] ? hex(colors[i]) : '#000000'}
                  onChange={(e) => {
                    const next = [...colors]
                    while (next.length < i) next.push([0, 0, 0])
                    next[i] = rgb(e.target.value)
                    set({ colors: next })
                  }}
                />
              ))}
              {colors.length > 0 && <button type="button" className="btn" onClick={() => set({ colors: null })}>Clear</button>}
            </div>
          </div>
        </div>
      )}
      <div>
        <span className="label">Transition (ms)</span>
        <input type="number" min={0} step={100} className="input" value={value.transition_ms ?? ''} placeholder="board default"
          onChange={(e) => set({ transition_ms: e.target.value === '' ? null : Number(e.target.value) })} />
      </div>
    </div>
  )
}
