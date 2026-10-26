import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useLive } from '../live'
import type { Media as MediaItem } from '../types'
import { ErrorText, Section } from '../components/ui'
import { fmtTime, useAction } from '../lib'

const ALLOWED_EXT = ['.mp4', '.m4v', '.mkv', '.mov', '.webm', '.avi']
const MAX_BYTES = 8 * 1024 ** 3
const HW_CODECS = ['h264', 'hevc']

function fmtSize(b: number): string {
  if (b >= 1024 ** 3) return `${(b / 1024 ** 3).toFixed(2)} GB`
  return `${(b / 1024 ** 2).toFixed(1)} MB`
}

function MediaCard({ m }: { m: MediaItem }) {
  const del = useAction()
  const inUse = m.used_by.length > 0
  const hwRisk = !HW_CODECS.includes(m.codec.toLowerCase()) || m.height > 1080

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
      <video
        src={api.media.fileUrl(m.id)}
        preload="metadata"
        muted
        playsInline
        onClick={(e) => togglePlay(e.currentTarget)}
        className="aspect-video w-full shrink-0 cursor-pointer rounded-lg bg-black object-contain sm:w-48"
      />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="break-all font-medium">{m.filename}</p>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-mute">
          <span>{fmtTime(m.duration_s)}</span>
          <span>{m.width}×{m.height}</span>
          <span>{m.codec}</span>
          <span>{fmtSize(m.size)}</span>
          {hwRisk && (
            <span className="rounded-md border border-pumpkin/60 bg-pumpkin/15 px-1.5 py-0.5 text-xs text-pumpkin">
              may not hardware-decode
            </span>
          )}
        </div>
        <p className="text-sm text-mute">
          Used by: {inUse ? <span className="text-ink">{m.used_by.join(', ')}</span> : 'none'}
        </p>
        <div className="pt-1">
          <span title={inUse ? 'In use by a profile; remove it there first' : undefined} className="inline-block">
            <button className="btn btn-danger" disabled={inUse || del.busy} onClick={remove}>
              Delete
            </button>
          </span>
        </div>
        <ErrorText error={del.error} />
      </div>
    </div>
  )
}

export default function Media() {
  const { mediaRev } = useLive()
  const [items, setItems] = useState<MediaItem[] | null>(null)
  const list = useAction()
  const [progress, setProgress] = useState<number | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const { run } = list
  const load = useCallback(async () => {
    const r = await run(() => api.media.list())
    if (r) setItems(r)
  }, [run])

  useEffect(() => {
    void run(() => api.media.list()).then((r) => {
      if (r) setItems(r)
    })
  }, [run, mediaRev])

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

  return (
    <div className="space-y-6">
      <Section
        title="Media"
        actions={
          <label className={`btn btn-primary cursor-pointer ${uploading ? 'pointer-events-none opacity-40' : ''}`}>
            Upload video
            <input
              ref={fileRef}
              type="file"
              accept={ALLOWED_EXT.join(',')}
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

        {items === null ? (
          <p className="text-sm text-mute">Loading…</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-mute">No media yet. Upload a video to get started.</p>
        ) : (
          <div className="space-y-3">
            {items.map((m) => <MediaCard key={m.id} m={m} />)}
          </div>
        )}
      </Section>
    </div>
  )
}
