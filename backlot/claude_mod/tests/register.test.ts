import { expect, mock, test } from 'claude-code/testing'

const SOCK = '/tmp/x.sock'
type Req = { route: string; body: any; headers: Record<string, string> }
type Probe = { messages: number; registers?: number }

// Common stubs. Every test calls `const clock = mock.clock(on)` itself first, then wire(on, …).
// `sessionId` lets a test move the engine's session id (the stale→fresh switch after /resume);
// `probe` counts `$.session.messages()` and `$.tool.register` calls; `fetchDenied` makes every request fail;
// `helloGate` holds every /hello answer, `inboxGate` the first /inbox answer, until the test settles them.
// (A test may register each event once, so variations go through these options.)
function wire(on: any, opts: { env?: boolean; inbox?: any[]; sessionId?: () => string; probe?: Probe; fetchDenied?: boolean; helloGate?: Promise<void>; inboxGate?: Promise<void>; registerThrows?: boolean; runReply?: unknown; runGate?: Promise<void>; runThrows?: boolean } = {}) {
  const reqs: Req[] = []
  const inbox = [...(opts.inbox ?? [])]
  on('env.get', ($: any, e: any) => ({ value: opts.env === false ? undefined : e.name === 'FRONTLOT_LIVE_SOCKET' ? SOCK : 'tok' }))
  let inboxGate = opts.inboxGate
  on('http.fetch', async ($: any, e: any) => {
    if (opts.fetchDenied) return { deny: 'socket gone' }
    const route = new URL(e.url).pathname
    const body = e.init?.body ? JSON.parse(e.init.body) : null
    reqs.push({ route, body, headers: e.init?.headers ?? {} })
    const ok = (v: unknown) => ({ value: { status: 200, ok: true, headers: {}, text: JSON.stringify(v) } })
    if (route === '/report') return ok({ acceptedThrough: body.events.at(-1).seq })
    if (route === '/inbox') {
      if (inboxGate) { const g = inboxGate; inboxGate = undefined; await g }
      return ok(inbox.shift() ?? {})
    }
    if (route === '/run' || route === '/run-check') {
      if (opts.runGate) await opts.runGate
      if (opts.runThrows) return { deny: 'socket gone' }
      return ok('runReply' in opts ? opts.runReply : { requestId: 'r-1', status: 'running', plain: 'Look' })
    }
    if (route === '/hello' && opts.helloGate) await opts.helloGate
    return ok({})
  })
  on('session.id', () => ({ value: opts.sessionId ? opts.sessionId() : 'sess-1' }))
  on('session.cwd', () => ({ value: '/work' }))
  on('session.version', () => ({ value: { version: '2.1.288' } }))
  on('session.messages', () => {
    if (opts.probe) opts.probe.messages++
    return { value: [{ role: 'assistant', text: 'Earlier.', toolUses: [] }] }
  })
  on('tool.register', () => {
    if (opts.registerThrows) throw new Error('tool registry unavailable')
    if (opts.probe) opts.probe.registers = (opts.probe.registers ?? 0) + 1
    return { value: { tool: 'mcp__frontlot-live__frontlot_run' } }
  })
  on('session.start', () => ({ cwd: '/work' }))
  on('classic.SessionStart', () => ({}))
  on('turn.start', ($: any, e: any) => ({ turnId: e.turnId }))
  return reqs
}
const events = (reqs: Req[]) => reqs.filter((r) => r.route === '/report').flatMap((r) => r.body.events)
const hellos = (reqs: Req[]) => reqs.filter((r) => r.route === '/hello').map((r) => r.body)
const start = ($: any) => $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
// A fresh launch as the engine runs it: classic.SessionStart (startup) first, then session.start.
const launch = async ($: any, sessionId = 'sess-1') => { await $.classic.SessionStart({ source: 'startup', session_id: sessionId }); await start($) }
// Lets the queue's sender and the inbox loop (both scheduled on the mocked clock) run to quiescence.
const settle = async (clock: any) => { for (let i = 0; i < 10; i++) { await clock.advance(0); await clock.settle() } }

test('inert without the env vars: no requests, no tool registered', async ($, on) => {
  mock.clock(on)
  const probe: Probe = { messages: 0 }
  const reqs = wire(on, { env: false, probe })
  await launch($)
  expect(reqs).toEqual([])
  expect(probe.registers ?? 0).toBe(0)
})

