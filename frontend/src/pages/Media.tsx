import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useLive } from '../live'
import type { BlackCheck, DisplayProfile, Media as MediaItem } from '../types'
import { normalize } from '../video/geometry'
import { ErrorText, Section } from '../components/ui'
import { fmtTime, useAction } from '../lib'

const ALLOWED_EXT = ['.mp4', '.m4v', '.mkv', '.mov', '.webm', '.avi', '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.opus']
const ACCEPT = [...ALLOWED_EXT, 'audio/*'].join(',')
type KindFilter = 'all' | 'video' | 'audio'
const FILTERS: { id: KindFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'video', label: 'Video' },
  { id: 'audio', label: 'Audio' },
]
const MAX_BYTES = 8 * 1024 ** 3
const HW_CODECS = ['h264', 'hevc']

function fmtSize(b: number): string {
  if (b >= 1024 ** 3) return `${(b / 1024 ** 3).toFixed(2)} GB`
  return `${(b / 1024 ** 2).toFixed(1)} MB`
}

const ASPECT_TOLERANCE = 0.02

function MediaCard({ m, profiles, defaultProfile, onChanged }: {
  m: MediaItem
  profiles: DisplayProfile[]
  defaultProfile: DisplayProfile | null
  onChanged: () => void
}) {
  const del = useAction()
  const tx = useAction()
  const dp = useAction()
  const bc = useAction()
  const [black, setBlack] = useState<BlackCheck | null>(null)
  const isAudio = m.kind === 'audio'
  const aspect = isAudio ? null : m.aspect ?? (m.width > 0 && m.height > 0 ? m.width / m.height : null)
  // Compare against what the default profile forces, else the 16:9 output.
  const refAspect = defaultProfile?.geometry.aspect ?? 16 / 9
  const ownProfile = m.display_profile_id != null ? profiles.find((p) => p.id === m.display_profile_id) ?? null : null
  const aspectOff = aspect != null && Math.abs(aspect - refAspect) > ASPECT_TOLERANCE && !ownProfile

  const createProfile = () => {
    if (aspect == null) return
    const name = window.prompt('Name for the display profile', `${m.filename.replace(/\.[^.]+$/, '')} framing`)?.trim()
    if (!name) return
    void dp.run(async () => {
      // Start from the default so keystone and mask carry over; only the aspect differs.
      const p = await api.display.createProfile(name, { ...normalize(defaultProfile?.geometry), aspect })
      await api.media.setDisplayProfile(m.id, p.id)
      onChanged()
    })
  }
  const { transcodes } = useLive()
  const inUse = m.used_by.length > 0
  const hwRisk = !isAudio && !HW_CODECS.includes(m.codec.toLowerCase()) || m.height > 1080
  const tc = m.transcode
  const tcProgress = tc && tc.state === 'running' ? (transcodes[m.id] ?? tc.progress) : null
  const running = tcProgress !== null
  const pct = running ? Math.round(Math.min(1, Math.max(0, tcProgress)) * 100) : 0
  const failed = tc?.state === 'failed'

  const startTranscode = () => {
    if (!confirm(`Transcode "${m.filename}" to H.264 1080p? This can take a while; the original is replaced when done.`)) return
    void tx.run(() => api.media.transcode(m.id))
  }

  const togglePlay = (v: HTMLVideoElement) => {
    if (v.paused) v.play().catch(() => {})
    else v.pause()
  }

  const remove = () => {
    if (!confirm(`Delete "${m.filename}"?`)) return
    void del.run(() => api.media.remove(m.id))
  }

  return (
    <div className="card flex flex-col gap-3 sm:flex-row">
      {isAudio ? (
        <div className="flex aspect-video w-full shrink-0 items-center justify-center rounded-lg bg-black text-4xl sm:w-48"
          role="img" aria-label="Audio file">
          🔊
        </div>
      ) : (
        <video
          src={api.media.fileUrl(m.id)}
          preload="metadata"
          muted
          playsInline
          onClick={(e) => togglePlay(e.currentTarget)}
          className="aspect-video w-full shrink-0 cursor-pointer rounded-lg bg-black object-contain sm:w-48"
        />
      )}
      <div className="min-w-0 flex-1 space-y-1">
        <p className="break-all font-medium">{m.filename}</p>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-mute">
          <span>{fmtTime(m.duration_s)}</span>
          {!isAudio && <span>{m.width}×{m.height}</span>}
          <span>{m.codec}</span>
          <span>{fmtSize(m.size)}</span>
          {!isAudio && (hwRisk || m.needs_transcode) && !running && (
            <span className="rounded-md border border-pumpkin/60 bg-pumpkin/15 px-1.5 py-0.5 text-xs text-pumpkin">
              may not hardware-decode
            </span>
          )}
        </div>
        {aspectOff && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-md border border-pumpkin/60 bg-pumpkin/15 px-1.5 py-0.5 text-xs text-pumpkin"
              title={`Default display expects ${refAspect.toFixed(2)}:1`}>
              aspect {aspect.toFixed(2)}:1 differs from display
            </span>
            <button className="btn px-2 py-1 text-xs" disabled={dp.busy} onClick={createProfile}>Create display profile for this video</button>
          </div>
        )}
        {isAudio && (
          <audio controls preload="none" src={api.media.fileUrl(m.id)} className="w-full max-w-md" />
        )}
        {!isAudio && ownProfile && (
          <p className="text-sm text-mute">Display profile: <Link className="text-ghost underline" to="/video">{ownProfile.name}</Link></p>
        )}
        {black && (
          <p className={`text-sm ${black.ok ? 'text-slime' : 'text-pumpkin'}`}>
            Black check: {black.message} (level {(black.black_level * 100).toFixed(1)} %, {black.sampled} frames)
          </p>
        )}
        <p className="text-sm text-mute">
          Used by: {inUse ? <span className="text-ink">{m.used_by.join(', ')}</span> : 'none'}
        </p>
        {running && (
          <div className="space-y-1">
            <div className="h-2 w-full overflow-hidden rounded-full bg-line">
              <div className="h-full bg-pumpkin transition-all" style={{ width: `${pct}%` }} />
            </div>
            <p className="text-sm text-mute">Converting to H.264 1080p… {pct} %</p>
          </div>
        )}
        {failed && (
          <p className="text-sm text-blood">Conversion failed: {tc?.error ?? 'unknown error'}</p>
        )}
        <div className="flex flex-wrap gap-2 pt-1">
          {!isAudio && (m.needs_transcode || running || failed) && (
            <button className="btn" disabled={running || tx.busy} onClick={startTranscode}>
              {running ? `Converting… ${pct} %` : failed ? 'Retry conversion' : 'Convert to H.264 1080p'}
            </button>
          )}
          {!isAudio && (
            <button className="btn" disabled={bc.busy} title="Checks the background is near black (no light spill on the scrim)"
              onClick={() => void bc.run(async () => setBlack(await api.media.blackCheck(m.id)))}>
              {bc.busy ? 'Checking…' : 'Black check'}
            </button>
          )}
          <span title={inUse ? 'In use by a profile; remove it there first' : undefined} className="inline-block">
            <button className="btn btn-danger" disabled={inUse || del.busy} onClick={remove}>
              Delete
            </button>
          </span>
        </div>
        <ErrorText error={tx.error} />
        <ErrorText error={del.error} />
        <ErrorText error={dp.error ?? bc.error} />
      </div>
    </div>
  )
}

