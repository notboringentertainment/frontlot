import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'
import { ReportQueue } from './queue'
import { Waits } from './waits'
import { TOKEN_HEADER, type Hello, type HelloSource, type HistoryMessage, type InboxAck, type InboxAction, type InboxReply, type LiveEvent, type ReportBatch, type RowBlock, type RowToolRef } from './protocol'

// `claude plugin validate` rules (spike R5): one literal on('<event>', …) per event, and every helper
// that receives `$` is a top-level `function` declaration in this file.

const opened = atom({ plugin: 'frontlot-live', key: 'opened' } as const, false)

const SNAPSHOT_STEP_MS = 250
const SNAPSHOT_GIVE_UP_MS = 5000
const POLL_RETRY_MS = 1000
const POLL_WAIT_FOR_HELLO_MS = 250
// After an empty long-poll reply (Story-drive's 25 s timeout) the next poll waits this long, so a
// reply that comes back empty at once can never spin the loop. After an action it re-polls at once.
const POLL_IDLE_MS = 100
const PING_MS = 5000

// Spike check e: the only notification types seen were `permission_prompt` (already covered by
// classic.PermissionRequest) and `idle_prompt` (ordinary idle). None means "waiting for input" on
// its own, so the set is empty; the classic.Notification hook stays so a future type can be added.
const WAITING_TYPES = new Set<string>([])

type Timer = { cancel(): void }
type HistoryMode = 'empty' | 'none' | 'deferred' | 'unavailable'

// One reporting epoch. Its queue's posts wait for `helloSent` (R4.4), so no /report precedes its /hello.
interface EpochCtx {
  id: string
  source: HelloSource
  sessionId: string | undefined
  helloSent: Promise<void>
  release: () => void
  isHelloStarted: boolean
  isHelloDone: boolean
  isDeferred: boolean
  waitedMs: number
  timer: Timer | null
}

let socketPath: string | undefined
let token: string | undefined
let started = false
let lastSource: string | undefined
let expectedSessionId: string | undefined
let current: EpochCtx | null = null
let queue: ReportQueue | null = null
let mainTurnId: string | null = null
const running = new Set<string>()
const waits = new Waits()
const handled = new Set<string>()
// Submits handed to Claude and not yet settled; a redelivery of one is acked `queued` again, never re-run.
const inProgress = new Set<string>()
let pollTimer: Timer | null = null
let pingTimer: Timer | null = null

const rand = (): string => Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('')
const isActive = (): boolean => !!socketPath && !!token
const report = (ev: LiveEvent): void => { queue?.enqueue(ev) }

const summary = (tool: string, e: Record<string, unknown>): string => {
  const path = typeof e.file_path === 'string' ? e.file_path : typeof e.path === 'string' ? e.path : null
  if (path) return path.replace(/^.*?\/(wayfinder\/)/, '$1')
  if (tool === 'Bash' && typeof e.command === 'string') return e.command.split('\n')[0]!.slice(0, 120)
  if (typeof e.pattern === 'string') return e.pattern
  return tool
}

const toHistory = (rows: readonly any[]): HistoryMessage[] => rows.map((m) => ({
  role: m.role,
  text: m.text,
  toolUses: (m.toolUses ?? []).map((t: any) => ({ tool_use_id: t.tool_use_id, tool: t.tool, input: t.input })),
}))

async function call($: any, route: string, body: unknown): Promise<any> {
  const r = await $.http.fetch(`http://frontlot${route}`, {
    method: 'POST', socketPath, headers: { 'content-type': 'application/json', [TOKEN_HEADER]: token! }, body: JSON.stringify(body),
  })
  if (!r || !r.ok) throw new Error(`${route} ${r?.status}`)
  return r.text ? JSON.parse(r.text) : {}
}

async function postReport($: any, ctx: EpochCtx, batch: ReportBatch): Promise<{ acceptedThrough: number }> {
  await ctx.helloSent
  return call($, '/report', batch)
}

