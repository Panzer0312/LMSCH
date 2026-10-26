import { api } from '../api'
import { useLive } from '../live'

/** Always visible. Latches until released, so a stray tap can't silently undo it. */
export function BlackoutButton({ big = false }: { big?: boolean }) {
  const on = useLive().show?.blackout ?? false
  return (
    <button
      type="button"
      onClick={() => api.show.blackout(!on)}
      className={`btn ${on ? 'animate-pulse border-white bg-white text-black' : 'btn-danger'} ${big ? 'h-20 text-lg' : ''}`}
    >
      {on ? 'RELEASE BLACKOUT' : 'BLACKOUT'}
    </button>
  )
}
