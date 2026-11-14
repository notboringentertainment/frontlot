// Input-waiting bookkeeping. An opaque wait (no id from the engine) records the
// tool calls running when it opened and clears once they have all ended, or on
// the main loop's turn.complete. A wait with an explicit id clears by that id
// or on turn.complete.
interface Wait { id: string; reason: string; tools: Set<string>; explicit: boolean }

let counter = 0
const opaqueId = (): string => `wait-${Date.now().toString(36)}-${(counter++).toString(36)}`

export class Waits {
  private waits = new Map<string, Wait>()

  open(reason: 'permission' | 'elicitation' | 'notification', _detail: string, runningTools: string[], explicitId?: string): string {
    const id = explicitId ?? opaqueId()
    this.waits.set(id, { id, reason, tools: new Set(runningTools), explicit: explicitId !== undefined })
    return id
  }

  toolEnded(toolUseId: string): string[] {
    const cleared: string[] = []
    for (const w of this.waits.values()) {
      if (w.explicit || !w.tools.has(toolUseId)) continue
      w.tools.delete(toolUseId)
      if (w.tools.size === 0) cleared.push(w.id)
    }
    for (const id of cleared) this.waits.delete(id)
    return cleared
  }

  // A completed main-loop turn means nothing is waiting: every wait clears, explicit ones included.
  turnCompleted(): string[] {
    const cleared = [...this.waits.keys()]
    this.waits.clear()
    return cleared
  }

  explicitDone(id: string): boolean {
    return this.waits.delete(id)
  }
}