// Opens a new epoch. History never comes from $.session.messages() inside a hook (R4.2):
// `empty` for a fresh launch or /clear, `none` for a hot reload, `deferred` for resume/fork
// (a snapshot once the engine has switched sessions), `unavailable` when the source is unknown.
async function openEpoch($: any, source: HelloSource, mode: HistoryMode): Promise<void> {
  if (current) { current.timer?.cancel(); current.timer = null; current.isHelloStarted = true }   // a superseded epoch never says hello
  let release!: () => void
  const helloSent = new Promise<void>((r) => { release = r })
  const ctx: EpochCtx = {
    id: rand(), source, sessionId: expectedSessionId, helloSent, release,
    isHelloStarted: false, isHelloDone: false, isDeferred: mode === 'deferred', waitedMs: 0, timer: null,
  }
  current = ctx
  queue = new ReportQueue(ctx.id, (b) => postReport($, ctx, b), (ms) => $.clock.sleep(ms), () => Date.now())
  handled.clear()
  if (mode === 'deferred') { ctx.timer = $.clock.after(SNAPSHOT_STEP_MS, () => void snapshotTick($, ctx)); return }
  await sendHello($, ctx, mode === 'empty' ? { history: [] } : mode === 'unavailable' ? { historyUnavailable: true } : {})
}

async function sendHello($: any, ctx: EpochCtx, extra: Pick<Hello, 'history' | 'historyUnavailable'>): Promise<void> {
  if (ctx.isHelloStarted) return
  ctx.isHelloStarted = true
  ctx.timer?.cancel()
  ctx.timer = null
  try {
    const hello: Hello = {
      epoch: ctx.id, source: ctx.source, cwd: await $.session.cwd(),
      sessionId: ctx.sessionId ?? (await $.session.id()),
      claudeVersion: (await $.session.version()).version,
      ...extra,
    }
    await call($, '/hello', hello)
  } catch { /* Story-drive's hello timeout handles this */ }
  finally { ctx.isHelloDone = true; ctx.release() }
}

// Deferred snapshot (R4.2): every 250 ms, once $.session.id() is the new session's id, send its history.
async function snapshotTick($: any, ctx: EpochCtx): Promise<void> {
  ctx.timer = null
  if (ctx.isHelloStarted) return
  ctx.waitedMs += SNAPSHOT_STEP_MS
  let id: string | undefined
  try { id = await $.session.id() } catch { id = undefined }
  if (ctx.isHelloStarted) return
  if (id !== undefined && id === ctx.sessionId) {
    let history: HistoryMessage[] | undefined
    try { history = toHistory(await $.session.messages()) } catch { history = undefined }
    await sendHello($, ctx, history ? { history } : { historyUnavailable: true })
    return
  }
  if (ctx.waitedMs >= SNAPSHOT_GIVE_UP_MS) { await sendHello($, ctx, { historyUnavailable: true }); return }
  ctx.timer = $.clock.after(SNAPSHOT_STEP_MS, () => void snapshotTick($, ctx))
}

async function giveUpSnapshot($: any): Promise<void> {
  if (current && current.isDeferred && !current.isHelloStarted) await sendHello($, current, { historyUnavailable: true })
}

function ping($: any): void {
  if (current?.isHelloDone && queue?.state === 'open') void call($, '/ping', { epoch: current.id, enqueuedThrough: queue.enqueuedThrough }).catch(() => {})
}

function schedulePoll($: any, ms: number): void {
  pollTimer?.cancel()
  pollTimer = $.clock.after(ms, () => void poll($))
}

function sendAck($: any, ack: InboxAck): Promise<void> {
  return call($, '/inbox-ack', ack).then(() => {}, () => {})   // Story-drive redelivers; the id is remembered
}

// $.prompt.submit resolves only once Claude is idle (I1), so a submit never blocks the inbox loop (I2):
// it is handed over, acked `queued` at once, and its final ack is sent when it settles (after the queued one).
async function startSubmit($: any, id: string, text: string): Promise<void> {
  let pending: Promise<any>
  try { pending = Promise.resolve($.prompt.submit({ text, asUser: true })) }
  catch (err) { await sendAck($, { id, status: 'rejected', reason: String(err) }); return }
  inProgress.add(id)
  const queued = sendAck($, { id, status: 'queued' })
  void pending
    .then((r: any): InboxAck => (r?.drop ? { id, status: 'rejected', reason: r.drop } : { id, status: 'submitted' }), (err: unknown): InboxAck => ({ id, status: 'rejected', reason: String(err) }))
    .then(async (ack) => { await queued; await sendAck($, ack); inProgress.delete(id) })
  await queued
}

