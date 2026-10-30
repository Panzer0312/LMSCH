import { useMemo } from 'react'
import { Toggle } from './ui'
import type { Profile, SceneItem, SceneOrder, SceneSpec } from '../types'

const orderLabel: Record<SceneOrder, string> = {
  sequential: 'Sequential',
  shuffle: 'Shuffle (no repeat)',
  weighted: 'Weighted random',
}

// Fixed seed so the example stays stable while editing; the hub uses its own picker.
function mulberry32(seed: number) {
  let a = seed
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function previewPicks(items: SceneItem[], order: SceneOrder, n: number): number[] {
  if (items.length === 0) return []
  const rnd = mulberry32(1031)
  const out: number[] = []
  if (order === 'sequential') {
    for (let i = 0; i < n; i++) out.push(i % items.length)
    return out
  }
  if (order === 'shuffle') {
    let bag: number[] = []
    while (out.length < n) {
      if (bag.length === 0) {
        bag = items.map((_, i) => i)
        for (let i = bag.length - 1; i > 0; i--) {
          const j = Math.floor(rnd() * (i + 1))
          ;[bag[i], bag[j]] = [bag[j], bag[i]]
        }
        // Avoid repeating the last pick across bag boundaries.
        if (bag.length > 1 && bag[0] === out[out.length - 1]) [bag[0], bag[1]] = [bag[1], bag[0]]
      }
      out.push(bag.shift() as number)
    }
    return out
  }
  let last = -1
  for (let k = 0; k < n; k++) {
    const pool = items.map((it, i) => ({ i, w: Math.max(0, it.weight) })).filter((c) => c.w > 0 && (items.length < 2 || c.i !== last))
    if (pool.length === 0) break
    const total = pool.reduce((s, c) => s + c.w, 0)
    let r = rnd() * total
    let pick = pool[pool.length - 1].i
    for (const c of pool) {
      r -= c.w
      if (r < 0) { pick = c.i; break }
    }
    out.push(pick)
    last = pick
  }
  return out
}

export function SceneEditor({ value, onChange, profiles }: {
  value: SceneSpec
  onChange: (s: SceneSpec) => void
  profiles: Profile[]
}) {
  const choices = profiles.filter((p) => p.type !== 'scene')
  const nameOf = (pid: number) => profiles.find((p) => p.id === pid)?.name ?? `#${pid}`
  const set = (patch: Partial<SceneSpec>) => onChange({ ...value, ...patch })
  const setItems = (items: SceneItem[]) => set({ items })
  const nextFree = choices.find((p) => !value.items.some((x) => x.profile_id === p.id))

  const move = (i: number, d: -1 | 1) => {
    const j = i + d
    if (j < 0 || j >= value.items.length) return
    const items = [...value.items]
    ;[items[i], items[j]] = [items[j], items[i]]
    setItems(items)
  }

  const picks = useMemo(() => previewPicks(value.items, value.order, 10), [value.items, value.order])

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <span className="label">Items</span>
        {value.items.length === 0 && <p className="text-sm text-mute">No items yet.</p>}
        {value.items.map((it, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-panel-2 p-2">
            <select className="input min-w-0 flex-1" value={it.profile_id}
              onChange={(e) => setItems(value.items.map((x, k) => (k === i ? { ...x, profile_id: Number(e.target.value) } : x)))}>
              {!choices.some((p) => p.id === it.profile_id) && <option value={it.profile_id}>{nameOf(it.profile_id)}</option>}
              {choices.filter((p) => p.id === it.profile_id || !value.items.some((x, k) => k !== i && x.profile_id === p.id))
                .map((p) => <option key={p.id} value={p.id}>{p.name} ({p.type})</option>)}
            </select>
            {value.order === 'weighted' && (
              <input type="number" min={0} step={1} className="input w-20" aria-label="Weight" title="Weight" value={it.weight}
                onChange={(e) => setItems(value.items.map((x, k) => (k === i ? { ...x, weight: Math.max(0, Number(e.target.value) || 0) } : x)))} />
            )}
            <div className="flex gap-1">
              <button type="button" className="btn" disabled={i === 0} aria-label="Move up" onClick={() => move(i, -1)}>↑</button>
              <button type="button" className="btn" disabled={i === value.items.length - 1} aria-label="Move down" onClick={() => move(i, 1)}>↓</button>
              <button type="button" className="btn btn-danger" aria-label="Remove" onClick={() => setItems(value.items.filter((_, k) => k !== i))}>✕</button>
            </div>
          </div>
        ))}
        <button type="button" className="btn" disabled={!nextFree}
          title={choices.length > 0 && !nextFree ? 'all profiles added' : undefined}
          onClick={() => { if (nextFree) setItems([...value.items, { profile_id: nextFree.id, weight: 1 }]) }}>Add item</button>
        {choices.length === 0 && <p className="text-sm text-mute">Create a light or video profile first.</p>}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <span className="label">Order</span>
          <select className="input" value={value.order} onChange={(e) => set({ order: e.target.value as SceneOrder })}>
            {(Object.keys(orderLabel) as SceneOrder[]).map((o) => <option key={o} value={o}>{orderLabel[o]}</option>)}
          </select>
        </div>
        <div>
          <span className="label">Idle profile</span>
          <select className="input" value={value.idle_profile_id ?? ''}
            onChange={(e) => set({ idle_profile_id: e.target.value === '' ? null : Number(e.target.value) })}>
            <option value="">None</option>
            {value.idle_profile_id != null && !choices.some((p) => p.id === value.idle_profile_id) && (
              <option value={value.idle_profile_id}>{nameOf(value.idle_profile_id)}</option>
            )}
            {choices.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div>
          <span className="label">Light hold (s)</span>
          <input type="number" min={0} step={1} className="input" value={value.light_hold_s}
            onChange={(e) => set({ light_hold_s: Math.max(0, Number(e.target.value) || 0) })} />
        </div>
        <div className="flex items-end pb-2">
          <Toggle checked={value.chain} label="Play back to back, no trigger" onChange={(v) => set({ chain: v })} />
        </div>
      </div>

      {picks.length > 0 && (
        <div className="space-y-1">
          <span className="label">Next 10 picks (example)</span>
          <ol className="list-inside list-decimal text-sm text-mute">
            {picks.map((i, k) => <li key={k} className="truncate">{nameOf(value.items[i].profile_id)}</li>)}
          </ol>
        </div>
      )}
    </div>
  )
}
