// Pure helpers for the frontlot_run tool (no claude-code import, so tests stay plain).
import type { RunCheck, RunReply, RunRequest } from './protocol'

export const RUN_DESCRIPTION = 'Ask Front Lot to run a pipeline step for this film. Give {op, params}. Free steps run now; paid steps show Ben a spend card and wait. If the reply says Front Lot did not confirm receipt, call again with {check: "<key>"}; never resubmit.'

const FINISHED: Record<string, string> = {
  done: 'It finished', failed: 'It failed', declined: 'Ben said Not now',
  cancelled: 'It was cancelled before it started', expired: 'The card expired unanswered',
}

export function runCall(e: Record<string, unknown>, epoch: string, turnId: string, turnSeq: number): { route: '/run' | '/run-check'; key: string; body: RunRequest | RunCheck } {
  if (typeof e.check === 'string' && e.check) return { route: '/run-check', key: e.check, body: { key: e.check } }
  const key = String(e.tool_use_id ?? '')
  const params = e.params && typeof e.params === 'object' ? (e.params as Record<string, unknown>) : {}
  return { route: '/run', key, body: { key, op: String(e.op ?? ''), params, epoch, turnId, turnSeq } }
}

export function runResultText(r: RunReply, key: string): string {
  switch (r.status) {
    case 'running': return `Front Lot is running it. You'll get a message when it finishes. ${r.plain}`
    case 'waiting-for-ben': return `Ben now sees a spend card for: ${r.plain}. Wait for his answer; you'll get a message.`
    case 'refused': return `Front Lot refused this: ${r.plain}`
    case 'uncertain': return `Front Lot didn't confirm it received this. Check with frontlot_run {"check": "${key}"} before doing anything else. Do not resubmit.`
    case 'not-received': return 'Front Lot never received that request. You may submit it once more as a new call.'
    case 'unknown-outcome': return `Front Lot can't tell whether this ran (${r.plain}). Tell Ben plainly and do not retry it.`
    default: return `${FINISHED[r.status] ?? r.status}: ${r.plain}`
  }
}
