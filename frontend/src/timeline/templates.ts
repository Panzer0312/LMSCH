// Cue templates for the timeline editor. Add new templates by appending to the array.
import type { Board, LightState } from '../types'

export interface CueTemplate {
  id: string
  name: string
  duration_s: number | null
  state: LightState
  effect?: string[] /* candidate WLED effect names, first found in board.effects wins */
}

export const CUE_TEMPLATES: CueTemplate[] = [
  { id: 'flicker', name: 'Flicker', duration_s: 3, effect: ['Fire Flicker', 'Candle', 'Flicker'], state: { on: true, colors: [[255, 120, 20]], bri: 200, sx: 200, ix: 160 } },
  { id: 'strobe', name: 'Strobe', duration_s: 1.5, effect: ['Strobe', 'Strobe Mega'], state: { on: true, colors: [[255, 255, 255]], bri: 255, sx: 220 } },
  { id: 'blackout', name: 'Blackout', duration_s: null, state: { on: false, transition_ms: 0 } },
  { id: 'blood-red', name: 'Blood red', duration_s: null, effect: ['Solid'], state: { on: true, colors: [[180, 0, 0]], bri: 220, transition_ms: 800 } },
  { id: 'lightning', name: 'Lightning flash', duration_s: 1.2, effect: ['Lightning'], state: { on: true, colors: [[220, 230, 255]], bri: 255, sx: 255, ix: 200 } },
]

export function resolveTemplate(t: CueTemplate, boards: Board[]): LightState {
  const state: LightState = { ...t.state }
  if (!t.effect?.length) return state
  const effects = boards.find((b) => b.effects.length > 0)?.effects
  if (!effects) return state
  const lower = effects.map((e) => e.toLowerCase())
  for (const name of t.effect) {
    const idx = lower.indexOf(name.toLowerCase())
    if (idx >= 0) return { ...state, fx: idx }
  }
  return state
}
