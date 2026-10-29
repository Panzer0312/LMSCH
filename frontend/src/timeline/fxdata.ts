// WLED fxdata string format: "<sliders>;<colors>;<palette>;<flags>;<defaults>". Slider part is comma
// separated for sx,ix,c1,c2,c3 (and checkboxes o1,o2,o3); empty entry = slider unused; "!" = default
// label; missing whole string = all default.

export interface FxSlider { key: 'sx' | 'ix'; label: string }
export interface FxMeta { sliders: FxSlider[]; usesPalette: boolean; colors: number }

const SLIDERS: { key: 'sx' | 'ix'; label: string }[] = [
  { key: 'sx', label: 'Speed' },
  { key: 'ix', label: 'Intensity' },
]

const DEFAULT_META: FxMeta = { sliders: SLIDERS.map((s) => ({ ...s })), usesPalette: true, colors: 3 }

export function parseFxData(s: string | undefined | null): FxMeta {
  if (s == null) return { ...DEFAULT_META, sliders: DEFAULT_META.sliders.map((x) => ({ ...x })) }
  const at = s.indexOf('@')
  // No '@' with no ';' or ',' is just a bare effect name, which WLED treats as "all default".
  const data = at >= 0 ? s.slice(at + 1) : /[;,!]/.test(s) ? s : ''
  if (data.trim() === '') return { ...DEFAULT_META, sliders: DEFAULT_META.sliders.map((x) => ({ ...x })) }

  const parts = data.split(';')
  const sliderPart = parts[0] ?? ''
  const colorPart = parts[1] ?? ''
  const palPart = parts[2] ?? ''

  const entries = sliderPart === '' ? [] : sliderPart.split(',')
  const sliders: FxSlider[] = []
  SLIDERS.forEach((def, i) => {
    const raw = (entries[i] ?? '').trim()
    if (raw === '') return
    sliders.push({ key: def.key, label: raw === '!' ? def.label : raw })
  })

  const colors = colorPart === '' ? 0 : Math.min(3, colorPart.split(',').filter((c) => c.trim() !== '').length)
  const usesPalette = palPart.trim() !== ''

  return { sliders, usesPalette, colors }
}