test('a fresh launch opens an epoch with empty history, never reads messages, and sends the token header', async ($, on) => {
  const clock = mock.clock(on)
  const probe = { messages: 0 }
  const reqs = wire(on, { probe })
  await launch($)
  await settle(clock)
  const hello = reqs.find((r) => r.route === '/hello')!
  expect(hello.headers['x-frontlot-token']).toBe('tok')
  expect(hello.body).toMatchObject({ sessionId: 'sess-1', source: 'launch', cwd: '/work', claudeVersion: '2.1.288' })
  expect(hello.body.history).toEqual([])
  expect(hello.body.historyUnavailable).toBeUndefined()
  expect(probe.messages).toBe(0)
})

test('/clear opens a new epoch with empty history and never reads messages', async ($, on) => {
  const clock = mock.clock(on)
  const probe = { messages: 0 }
  const reqs = wire(on, { probe })
  await launch($)
  await $.classic.SessionStart({ source: 'clear', session_id: 'sess-2' })
  await settle(clock)
  const hs = hellos(reqs)
  expect(hs.map((h) => h.source)).toEqual(['launch', 'clear'])
  expect(hs[0].epoch).not.toBe(hs[1].epoch)
  expect(hs[1].sessionId).toBe('sess-2')
  expect(hs[1].history).toEqual([])
  expect(probe.messages).toBe(0)
})

test('a second session.start in the same session (hot reload) opens an epoch without history', async ($, on) => {
  const clock = mock.clock(on)
  const reqs = wire(on)
  await start($)
  await settle(clock)
  await start($)   // $.state.opened survives, as it does across a real hot reload
  await settle(clock)
  const hs = hellos(reqs)
  expect(hs.map((h) => h.source)).toEqual(['launch', 'reload'])
  expect(hs[1].history).toBeUndefined()
})

test('/resume waits for the engine to switch sessions, then sends the snapshot; no report goes before that hello', async ($, on) => {
  const clock = mock.clock(on)
  const probe = { messages: 0 }
  on('tool.call', () => ({ result: 'file text' }))
  // The engine still answers the old session for a while after classic.SessionStart.
  const reqs = wire(on, { probe, sessionId: () => (clock.now() >= 750 ? 'sess-2' : 'sess-1') })
  await launch($)
  await settle(clock)
  await $.classic.SessionStart({ source: 'resume', session_id: 'sess-2' })
  await $.tool.call({ tool: 'Read', tool_use_id: 'tu9', file_path: '/work/notes.md' } as any)
  await settle(clock)
  expect(hellos(reqs).length).toBe(1)
  expect(probe.messages).toBe(0)
  const before = reqs.length
  expect(reqs.filter((r) => r.route === '/report').every((r) => r.body.epoch === hellos(reqs)[0].epoch)).toBe(true)

  await clock.advance(250); await settle(clock)
  await clock.advance(250); await settle(clock)
  expect(hellos(reqs).length).toBe(1)
  expect(probe.messages).toBe(0)

  await clock.advance(250); await settle(clock)   // t = 750: the id now matches
  const hs = hellos(reqs)
  expect(hs.length).toBe(2)
  expect(hs[1]).toMatchObject({ source: 'resume', sessionId: 'sess-2', history: [{ role: 'assistant', text: 'Earlier.', toolUses: [] }] })
  expect(hs[1].historyUnavailable).toBeUndefined()
  expect(probe.messages).toBe(1)

  // The resumed epoch's reports exist, and every one of them was posted after its hello.
  const helloAt = reqs.findIndex((r) => r.route === '/hello' && r.body.epoch === hs[1].epoch)
  const reportIdx = reqs.map((r, i) => (r.route === '/report' && r.body.epoch === hs[1].epoch ? i : -1)).filter((i) => i >= 0)
  expect(reportIdx.length).toBeGreaterThan(0)
  expect(reportIdx.every((i) => i > helloAt)).toBe(true)
  expect(helloAt).toBeGreaterThanOrEqual(before)
  expect(events(reqs)).toContainEqual(expect.objectContaining({ kind: 'tool', toolUseId: 'tu9', phase: 'start' }))
})

