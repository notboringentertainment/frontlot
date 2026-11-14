import { expect, test } from 'claude-code/testing'
import { runCall, runResultText } from '../hooks/run'

test('uncertain tells Claude to check by key, never resubmit', () => {
  const t = runResultText({ status: 'uncertain', plain: 'no answer' }, 'tu-1')
  expect(t).toMatch(/check/i)
  expect(t).toMatch(/tu-1/)
  expect(t).toMatch(/do not resubmit/i)
})

test('not-received allows one new submission; unknown outcome is never retried', () => {
  expect(runResultText({ status: 'not-received', plain: '' }, 'k')).toMatch(/once more/i)
  expect(runResultText({ status: 'unknown-outcome', plain: 'Make 3' }, 'k')).toMatch(/do not retry/i)
})

test('waiting tells Claude Ben sees a card', () => {
  expect(runResultText({ requestId: 'r-1', status: 'waiting-for-ben', plain: 'Make 3' }, 'k')).toMatch(/spend card/i)
})

test('the key is the tool-use id; runs carry epoch and turn; a check uses the key it was given', () => {
  expect(runCall({ tool_use_id: 'tu-9', op: 'look', params: { entity: 'hero-a' } }, 'ep-1', 'turn-3', 3))
    .toEqual({ route: '/run', key: 'tu-9', body: { key: 'tu-9', op: 'look', params: { entity: 'hero-a' }, epoch: 'ep-1', turnId: 'turn-3', turnSeq: 3 } })
  expect(runCall({ tool_use_id: 'tu-10', check: 'tu-9' }, 'ep-1', '', 0))
    .toEqual({ route: '/run-check', key: 'tu-9', body: { key: 'tu-9' } })
})