async function act($: any, action: InboxAction): Promise<void> {
  if (inProgress.has(action.id)) return sendAck($, { id: action.id, status: 'queued' })
  if (handled.has(action.id)) return sendAck($, { id: action.id, status: 'submitted' })
  handled.add(action.id)
  if ('submit' in action) return startSubmit($, action.id, action.submit)
  if (action.stop.turnId !== mainTurnId) return sendAck($, { id: action.id, status: 'rejected', reason: 'turn already ended' })
  let ack: InboxAck
  try { await $.turn.abort({ turnId: action.stop.turnId }); ack = { id: action.id, status: 'submitted' } }
  catch { ack = { id: action.id, status: 'rejected', reason: 'turn already ended' } }
  await sendAck($, ack)
}

// The composer inbox: a long-poll Story-drive answers per epoch. Waits for the epoch's hello first.
async function poll($: any): Promise<void> {
  pollTimer = null
  const ctx = current
  if (!ctx || !queue || queue.state !== 'open') return
  if (!ctx.isHelloDone) { schedulePoll($, POLL_WAIT_FOR_HELLO_MS); return }
  let action: InboxReply = {}
  try { action = await call($, '/inbox', { epoch: ctx.id }) } catch { schedulePoll($, POLL_RETRY_MS); return }
  if (!('id' in action)) { schedulePoll($, POLL_IDLE_MS); return }
  // The epoch changed while the long-poll waited (/clear, /resume): the action was meant for the old session.
  if (current === ctx) await act($, action as InboxAction)
  else await sendAck($, { id: action.id as string, status: 'rejected', reason: 'epoch ended' })
  schedulePoll($, 0)
}

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    started = true
    socketPath = (await $.env.get('FRONTLOT_LIVE_SOCKET')) || undefined
    token = (await $.env.get('FRONTLOT_LIVE_TOKEN')) || undefined
    if (!isActive()) return next(e)
    const isReload = await read($, opened)   // $.state survives a hot reload; module variables do not
    // A fresh or --resume launch is sent as `launch` (R4.5); its history follows the classic source.
    const mode: HistoryMode = isReload ? 'none'
      : lastSource === 'startup' ? 'empty'
        : lastSource === 'resume' || lastSource === 'fork' ? 'deferred'
          : 'unavailable'
    // Never await the hello in a hook: $.http.fetch has no timeout and the hook budget stops while it
    // waits, so a Story-drive that never answers would block the session. openEpoch sets `current` and
    // `queue` before its first await, and the helloSent gate keeps /report behind the hello.
    void openEpoch($, isReload ? 'reload' : 'launch', mode).catch(() => {})
    await update($, opened, () => true)
    pingTimer?.cancel()
    pingTimer = $.clock.every(PING_MS, () => ping($))
    schedulePoll($, 0)
    return next(e)
  }).catch(($, e, next) => next(e))

  // Every source, no matcher (R4.1). Before session.start has run it only records; session.start opens the epoch.
  on('classic.SessionStart', async ($, e, next) => {
    lastSource = e.source
    expectedSessionId = e.session_id
    if (started && isActive()) {
      if (e.source === 'clear') void openEpoch($, 'clear', 'empty').catch(() => {})
      else if (e.source === 'resume' || e.source === 'fork') void openEpoch($, e.source, 'deferred').catch(() => {})
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('session.end', async ($, e, next) => { report({ kind: 'session-end', reason: e.reason }); return next(e) }).catch(($, e, next) => next(e))

  on('session.append', async ($, e, next) => {
    const r = await next(e)
    if (queue) {
      const blocks = (e.message.content as any[]).flatMap((b): (RowBlock | RowToolRef)[] =>
        b.type === 'text' ? [{ type: 'text' as const, text: String(b.text) }]
          : b.type === 'tool_use' ? [{ type: 'tool_use' as const, toolUseId: String(b.id), name: String(b.name) }]
            : b.type === 'tool_result' ? [{ type: 'tool_result' as const, toolUseId: String(b.tool_use_id), isError: !!b.is_error, text: (typeof b.content === 'string' ? b.content : JSON.stringify(b.content ?? '')).slice(0, 2048) }]
              : [])
      report({ kind: 'row', uuid: e.uuid, door: e.door, type: e.message.type, role: e.message.role, origin: e.origin as any, isMeta: e.message.isMeta, agentId: e.agentId, blocks })
    }
    return r
  }).catch(($, e, next) => next(e))

  on('turn.step', async function* ($, e, next) {
    const stream = next(e)
    for await (const c of stream) {
      const chunk = c as any
      if (queue && e.agentId === undefined && chunk.kind === 'text' && chunk.text) report({ kind: 'delta', turnId: e.turnId, text: chunk.text })
      yield c
    }
    return await stream.result   // read to its end, so this is what beneath returned, unchanged
  }).catch(async function* ($, e, next) { return yield* next(e) })

  // A main-loop turn starting before a deferred snapshot matched ends the wait (R4.2).
  on('turn.start', async ($, e, next) => {
    mainTurnId = e.turnId
    report({ kind: 'turn', phase: 'start', turnId: e.turnId })
    void giveUpSnapshot($).catch(() => {})   // never await the hello in a hook (see session.start)
    return next(e)
  }).catch(($, e, next) => next(e))

  on('turn.complete', async ($, e, next) => {
    report({ kind: 'turn', phase: 'complete', turnId: e.turnId, agentId: e.agentId, isAborted: e.isAborted, durationMs: e.durationMs })
    if (e.agentId === undefined) {
      if (mainTurnId === e.turnId) mainTurnId = null
      for (const id of waits.turnCompleted()) report({ kind: 'input-done', requestId: id })
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('tool.call', async ($, e, next) => {
    const id = String((e as any).tool_use_id ?? '')
    if (!queue) return next(e)
    running.add(id)
    report({ kind: 'tool', toolUseId: id, tool: e.tool, agentId: e.agentId, phase: 'start', summary: summary(e.tool, e as any) })
    if (e.tool === 'AskUserQuestion') report({ kind: 'waiting-for-input', requestId: id, reason: 'question', detail: 'Claude asked a multiple-choice question' })
    try {
      const r: any = await next(e)
      report({ kind: 'tool', toolUseId: id, tool: e.tool, agentId: e.agentId, phase: 'end', summary: summary(e.tool, e as any), isError: !!(r.isError || r.deny) })
      return r
    } finally {
      running.delete(id)
      if (e.tool === 'AskUserQuestion') report({ kind: 'input-done', requestId: id })
      for (const w of waits.toolEnded(id)) report({ kind: 'input-done', requestId: w })
    }
  }).catch(($, e, next) => next(e))

  on('classic.PermissionRequest', async ($, e, next) => {
    if (queue) report({ kind: 'waiting-for-input', requestId: waits.open('permission', e.tool_name, [...running]), reason: 'permission', detail: `Claude wants to use ${e.tool_name}` })
    return next(e)
  }).catch(($, e, next) => next(e))

  on('classic.Elicitation', async ($, e, next) => {
    if (queue) { const x = e as any; report({ kind: 'waiting-for-input', requestId: waits.open('elicitation', x.mcp_server_name, [...running], x.elicitation_id), reason: 'elicitation', detail: `${x.mcp_server_name} is asking for input` }) }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('classic.ElicitationResult', async ($, e, next) => {
    const x = e as any
    if (queue && x.elicitation_id && waits.explicitDone(x.elicitation_id)) report({ kind: 'input-done', requestId: x.elicitation_id })
    return next(e)
  }).catch(($, e, next) => next(e))

  on('classic.Notification', async ($, e, next) => {
    const x = e as any
    if (queue && WAITING_TYPES.has(x.notification_type)) report({ kind: 'waiting-for-input', requestId: waits.open('notification', x.message, [...running]), reason: 'notification', detail: x.message })
    return next(e)
  }).catch(($, e, next) => next(e))
}