test('/branch gives up after 5 s when the session never switches: historyUnavailable, no history', async ($, on) => {
  const clock = mock.clock(on)
  const probe = { messages: 0 }
  const reqs = wire(on, { probe, sessionId: () => 'sess-1' })
  await launch($)
  await settle(clock)
  await $.classic.SessionStart({ source: 'fork', session_id: 'sess-3' })
  for (let t = 0; t < 4750; t += 250) { await clock.advance(250); await settle(clock) }
  expect(hellos(reqs).length).toBe(1)
  await clock.advance(250); await settle(clock)   // t = 5000
  const hs = hellos(reqs)
  expect(hs.length).toBe(2)
  expect(hs[1]).toMatchObject({ source: 'fork', sessionId: 'sess-3', historyUnavailable: true })
  expect(hs[1].history).toBeUndefined()
  expect(probe.messages).toBe(0)
  await clock.advance(5000); await settle(clock)
  expect(hellos(reqs).length).toBe(2)
})

test('a --resume launch whose first main turn starts before the switch sends historyUnavailable at once', async ($, on) => {
  const clock = mock.clock(on)
  const probe = { messages: 0 }
  const reqs = wire(on, { probe, sessionId: () => 'sess-old' })
  await $.classic.SessionStart({ source: 'resume', session_id: 'sess-new' })
  await start($)
  await settle(clock)
  expect(hellos(reqs)).toEqual([])
  expect(reqs.filter((r) => r.route === '/report')).toEqual([])
  await $.turn.start({ text: 'go on', turnId: 't1' })
  await settle(clock)
  const hs = hellos(reqs)
  expect(hs.length).toBe(1)
  expect(hs[0]).toMatchObject({ source: 'launch', sessionId: 'sess-new', historyUnavailable: true })
  expect(hs[0].history).toBeUndefined()
  expect(probe.messages).toBe(0)
  const helloAt = reqs.findIndex((r) => r.route === '/hello')
  const firstReport = reqs.findIndex((r) => r.route === '/report')
  expect(firstReport).toBeGreaterThan(helloAt)
  expect(events(reqs)).toContainEqual(expect.objectContaining({ kind: 'turn', phase: 'start', turnId: 't1' }))
  await clock.advance(6000); await settle(clock)
  expect(hellos(reqs).length).toBe(1)
})

test('ordinary tool calls report start and end and pass through', async ($, on) => {
  const clock = mock.clock(on)
  on('tool.call', () => ({ result: 'file text' }))
  const reqs = wire(on)
  await launch($)
  const r = await $.tool.call({ tool: 'Read', tool_use_id: 'tu2', file_path: '/work/wayfinder/MAP.md' } as any)
  expect(r).toMatchObject({ result: 'file text' })
  await settle(clock)
  const tools = events(reqs).filter((e: any) => e.kind === 'tool')
  expect(tools.map((e: any) => e.phase)).toEqual(['start', 'end'])
  expect(tools[0].summary).toBe('wayfinder/MAP.md')
})

test('permission request reports a wait and passes through unchanged', async ($, on) => {
  const clock = mock.clock(on)
  on('classic.PermissionRequest', () => ({}))
  const reqs = wire(on)
  await launch($)
  const out = await $.classic.PermissionRequest({ tool_name: 'Write', tool_input: { file_path: '/tmp/a' } } as any)
  expect(out).toEqual({})
  await settle(clock)
  expect(events(reqs)).toContainEqual(expect.objectContaining({ kind: 'waiting-for-input', reason: 'permission' }))
})

test('two queued submits are sent in order, each once, and a redelivery is not resubmitted', async ($, on) => {
  const clock = mock.clock(on)
  const submitted: string[] = []
  on('prompt.submit', ($: any, e: any) => { submitted.push(e.text); return { text: e.text } })
  const reqs = wire(on, { inbox: [{ id: 'a1', epoch: 'E', submit: 'first' }, { id: 'a2', epoch: 'E', submit: 'second' }, { id: 'a1', epoch: 'E', submit: 'first' }] })
  await launch($)
  await settle(clock)
  expect(submitted).toEqual(['first', 'second'])
  const acks = reqs.filter((r) => r.route === '/inbox-ack').map((r) => r.body)
  // Each submit is acked queued at once, then submitted; the redelivered a1 is acked, never resubmitted.
  expect(acks.filter((a) => a.id === 'a2')).toEqual([{ id: 'a2', status: 'queued' }, { id: 'a2', status: 'submitted' }])
  expect(acks.filter((a) => a.id === 'a1').slice(0, 2)).toEqual([{ id: 'a1', status: 'queued' }, { id: 'a1', status: 'submitted' }])
  expect(acks.filter((a) => a.id === 'a1').length).toBe(3)
})

