import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { api, ApiError } from '../api'

type Phase = 'checking' | 'open' | 'locked'

export function LoginGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>('checking')
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.auth.status()
      .then((s): Phase | Promise<Phase> => {
        if (!s.pin_enabled) return 'open'
        return api.settings.get().then(
          (): Phase => 'open',
          (e): Phase => (e instanceof ApiError && e.status === 401 ? 'locked' : 'open'),
        )
      })
      .catch((): Phase => 'open')
      .then((p) => {
        if (alive) setPhase(p)
      })
    return () => {
      alive = false
    }
  }, [])

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!pin || busy) return
    setBusy(true)
    setError(null)
    api.auth.login(pin).then(
      () => location.reload(),
      (err: unknown) => {
        setBusy(false)
        setPin('')
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) setError('Wrong PIN')
        else setError(err instanceof Error ? err.message : String(err))
      },
    )
  }

  if (phase === 'open') return <>{children}</>

  if (phase === 'checking') {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-bg text-sm text-mute">Loading…</div>
    )
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg p-4 text-ink">
      <form className="card w-full max-w-xs space-y-4" onSubmit={onSubmit}>
        <h1 className="text-center text-xl font-semibold">🎃 Spookhub</h1>
        <div>
          <label className="label" htmlFor="login-pin">PIN</label>
          <input id="login-pin" className="input" type="password" inputMode="numeric" autoComplete="current-password"
            autoFocus value={pin} onChange={(e) => setPin(e.target.value)} />
        </div>
        <button type="submit" className="btn btn-primary w-full" disabled={busy || !pin}>
          {busy ? 'Unlocking…' : 'Unlock'}
        </button>
        {error && <p className="text-sm text-blood">{error}</p>}
      </form>
    </div>
  )
}
