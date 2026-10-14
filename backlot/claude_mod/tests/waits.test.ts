import { expect, test } from 'claude-code/testing'
import { Waits } from '../hooks/waits'

test('an opaque wait clears only when every tool running at open has ended', () => {
  const w = new Waits()
  const id = w.open('permission', 'Write', ['a', 'b'])
  expect(w.toolEnded('a')).toEqual([])
  expect(w.toolEnded('b')).toEqual([id])
})

test('two concurrent permission waits do not clear each other', () => {
  const w = new Waits()
  const p1 = w.open('permission', 'Write', ['a'])
  const p2 = w.open('permission', 'Bash', ['a', 'b'])
  expect(w.toolEnded('a')).toEqual([p1])
  expect(w.toolEnded('b')).toEqual([p2])
})

test('a wait with no running tools clears on turn complete', () => {
  const w = new Waits()
  const id = w.open('notification', 'waiting', [])
  expect(w.toolEnded('x')).toEqual([])
  expect(w.turnCompleted()).toEqual([id])
})

test('an explicit elicitation id clears by id', () => {
  const w = new Waits()
  const id = w.open('elicitation', 'server', [], 'el-1')
  expect(id).toBe('el-1')
  expect(w.explicitDone('el-1')).toBe(true)
  expect(w.turnCompleted()).toEqual([])
})
