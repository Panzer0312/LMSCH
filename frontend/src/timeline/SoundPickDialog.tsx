import { useEffect, useRef } from 'react'
import { fmtTime } from '../lib'
import type { Media } from '../types'

/** Picks the audio file for a new sound cue. */
export function SoundPickDialog({ audio, at, onPick, onClose }: {
  audio: Media[]
  at: number
  onPick: (mediaId: number) => void
  onClose: () => void
}) {
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose })
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); closeRef.current() } }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Add sound cue"
        className="card max-h-[90vh] w-full max-w-md space-y-3 overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <h2 className="text-base font-semibold">Add sound cue at {fmtTime(at)}</h2>
        {audio.length === 0 ? (
          <p className="text-sm text-mute">No audio files yet. Upload one on the Media page.</p>
        ) : (
          <ul className="space-y-1">
            {audio.map((m) => (
              <li key={m.id}>
                <button type="button" className="btn flex w-full items-center justify-between gap-2 text-left" onClick={() => onPick(m.id)}>
                  <span className="min-w-0 truncate">♪ {m.filename}</span>
                  <span className="shrink-0 font-mono text-xs text-mute">{fmtTime(m.duration_s)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex justify-end">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}
