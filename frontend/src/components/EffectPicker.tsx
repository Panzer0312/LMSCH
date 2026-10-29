import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

const MAX_RECENT = 8

function loadList(key: string): number[] {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return []
    const v: unknown = JSON.parse(raw)
    return Array.isArray(v) ? v.filter((n): n is number => Number.isInteger(n)) : []
  } catch {
    return []
  }
}

function saveList(key: string, list: number[]) {
  try {
    localStorage.setItem(key, JSON.stringify(list))
  } catch {
    // Storage full or blocked (private mode): favorites just won't persist.
  }
}

export function Picker({ label, items, value, onChange, storeKey, disabled, swatches }: {
  label: string
  items: string[]
  value: number | null
  onChange: (i: number | null) => void
  storeKey: string
  disabled?: boolean
  swatches?: Record<number, string>
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [favs, setFavs] = useState<number[]>([])
  const [recents, setRecents] = useState<number[]>([])
  const rootRef = useRef<HTMLDivElement>(null)
  const favKey = `spookhub.fav.${storeKey}`
  const recentKey = `spookhub.recent.${storeKey}`
  const isOpen = open && !disabled

  useEffect(() => {
    if (!isOpen) return
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && e.target instanceof Node && !rootRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [isOpen])

  const toggleOpen = () => {
    if (isOpen) { setOpen(false); return }
    // Reload on open so other pickers sharing the storeKey stay in sync.
    setFavs(loadList(favKey))
    setRecents(loadList(recentKey))
    setQuery('')
    setOpen(true)
  }

  const pick = (i: number | null) => {
    if (i != null) {
      const next = [i, ...recents.filter((r) => r !== i)].slice(0, MAX_RECENT)
      setRecents(next)
      saveList(recentKey, next)
    }
    onChange(i)
    setOpen(false)
  }

  const toggleFav = (i: number) => {
    const next = favs.includes(i) ? favs.filter((f) => f !== i) : [...favs, i]
    setFavs(next)
    saveList(favKey, next)
  }

  const q = query.trim().toLowerCase()
  const valid = (i: number) => i >= 0 && i < items.length
  const matches = (i: number) => q === '' || items[i].toLowerCase().includes(q)
  const favRows = favs.filter((i) => valid(i) && matches(i))
  const recentRows = recents.filter((i) => valid(i) && matches(i))
  const allRows = items.map((_, i) => i).filter(matches)
  const first = favRows[0] ?? recentRows[0] ?? allRows[0]

  const onInputKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      if (first != null) pick(first)
    }
  }

  const onPopKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      setOpen(false)
    }
  }

  const currentName = value != null && valid(value) ? items[value] : value != null ? `#${value}` : '—'

  const row = (i: number, section: string) => {
    const fav = favs.includes(i)
    return (
      <li key={`${section}-${i}`} className="flex items-center gap-1">
        <button type="button" aria-label={fav ? 'Remove favorite' : 'Add favorite'}
          className={`shrink-0 rounded px-2 py-1 text-base leading-none ${fav ? 'text-pumpkin' : 'text-mute hover:text-ink'}`}
          onClick={() => toggleFav(i)}>
          {fav ? '★' : '☆'}
        </button>
        <button type="button"
          className={`flex min-w-0 flex-1 items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-panel-2 ${i === value ? 'text-ghost' : 'text-ink'}`}
          onClick={() => pick(i)}>
          {swatches?.[i] && <span className="h-3 w-8 shrink-0 rounded-sm border border-line" style={{ background: swatches[i] }} />}
          <span className="truncate">{items[i]}</span>
        </button>
      </li>
    )
  }

  const section = (title: string, rows: number[], key: string) =>
    rows.length > 0 && (
      <div>
        <div className="px-2 pt-2 pb-1 text-[10px] font-medium uppercase tracking-wide text-mute">{title}</div>
        <ul>{rows.map((i) => row(i, key))}</ul>
      </div>
    )

  return (
    <div ref={rootRef} className="relative">
      <span className="label">{label}</span>
      <button type="button" className="input flex items-center gap-2 text-left disabled:opacity-40" disabled={disabled}
        aria-haspopup="listbox" aria-expanded={isOpen} onClick={toggleOpen}>
        {value != null && swatches?.[value] && <span className="h-3 w-8 shrink-0 rounded-sm border border-line" style={{ background: swatches[value] }} />}
        <span className="truncate">{currentName}</span>
      </button>
      {isOpen && (
        <div onKeyDown={onPopKey}
          className="fixed inset-x-3 top-20 z-50 flex max-h-[60vh] flex-col rounded-lg border border-line bg-panel shadow-xl sm:absolute sm:inset-x-auto sm:left-0 sm:top-full sm:mt-1 sm:w-72">
          <div className="border-b border-line p-2">
            <input autoFocus className="input" placeholder={`Search ${label.toLowerCase()}…`} value={query}
              onChange={(e) => setQuery(e.target.value)} onKeyDown={onInputKey} />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-1">
            {q === '' && (
              <button type="button"
                className={`w-full rounded px-2 py-1.5 text-left text-sm italic hover:bg-panel-2 ${value == null ? 'text-ghost' : 'text-mute'}`}
                onClick={() => pick(null)}>
                none
              </button>
            )}
            {section('Favorites', favRows, 'fav')}
            {section('Recent', recentRows, 'recent')}
            {section('All', allRows, 'all')}
            {first == null && <p className="px-2 py-3 text-sm text-mute">No match</p>}
          </div>
        </div>
      )}
    </div>
  )
}
