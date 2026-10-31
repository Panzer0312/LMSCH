import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAction } from '../lib'
import { ErrorText, Section } from './ui'

interface PresetInfo { id: string; title: string; detail: string; kind: 'show' | 'settings' }

/**
 * Ready-made "[Test]" profiles, rooms, scene, moment and settings bundles for checking a fresh
 * hub on real boards. Applying again updates in place; Remove only deletes "[Test]" items.
 */
export function TestPresets() {
  const [list, setList] = useState<PresetInfo[]>([])
  const [notes, setNotes] = useState<string[] | null>(null)
  const act = useAction()
  useEffect(() => { api.presets.list().then(setList, () => {}) }, [])
  const apply = (p: PresetInfo) => {
    setNotes(null)
    void act.run(() => api.presets.apply(p.id)).then((r) => r && setNotes(r.notes))
  }
  const remove = () => {
    if (!confirm('Delete every "[Test]" profile, room, scene and moment? Your own items stay.')) return
    setNotes(null)
    void act.run(() => api.presets.remove()).then((r) => r && setNotes(r.notes))
  }
  const group = (kind: PresetInfo['kind']) => list.filter((p) => p.kind === kind).map((p) => (
    <button key={p.id} type="button" className="btn min-h-10 flex-col items-start text-left" disabled={act.busy} onClick={() => apply(p)}>
      <span className="font-semibold">{p.title}</span>
      <span className="text-xs text-mute">{p.detail}</span>
    </button>
  ))
  return (
    <Section title="Test presets">
      <div className="card space-y-3">
        <p className="text-xs text-mute">
          Adds "[Test]" items built from the adopted boards, then activate them on Profiles or Dashboard.
          Applying again updates them. Tutorial: docs/tutorial.
        </p>
        <span className="label">Shows</span>
        <div className="grid gap-2 sm:grid-cols-2">{group('show')}</div>
        <span className="label">Settings</span>
        <div className="grid gap-2 sm:grid-cols-3">{group('settings')}</div>
        <button type="button" className="btn btn-danger" disabled={act.busy} onClick={remove}>Remove test items</button>
        {notes && <ul className="list-disc pl-5 text-sm text-slime">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
        <ErrorText error={act.error} />
      </div>
    </Section>
  )
}
