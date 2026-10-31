import { useEffect } from 'react'
import { api } from '../api'
import { useAction } from '../lib'
import { useLive } from '../live'

/** Always visible. Latches until released, so a stray tap can't silently undo it. */
export function BlackoutButton({ big = false }: { big?: boolean }) {
  const on = useLive().show?.blackout ?? false
  // No busy-disable: a hung request must never lock out the panic button, and repeats are idempotent.
  const { error, run, setError } = useAction()
  // The failure label must not stick, or the operator stops trusting the button.
  useEffect(() => {
    if (!error) return
    const t = setTimeout(() => setError(null), 4000)
    return () => clearTimeout(t)
  }, [error, setError])
  return (
    <button
      type="button"
      title={error ?? undefined}
      onClick={() => run(() => api.show.blackout(!on))}
      className={`btn ${on ? 'animate-pulse border-white bg-white text-black' : 'btn-danger'} ${big ? 'h-20 text-lg' : ''}`}
    >
      {error ? 'FAILED – RETRY' : on ? 'RELEASE BLACKOUT' : 'BLACKOUT'}
    </button>
  )
}
