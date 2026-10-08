export type Epoch = string
export type HelloSource = 'launch' | 'reload' | 'clear' | 'resume' | 'fork'

export interface HistoryMessage {
  role: 'user' | 'assistant'
  text: string
  toolUses: { tool_use_id: string; tool: string; input: Record<string, unknown> }[]
}

export interface Hello {
  epoch: Epoch
  sessionId: string
  source: HelloSource
  cwd: string
  claudeVersion: string
  history?: HistoryMessage[]
  historyUnavailable?: true
}

export type RowOrigin =
  | { kind: 'composer' }
  | { kind: 'plugin'; name: string; asUser?: boolean }
  | { kind: 'model'; model: string }
  | { kind: 'tool'; tool: string }
  | { kind: string; [k: string]: unknown }

export interface RowBlock { type: 'text'; text: string }
export interface RowToolRef { type: 'tool_use' | 'tool_result'; toolUseId: string; name?: string; text?: string; isError?: boolean }

export type LiveEvent =
  | { kind: 'row'; uuid: string; door: string; type: string; role?: 'user' | 'assistant'; origin: RowOrigin; isMeta?: boolean; agentId?: string; blocks: (RowBlock | RowToolRef)[] }
  | { kind: 'delta'; turnId: string; text: string }
  | { kind: 'turn'; phase: 'start' | 'complete'; turnId: string; agentId?: string; isAborted?: boolean; durationMs?: number }
  | { kind: 'tool'; toolUseId: string; tool: string; agentId?: string; phase: 'start' | 'end'; summary: string; isError?: boolean }
  | { kind: 'mark'; toolUseId: string; markKind: 'question' | 'draft' | 'check-start' | 'check-done'; text?: string; items?: string[]; results?: { item: string; ok: boolean; note?: string }[] }
  | { kind: 'waiting-for-input'; requestId: string; reason: 'permission' | 'question' | 'elicitation' | 'notification'; detail: string }
  | { kind: 'input-done'; requestId: string }
  | { kind: 'session-end'; reason: string }
  | { kind: 'channel-error'; message: string }
  | { kind: 'queue-overflow' }

export type Stamped = LiveEvent & { seq: number; at: number }

export interface ReportBatch { epoch: Epoch; fromSeq: number; events: Stamped[] }
export interface ReportReply { acceptedThrough: number }
export interface Ping { epoch: Epoch; enqueuedThrough: number }

export type InboxAction =
  | { id: string; epoch: Epoch; submit: string }
  | { id: string; epoch: Epoch; stop: { turnId: string } }
export type InboxReply = InboxAction | Record<string, never>
// `queued`: the submit was handed to Claude, which takes it once idle; `submitted`/`rejected` follow it.
export interface InboxAck { id: string; status: 'queued' | 'submitted' | 'rejected'; reason?: string }

export const TOKEN_HEADER = 'x-frontlot-token'
export const MARK_TOOL = 'mcp__frontlot-live__mark'
export const PLUGIN_NAME = 'frontlot-live'
