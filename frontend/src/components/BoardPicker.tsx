import type { Board, BoardGroup } from '../types'
import { brokenGroups } from '../groups'

export interface PickerValue { all: boolean; macs: string[]; groups?: number[] }

/**
 * One board picker for profiles, cues and scenes: "All boards", then groups (rooms) as chips,
 * then single boards. Groups stay references, so editing a room later updates every user.
 */
export function BoardPicker({ value, onChange, boards, groups, allLabel = 'All boards', compact = false }: {
  value: PickerValue
  onChange: (v: PickerValue) => void
  boards: Board[]
  groups: BoardGroup[]
  allLabel?: string
  compact?: boolean
}) {
  const gids = value.groups ?? []
  const set = (patch: Partial<PickerValue>) => onChange({ all: false, macs: value.macs, groups: gids, ...patch })
  const toggleGroup = (id: number) => set({ groups: gids.includes(id) ? gids.filter((g) => g !== id) : [...gids, id] })
  const toggleMac = (mac: string) => set({ macs: value.macs.includes(mac) ? value.macs.filter((m) => m !== mac) : [...value.macs, mac] })
  const broken = brokenGroups(gids, groups, boards.map((b) => b.mac))
  const chip = (on: boolean) =>
    `rounded-full border px-3 py-1 text-sm ${compact ? 'min-h-8' : 'min-h-10'} ${on ? 'border-pumpkin bg-pumpkin/20 text-ghost' : 'border-line text-mute'}`
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <button type="button" className={chip(value.all)} aria-pressed={value.all}
          onClick={() => onChange(value.all ? { all: false, macs: [], groups: [] } : { all: true, macs: [], groups: [] })}>
          {allLabel}
        </button>
        {groups.map((g) => {
          const on = !value.all && gids.includes(g.id)
          return (
            <button key={g.id} type="button" className={chip(on)} aria-pressed={on} onClick={() => toggleGroup(g.id)}
              title={`${g.macs.length} board${g.macs.length === 1 ? '' : 's'}`}>
              <span className="mr-1.5 inline-block size-2.5 rounded-full align-middle" style={{ background: g.color }} />
              {g.name} <span className="text-xs text-mute">{g.macs.length}</span>
            </button>
          )
        })}
      </div>
      {!value.all && (
        <div className="flex flex-wrap gap-2">
          {boards.map((b) => {
            const viaGroup = groups.some((g) => gids.includes(g.id) && g.macs.includes(b.mac))
            const on = value.macs.includes(b.mac)
            return (
              <button key={b.mac} type="button" className={`${chip(on)} ${viaGroup && !on ? 'border-dashed text-ghost' : ''}`}
                aria-pressed={on} onClick={() => toggleMac(b.mac)} title={viaGroup ? 'Included through a group' : b.mac}>
                {b.name || b.mac}
              </button>
            )
          })}
        </div>
      )}
      {broken.length > 0 && (
        <p className="text-xs text-pumpkin">
          {broken.length} selected group{broken.length === 1 ? ' is' : 's are'} empty or deleted: nothing lights there.
        </p>
      )}
    </div>
  )
}