test('a submit held by a working Claude is acked queued at once, and submitted only when it resolves (I1)', async ($, on) => {
  const clock = mock.clock(on)
  let release!: () => void
  const gate = new Promise<void>((r) => { release = r })
  on('prompt.submit', async ($: any, e: any) => { await gate; return { text: e.text } })
  const reqs = wire(on, { inbox: [{ id: 'q1', epoch: 'E', submit: 'thanks' }] })
  await launch($)
  await settle(clock)
  const acks = () => reqs.filter((r) => r.route === '/inbox-ack').map((r) => r.body)
  expect(acks()).toEqual([{ id: 'q1', status: 'queued' }])
  // The inbox loop keeps polling while the submit is held.
  const polls = reqs.filter((r) => r.route === '/inbox').length
  await clock.advance(1000); await settle(clock)
  expect(reqs.filter((r) => r.route === '/inbox').length).toBeGreaterThan(polls)
  release()
  await settle(clock)
  expect(acks()).toEqual([{ id: 'q1', status: 'queued' }, { id: 'q1', status: 'submitted' }])
})

test('Stop works while a typed reply is held: abort is called with the running turn and acked (I2)', async ($, on) => {
  const clock = mock.clock(on)
  let release!: () => void
  const gate = new Promise<void>((r) => { release = r })
  const aborted: string[] = []
  on('prompt.submit', async ($: any, e: any) => { await gate; return { text: e.text } })
  on('turn.abort', ($: any, e: any) => { aborted.push(e.turnId); return { value: undefined } })
  const reqs = wire(on, { inbox: [{ id: 'q1', epoch: 'E', submit: 'thanks' }, { id: 's1', epoch: 'E', stop: { turnId: 't1' } }] })
  await launch($)
  await $.turn.start({ text: 'count', turnId: 't1' })
  await settle(clock)
  expect(aborted).toEqual(['t1'])
  expect(reqs.filter((r) => r.route === '/inbox-ack').map((r) => r.body)).toEqual([{ id: 'q1', status: 'queued' }, { id: 's1', status: 'submitted' }])
  release()
  await settle(clock)
})

test('a dropped submit is acknowledged as rejected with the reason', async ($, on) => {
  const clock = mock.clock(on)
  on('prompt.submit', () => ({ drop: 'blocked by a hook' }))
  const reqs = wire(on, { inbox: [{ id: 'd1', epoch: 'E', submit: 'hello' }] })
  await launch($)
  await settle(clock)
  expect(reqs.filter((r) => r.route === '/inbox-ack').map((r) => r.body)).toEqual([{ id: 'd1', status: 'queued' }, { id: 'd1', status: 'rejected', reason: 'blocked by a hook' }])
})

test('a stale Stop is rejected and stops nothing', async ($, on) => {
  const clock = mock.clock(on)
  let aborted = false
  on('turn.abort', () => { aborted = true; return { value: undefined } })
  const reqs = wire(on, { inbox: [{ id: 's1', epoch: 'E', stop: { turnId: 'old' } }] })
  await launch($)
  await settle(clock)
  expect(aborted).toBe(false)
  expect(reqs.filter((r) => r.route === '/inbox-ack').map((r) => r.body)).toEqual([{ id: 's1', status: 'rejected', reason: 'turn already ended' }])
})

test('hooks never wait for /hello: session.start, classic.SessionStart and turn.start resolve while it hangs', async ($, on) => {
  const clock = mock.clock(on)
  let answerHello!: () => void
  const helloGate = new Promise<void>((r) => { answerHello = r })
  const reqs = wire(on, { helloGate, sessionId: () => 'sess-1' })
  const resolved: string[] = []
  await $.classic.SessionStart({ source: 'startup', session_id: 'sess-1' })
  void start($).then(() => resolved.push('session.start'))
  await settle(clock)
  void $.classic.SessionStart({ source: 'clear', session_id: 'sess-2' }).then(() => resolved.push('clear'))
  await settle(clock)
  void $.classic.SessionStart({ source: 'resume', session_id: 'sess-3' }).then(() => resolved.push('resume'))
  await settle(clock)
  void $.turn.start({ text: 'go', turnId: 't1' }).then(() => resolved.push('turn.start'))   // gives up the snapshot: a third /hello
  await settle(clock)
  expect(resolved).toEqual(['session.start', 'clear', 'resume', 'turn.start'])
  expect(hellos(reqs).map((h) => h.source)).toEqual(['launch', 'clear', 'resume'])
  expect(reqs.filter((r) => r.route === '/report')).toEqual([])
  answerHello()
  await settle(clock)
  expect(events(reqs)).toContainEqual(expect.objectContaining({ kind: 'turn', phase: 'start', turnId: 't1' }))
})

