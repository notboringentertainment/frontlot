import { expect, test } from 'claude-code/testing'
import { ReportQueue } from '../hooks/queue'
import type { ReportBatch } from '../hooks/protocol'

const noSleep = async () => {}
const ev = (n: number) => ({ kind: 'delta', turnId: 't1', text: 'x'.repeat(n) }) as const

test('stamps contiguous seq and posts one batch at a time in order', async () => {
  const seen: ReportBatch[] = []
  let inFlight = 0
  let maxInFlight = 0
  const q = new ReportQueue('e1', async (b) => {
    inFlight++; maxInFlight = Math.max(maxInFlight, inFlight)
    seen.push(b); await Promise.resolve(); inFlight--
    return { acceptedThrough: b.events.at(-1)!.seq }
  }, noSleep, () => 1)
  for (let i = 0; i < 250; i++) q.enqueue(ev(1))
  await q.flushed()
  expect(maxInFlight).toBe(1)
  expect(seen.map((b) => b.fromSeq)).toEqual([0, 100, 200])
  expect(seen.flatMap((b) => b.events.map((e) => e.seq))).toEqual([...Array(250).keys()])
  expect(q.acceptedThrough).toBe(249)
})

test('splits batches by byte size', async () => {
  const sizes: number[] = []
  const q = new ReportQueue('e1', async (b) => { sizes.push(b.events.length); return { acceptedThrough: b.events.at(-1)!.seq } }, noSleep, () => 1,
    { maxEvents: 5000, maxBytes: 8 << 20, batchEvents: 100, batchBytes: 1000 })
  for (let i = 0; i < 4; i++) q.enqueue(ev(400))
  await q.flushed()
  expect(sizes).toEqual([2, 2])
})

test('retries the same batch twice, then fails and reports channel-error', async () => {
  const posts: ReportBatch[] = []
  const q = new ReportQueue('e1', async (b) => { posts.push(b); throw new Error('down') }, noSleep, () => 1)
  q.enqueue(ev(1))
  await q.flushed()
  expect(posts.slice(0, 3).map((b) => b.fromSeq)).toEqual([0, 0, 0])
  expect(posts.at(-1)!.events[0]!.kind).toBe('channel-error')
  expect(q.state).toBe('failed')
  q.enqueue(ev(1))
  await q.flushed()
  expect(posts.length).toBe(4)
})

test('overflow drops the queue and reports queue-overflow', async () => {
  const posts: ReportBatch[] = []
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  const q = new ReportQueue('e1', async (b) => { posts.push(b); await gate; return { acceptedThrough: b.events.at(-1)!.seq } }, noSleep, () => 1,
    { maxEvents: 3, maxBytes: 8 << 20, batchEvents: 1, batchBytes: 1 << 20 })
  for (let i = 0; i < 5; i++) q.enqueue(ev(1))
  expect(q.state).toBe('overflow')
  release()
  await q.flushed()
  expect(posts.some((b) => b.events[0]!.kind === 'queue-overflow')).toBe(true)
})
