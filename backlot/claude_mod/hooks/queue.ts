import type { LiveEvent, ReportBatch, Stamped } from './protocol'

export interface QueueLimits { maxEvents: number; maxBytes: number; batchEvents: number; batchBytes: number }
export const DEFAULT_LIMITS: QueueLimits = { maxEvents: 5000, maxBytes: 8 * 1024 * 1024, batchEvents: 100, batchBytes: 256 * 1024 }
export type Post = (batch: ReportBatch) => Promise<{ acceptedThrough: number }>
export type Sleep = (ms: number) => Promise<void>

const RETRY_DELAYS = [250, 1000]
const size = (e: Stamped): number => JSON.stringify(e).length

export class ReportQueue {
  private pending: Stamped[] = []
  private pendingBytes = 0
  private nextSeq = 0
  private accepted = -1
  private status: 'open' | 'failed' | 'overflow' = 'open'
  private running: Promise<void> | null = null

  constructor(
    private readonly epoch: string,
    private readonly post: Post,
    private readonly sleep: Sleep,
    private readonly now: () => number,
    private readonly limits: QueueLimits = DEFAULT_LIMITS,
  ) {}

  get enqueuedThrough(): number { return this.nextSeq - 1 }
  get acceptedThrough(): number { return this.accepted }
  get state(): 'open' | 'failed' | 'overflow' { return this.status }

  enqueue(ev: LiveEvent): void {
    if (this.status !== 'open') return
    const stamped = { ...ev, seq: this.nextSeq, at: this.now() } as Stamped
    const bytes = size(stamped)
    if (this.pending.length + 1 > this.limits.maxEvents || this.pendingBytes + bytes > this.limits.maxBytes) {
      this.stop('overflow', { kind: 'queue-overflow' })
      return
    }
    this.nextSeq++
    this.pending.push(stamped)
    this.pendingBytes += bytes
    this.kick()
  }

  flushed(): Promise<void> { return this.running ?? Promise.resolve() }

  private kick(): void {
    if (this.running) return
    this.running = Promise.resolve().then(() => this.drain()).finally(() => { this.running = null; if (this.status === 'open' && this.pending.length) this.kick() })
  }

  private nextBatch(): Stamped[] {
    const out: Stamped[] = []
    let bytes = 0
    for (const e of this.pending) {
      const b = size(e)
      if (out.length && (out.length >= this.limits.batchEvents || bytes + b > this.limits.batchBytes)) break
      out.push(e); bytes += b
    }
    return out
  }

  private async drain(): Promise<void> {
    while (this.status === 'open' && this.pending.length) {
      const events = this.nextBatch()
      const batch: ReportBatch = { epoch: this.epoch, fromSeq: events[0]!.seq, events }
      let reply: { acceptedThrough: number } | null = null
      for (let attempt = 0; attempt <= RETRY_DELAYS.length && !reply; attempt++) {
        if (attempt > 0) await this.sleep(RETRY_DELAYS[attempt - 1]!)
        try { reply = await this.post(batch) } catch { reply = null }
      }
      if (!reply) { this.stop('failed', { kind: 'channel-error', message: 'report post failed after retries' }); return }
      this.accepted = Math.max(this.accepted, reply.acceptedThrough)
      while (this.pending.length && this.pending[0]!.seq <= this.accepted) this.pendingBytes -= size(this.pending.shift()!)
    }
  }

  private stop(status: 'failed' | 'overflow', last: LiveEvent): void {
    this.status = status
    this.pending = []
    this.pendingBytes = 0
    const stamped = { ...last, seq: this.nextSeq++, at: this.now() } as Stamped
    void this.post({ epoch: this.epoch, fromSeq: stamped.seq, events: [stamped] }).catch(() => {})
  }
}
