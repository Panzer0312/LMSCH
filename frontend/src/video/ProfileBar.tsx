import type { DisplayProfile } from '../types'

export function ProfileBar({ profiles, selectedId, defaultId, dirty, busy, mediaName, mediaUsesSelected, ab, onSelect, onSave, onSaveAs, onRename, onDelete, onSetDefault, onUseForMedia, onAb }: {
  profiles: DisplayProfile[]
  selectedId: number | null
  defaultId: number | null
  dirty: boolean
  busy: boolean
  /** Filename of the media source, when one is shown. */
  mediaName: string | null
  mediaUsesSelected: boolean
  ab: 'A' | 'B'
  onSelect: (id: number | null) => void
  onSave: () => void
  onSaveAs: () => void
  onRename: () => void
  onDelete: () => void
  onSetDefault: () => void
  onUseForMedia: (on: boolean) => void
  onAb: (v: 'A' | 'B') => void
}) {
  const none = selectedId === null
  return (
    <div className="card space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <label className="label mb-0" htmlFor="dp-select">Display profile</label>
        <select id="dp-select" className="input min-w-0 flex-1 sm:max-w-xs" value={selectedId ?? ''} disabled={busy}
          onChange={(e) => onSelect(e.target.value === '' ? null : Number(e.target.value))}>
          <option value="">(none: identity)</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>{p.name}{p.id === defaultId ? ' (default)' : ''}</option>
          ))}
        </select>
        {dirty && <span className="rounded-full bg-pumpkin/20 px-2 py-0.5 text-xs font-semibold text-pumpkin">unsaved</span>}
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn btn-primary" disabled={busy || !dirty} onClick={none ? onSaveAs : onSave}>Save</button>
        <button className="btn" disabled={busy} onClick={onSaveAs}>Save as…</button>
        <button className="btn" disabled={busy || none} onClick={onRename}>Rename</button>
        <button className="btn btn-danger" disabled={busy || none} onClick={onDelete}>Delete</button>
        <button className="btn" disabled={busy || none || selectedId === defaultId} onClick={onSetDefault}
          title="Global default for everything mpv plays">
          {selectedId !== null && selectedId === defaultId ? 'Is default' : 'Set as default'}
        </button>
        {mediaName && (
          <button className="btn" disabled={busy || (none && !mediaUsesSelected)} onClick={() => onUseForMedia(!mediaUsesSelected)}
            title={mediaName}>
            {mediaUsesSelected ? 'Stop using for this video' : 'Use for this video'}
          </button>
        )}
      </div>
      <div className="flex items-center gap-2 text-sm">
        <span className="text-mute">Compare</span>
        <div className="inline-flex overflow-hidden rounded-lg border border-line" role="group" aria-label="A/B compare">
          {(['A', 'B'] as const).map((v) => (
            <button key={v} type="button" disabled={!dirty}
              className={`px-3 py-1 text-sm disabled:opacity-40 ${ab === v ? 'bg-pumpkin text-black' : 'bg-panel-2 text-ink'}`}
              onClick={() => onAb(v)}>
              {v === 'A' ? 'A saved' : 'B edited'}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
