/**
 * Cue tracks (main / idle) and the stand-in playhead for light profiles' idle loop.
 * The editor edits one track at a time; saving always sends both (PUT replaces all cues).
 */
import type { Cue, CueTrack } from '../types'

export const trackOf = (c: Pick<Cue, 'track'>): CueTrack => c.track ?? 'main'

export const cuesOfTrack = <T extends Pick<Cue, 'track'>>(cues: T[], track: CueTrack): T[] =>
  cues.filter((c) => trackOf(c) === track)

/** Full cue list: `edited` (tagged with `track`) replaces that track, the other track stays. */
export const mergeTrack = (all: Cue[], track: CueTrack, edited: Cue[]): Cue[] => [
  ...edited.map((c) => ({ ...c, track })),
  ...all.filter((c) => trackOf(c) !== track),
]

/** What the editor's playhead needs from a <video>; LoopClock implements it without one. */
export interface Playable {
  currentTime: number
  readonly paused: boolean
  play(): Promise<void>
  pause(): void
}

export interface ClockEvents {
  onPlay?: (t: number) => void
  onPause?: (t: number) => void
  onSeeked?: (t: number, paused: boolean) => void
}

/** Wall-clock playhead that wraps every `length` seconds, like the hub's light idle loop. */
export class LoopClock implements Playable {
  private events: ClockEvents = {}
  private base = 0
  private startedAt: number | null = null
  private length: number
  private readonly now: () => number

  constructor(length: number, now: () => number = () => performance.now() / 1000) {
    this.length = length
    this.now = now
  }

  listen(events: ClockEvents) { this.events = events }

  get paused() { return this.startedAt == null }

  get currentTime() {
    const t = this.startedAt == null ? this.base : this.base + this.now() - this.startedAt
    return this.length > 0 ? t % this.length : t
  }

  set currentTime(t: number) {
    this.base = Math.max(0, t)
    if (this.startedAt != null) this.startedAt = this.now()
    this.events.onSeeked?.(this.currentTime, this.paused)
  }

  play() {
    if (this.startedAt == null) {
      this.startedAt = this.now()
      this.events.onPlay?.(this.currentTime)
    }
    return Promise.resolve()
  }

  pause() {
    if (this.startedAt == null) return
    this.base = this.currentTime
    this.startedAt = null
    this.events.onPause?.(this.base)
  }

  setLength(length: number) {
    if (length === this.length) return
    const t = this.currentTime
    this.length = length
    this.base = length > 0 ? t % length : t
    if (this.startedAt != null) this.startedAt = this.now()
  }
}