export default function Media() {
  const { mediaRev, displayProfilesRev, settings } = useLive()
  const [displayProfiles, setDisplayProfiles] = useState<DisplayProfile[]>([])
  const loadDisplay = useCallback(() => { api.display.profiles().then(setDisplayProfiles, () => undefined) }, [])
  useEffect(loadDisplay, [loadDisplay, mediaRev, displayProfilesRev])
  const defaultId = settings?.display_profile_id ?? null
  const defaultProfile = defaultId != null ? displayProfiles.find((p) => p.id === defaultId) ?? null : null
  const [items, setItems] = useState<MediaItem[] | null>(null)
  const list = useAction()
  const [progress, setProgress] = useState<number | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [filter, setFilter] = useState<KindFilter>('all')

  const { run } = list
  const load = useCallback(async () => {
    const r = await run(() => api.media.list())
    if (r) setItems(r)
  }, [run])

  // A deleted display profile clears media.display_profile_id, so refetch on that too.
  useEffect(() => {
    void run(() => api.media.list()).then((r) => {
      if (r) setItems(r)
    })
  }, [run, mediaRev, displayProfilesRev])

  const onFile = async (file: File | undefined) => {
    if (fileRef.current) fileRef.current.value = ''
    if (!file) return
    setUploadError(null)
    const name = file.name.toLowerCase()
    if (!ALLOWED_EXT.some((ext) => name.endsWith(ext))) {
      setUploadError(`Unsupported file type. Allowed: ${ALLOWED_EXT.join(', ')}`)
      return
    }
    if (file.size > MAX_BYTES) {
      setUploadError(`File too large (${fmtSize(file.size)}); max 8 GB.`)
      return
    }
    setProgress(0)
    try {
      await api.media.upload(file, setProgress)
      await load()
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e))
    } finally {
      setProgress(null)
    }
  }

  const uploading = progress !== null
  const processing = uploading && progress >= 1

  // Leaving mid-upload aborts the XHR and loses a possibly multi-GB transfer.
  useEffect(() => {
    if (!uploading) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [uploading])
  const shown = items?.filter((m) => filter === 'all' || (m.kind ?? 'video') === filter) ?? null

  return (
    <div className="space-y-6">
      <Section
        title="Media"
        actions={
          <label className={`btn btn-primary cursor-pointer ${uploading ? 'pointer-events-none opacity-40' : ''}`}>
            Upload media
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              disabled={uploading}
              onChange={(e) => void onFile(e.target.files?.[0])}
            />
          </label>
        }
      >
        {uploading && (
          <div className="card space-y-2">
            <div className="h-2 w-full overflow-hidden rounded-full bg-line">
              <div className="h-full bg-pumpkin transition-all" style={{ width: `${Math.round(progress * 100)}%` }} />
            </div>
            <p className="text-sm text-mute">
              {processing ? 'Processing (ffprobe)…' : `Uploading… ${Math.round(progress * 100)} %`}
            </p>
          </div>
        )}
        <ErrorText error={uploadError} />
        <ErrorText error={list.error} />

        <div className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button key={f.id} aria-pressed={filter === f.id}
              className={`btn px-3 py-1 text-xs ${filter === f.id ? 'btn-primary' : ''}`}
              onClick={() => setFilter(f.id)}>
              {f.label}
            </button>
          ))}
        </div>

        {items === null || shown === null ? (
          list.error && !list.busy ? (
            <button className="btn" onClick={() => void load()}>Retry</button>
          ) : (
            <p className="text-sm text-mute">Loading…</p>
          )
        ) : items.length === 0 ? (
          <p className="text-sm text-mute">No media yet. Upload a video or audio file to get started.</p>
        ) : shown.length === 0 ? (
          <p className="text-sm text-mute">No {filter} files.</p>
        ) : (
          <div className="space-y-3">
            {shown.map((m) => (
              <MediaCard key={m.id} m={m} profiles={displayProfiles} defaultProfile={defaultProfile}
                onChanged={() => { loadDisplay(); void load() }} />
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}