test('an inbox action that arrives after the epoch changed is rejected, not submitted', async ($, on) => {
  const clock = mock.clock(on)
  const submitted: string[] = []
  on('prompt.submit', ($: any, e: any) => { submitted.push(e.text); return { text: e.text } })
  let answerInbox!: () => void
  const inboxGate = new Promise<void>((r) => { answerInbox = r })
  const reqs = wire(on, { inboxGate, inbox: [{ id: 'x1', epoch: 'E', submit: 'meant for the old session' }] })
  await launch($)
  await settle(clock)   // the first long-poll is in flight
  await $.classic.SessionStart({ source: 'clear', session_id: 'sess-2' })
  await settle(clock)
  answerInbox()
  await settle(clock)
  expect(submitted).toEqual([])
  expect(reqs.filter((r) => r.route === '/inbox-ack').map((r) => r.body)).toEqual([{ id: 'x1', status: 'rejected', reason: 'epoch ended' }])
  const hs = hellos(reqs)
  expect(reqs.filter((r) => r.route === '/inbox').map((r) => r.body.epoch).at(-1)).toBe(hs[1].epoch)
})


test('frontlot_run posts /run keyed by the tool-use id and never reaches the engine', async ($, on) => {
  const clock = mock.clock(on)
  const reqs = wire(on)
  let reachedEngine = false
  on('tool.call', () => { reachedEngine = true; return { result: 'engine' } })
  await launch($)
  await settle(clock)
  const r: any = await $.tool.call({ tool: 'mcp__frontlot-live__frontlot_run', tool_use_id: 'tu-7', op: 'look', params: { entity: 'hero-a' } } as any)
  const body = reqs.find((q) => q.route === '/run')!.body
  expect(body).toMatchObject({ key: 'tu-7', op: 'look', params: { entity: 'hero-a' }, turnId: '', turnSeq: 0 })
  expect(body.epoch).toBe(hellos(reqs)[0].epoch)   // bound to the epoch the add-on announced
  expect(reachedEngine).toBe(false)
  expect(r.result).toBe("Front Lot is running it. You'll get a message when it finishes. Look")
})

test('a failed tool registration never keeps the epoch closed: hello and inbox still happen', async ($, on) => {
  const clock = mock.clock(on)
  const reqs = wire(on, { registerThrows: true })
  await launch($)
  await settle(clock)
  expect(hellos(reqs).length).toBe(1)
  expect(reqs.some((r) => r.route === '/inbox')).toBe(true)
})

test("a Stop for the current turn aborts it and names the newest turn in the ack", async ($, on) => {
  const clock = mock.clock(on)
  const aborted: string[] = []
  on('turn.abort', ($: any, e: any) => { aborted.push(e.turnId); return { value: undefined } })
  const reqs = wire(on, { inbox: [{ id: 's1', epoch: 'E', stop: { turnId: '*' } }] })
  await launch($)
  await $.turn.start({ text: 'go', turnId: 't7' })
  await settle(clock)
  expect(aborted).toEqual(['t7'])
  expect(reqs.filter((r) => r.route === '/inbox-ack').map((r) => r.body)).toEqual([{ id: 's1', status: 'submitted', reason: 'stopped-through:1' }])
})

test('a turn that ended before the Stop arrived is still retired by the ack', async ($, on) => {
  const clock = mock.clock(on)
  let aborted = false
  on('turn.abort', () => { aborted = true; return { value: undefined } })
  on('turn.complete', () => ({ text: 'done' }))
  const reqs = wire(on, { inbox: [{ id: 's1', epoch: 'E', stop: { turnId: '*' } }] })
  await launch($)
  await $.turn.start({ text: 'go', turnId: 't7' })
  await $.turn.complete({ turnId: 't7', answer: 'done', durationMs: 1, isAborted: false, reason: 'answer' } as any)
  await settle(clock)
  expect(aborted).toBe(false)
  expect(reqs.filter((r) => r.route === '/inbox-ack').map((r) => r.body)).toEqual([{ id: 's1', status: 'rejected', reason: 'stopped-through:1' }])
})

test('a redelivered Stop replays its first final ack', async ($, on) => {
  const clock = mock.clock(on)
  const aborted: string[] = []
  on('turn.abort', ($: any, e: any) => { aborted.push(e.turnId); return { value: undefined } })
  const stop = { id: 's1', epoch: 'E', stop: { turnId: '*' } }
  const reqs = wire(on, { inbox: [stop, stop] })               // the broker never saw the first ack
  await launch($)
  await $.turn.start({ text: 'go', turnId: 't7' })
  await settle(clock)
  expect(aborted).toEqual(['t7'])                              // aborted once
  expect(reqs.filter((r) => r.route === '/inbox-ack').map((r) => r.body))
    .toEqual([{ id: 's1', status: 'submitted', reason: 'stopped-through:1' }, { id: 's1', status: 'submitted', reason: 'stopped-through:1' }])
})

const RUN = 'mcp__frontlot-live__frontlot_run'

test('empty and null broker replies read as uncertain with the check instruction', async ($, on) => {
  const clock = mock.clock(on)
  wire(on, { runReply: {} })
  await launch($)
  await settle(clock)
  const r1: any = await $.tool.call({ tool: RUN, tool_use_id: 'tu-a', op: 'look' } as any)
  expect(r1.result).toMatch(/"check": "tu-a"/)
  expect(r1.result).toMatch(/do not resubmit/i)
})

test('a null broker reply is uncertain and never falls through to the engine', async ($, on) => {
  const clock = mock.clock(on)
  let reachedEngine = false
  on('tool.call', () => { reachedEngine = true; return { result: 'engine' } })
  wire(on, { runReply: null })
  await launch($)
  await settle(clock)
  const r: any = await $.tool.call({ tool: RUN, tool_use_id: 'tu-b', op: 'look' } as any)
  expect(r.result).toMatch(/"check": "tu-b"/)
  expect(reachedEngine).toBe(false)
})

test('an unknown status reads as uncertain', async ($, on) => {
  const clock = mock.clock(on)
  wire(on, { runReply: { status: 'banana', plain: 'x' } })
  await launch($)
  await settle(clock)
  const r: any = await $.tool.call({ tool: RUN, tool_use_id: 'tu-c', op: 'look' } as any)
  expect(r.result).toMatch(/do not resubmit/i)
})

test('a /run that hangs past 5 s becomes uncertain and names the key', async ($, on) => {
  const clock = mock.clock(on)
  let open!: () => void
  const runGate = new Promise<void>((res) => { open = res })
  wire(on, { runGate })
  await launch($)
  await settle(clock)
  const p: any = $.tool.call({ tool: RUN, tool_use_id: 'tu-d', op: 'look' } as any)
  await clock.advance(5000)
  const r: any = await p
  expect(r.result).toMatch(/"check": "tu-d"/)
  open()
})

test('a failing broker call becomes uncertain', async ($, on) => {
  const clock = mock.clock(on)
  wire(on, { runThrows: true })
  await launch($)
  await settle(clock)
  const r: any = await $.tool.call({ tool: RUN, tool_use_id: 'tu-e', op: 'look' } as any)
  expect(r.result).toMatch(/"check": "tu-e"/)
})

test('{check} goes to /run-check with the given key', async ($, on) => {
  const clock = mock.clock(on)
  const reqs = wire(on, { runReply: { status: 'done', plain: 'Look' } })
  await launch($)
  await settle(clock)
  const r: any = await $.tool.call({ tool: RUN, tool_use_id: 'tu-10', check: 'tu-9' } as any)
  expect(reqs.find((q) => q.route === '/run-check')!.body).toEqual({ key: 'tu-9' })
  expect(reqs.some((q) => q.route === '/run')).toBe(false)
  expect(r.result).toBe('It finished: Look')
})

test('a /run inside a started turn carries the real turn id and a non-zero order', async ($, on) => {
  const clock = mock.clock(on)
  const reqs = wire(on)
  await launch($)
  await $.turn.start({ text: 'go', turnId: 't9' })
  await settle(clock)
  await $.tool.call({ tool: RUN, tool_use_id: 'tu-f', op: 'look' } as any)
  expect(reqs.find((q) => q.route === '/run')!.body).toMatchObject({ turnId: 't9', turnSeq: 1 })
})

test('a run with no tool-use id is refused locally and never sent', async ($, on) => {
  const clock = mock.clock(on)
  const reqs = wire(on)
  await launch($)
  await settle(clock)
  const r: any = await $.tool.call({ tool: RUN, tool_use_id: '', op: 'look' } as any)
  expect(reqs.filter((q) => q.route === '/run').map((q) => q.body)).toEqual([])
  expect(reqs.some((q) => q.route === '/run')).toBe(false)
  expect(r.result).toMatch(/couldn't send/i)
})
