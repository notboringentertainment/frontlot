// Conversation model for the Front Lot column: a pure reducer ported from Story-drive's live model
// (src/live/model.ts), plus the Front Lot cases (spend cards, run lines, session state, cursor).
// Story-drive messages: {type: "hello"|"events"|"lost"|"recovered"|"session-ended"|"unavailable"|...}.

export const RECOVERED_NOTICE = "The live view is back. You can switch to it.";
export const RELOAD_NOTICE = "Reconnected. The earlier conversation is in the terminal.";
export const LOST_NOTICE = "The live view dropped. The conversation continues in the terminal.";
export const UNAVAILABLE_NOTICE = "The live view isn't available, so the conversation is in the terminal.";
export const ENDED_NOTICE = "This conversation has ended.";

let nextId = 0;
const id = () => `f${nextId++}`;
const READS = new Set(["Read", "Grep", "Glob"]);
const WRITES = new Set(["Edit", "Write", "MultiEdit", "NotebookEdit"]);
const MARK = "mcp__story-drive-live__mark";

export const initialModel = () => ({
  epoch: null,
  feed: [],
  provisional: "",
  working: null,
  questions: [],
  checkState: "none",
  draftLabel: null,
  openWaits: new Map(),
  view: "raw",
  rawReason: "before-hello",
  canReturn: false,
  notice: null,
  ended: false,
  toolGroups: {},
  reloadPending: false,
  down: null,
  cards: {},
  session: "starting",
  cursor: 0,
});

export function toolLabel(tools) {
  const reads = tools.filter((t) => READS.has(t)).length;
  const writes = tools.filter((t) => WRITES.has(t)).length;
  const other = [...new Set(tools.filter((t) => !READS.has(t) && !WRITES.has(t)))];
  const parts = [];
  if (reads) parts.push(`Read ${reads} file${reads === 1 ? "" : "s"}`);
  if (writes) parts.push(`Updated ${writes} file${writes === 1 ? "" : "s"}`);
  for (const o of other) parts.push(`Used ${o}`);
  return parts.join(" · ");
}

function historySummary(tool, input) {
  const path = typeof input.file_path === "string" ? input.file_path : typeof input.path === "string" ? input.path : null;
  if (path) return path;
  if (tool === "Bash" && typeof input.command === "string") return input.command.split("\n")[0].slice(0, 120);
  if (typeof input.pattern === "string") return input.pattern;
  return tool;
}

// Marks (question / draft / checklist) are Story-drive's ticket UI; Front Lot keeps them harmless.
function applyMark(w, ev) {
  switch (ev.markKind) {
    case "question": {
      if (typeof ev.text !== "string" || !ev.text.trim()) return;
      const n = w.questions.length + 1;
      const item = { id: id(), kind: "question", n, text: ev.text };
      w.feed.push(item);
      w.questions = [...w.questions, { n, text: ev.text, itemId: item.id }];
      return;
    }
    case "draft": {
      if (typeof ev.text !== "string" || !ev.text.trim()) return;
      w.feed.push({ id: id(), kind: "draft", label: ev.text });
      w.draftLabel = ev.text;
      return;
    }
    case "check-start": {
      const items = Array.isArray(ev.items) ? ev.items.filter((x) => typeof x === "string") : [];
      w.feed.push({ id: id(), kind: "checklist", items: items.map((item) => ({ item, state: "pending" })) });
      w.checkState = "running";
      return;
    }
    case "check-done": {
      const results = Array.isArray(ev.results) ? ev.results : [];
      let idx = -1;
      for (let i = w.feed.length - 1; i >= 0; i--) if (w.feed[i].kind === "checklist") { idx = i; break; }
      const prev = idx >= 0 ? w.feed[idx] : { id: id(), kind: "checklist", items: [] };
      const items = prev.items.map((x) => ({ ...x }));
      for (const r of results) {
        const next = { item: r.item, state: r.ok ? "ok" : "conflict", ...(r.note ? { note: r.note } : {}) };
        const at = items.findIndex((x) => x.item === r.item);
        if (at >= 0) items[at] = next;
        else items.push(next);
      }
      const updated = { ...prev, items };
      if (idx >= 0) w.feed[idx] = updated;
      else w.feed.push(updated);
      w.checkState = "done";
      return;
    }
  }
}

function addTool(w, toolUseId, tool, summary, agentId) {
  let idx = -1;
  if (agentId !== undefined) {
    idx = w.feed.findIndex((f) => f.kind === "tools" && f.helper && w.toolGroups[f.id]?.agentId === agentId);
  } else {
    const last = w.feed.at(-1);
    if (last?.kind === "tools" && !last.helper) idx = w.feed.length - 1;
  }
  if (idx < 0) {
    const item = { id: id(), kind: "tools", label: "", files: [], open: false, ...(agentId !== undefined ? { helper: true } : {}) };
    w.feed.push(item);
    w.toolGroups = { ...w.toolGroups, [item.id]: { tools: [], ids: [], ...(agentId !== undefined ? { agentId } : {}) } };
    idx = w.feed.length - 1;
  }
  const item = w.feed[idx];
  const group = w.toolGroups[item.id];
  if (toolUseId && group.ids.includes(toolUseId)) return; // count each tool call once
  const next = { ...group, tools: [...group.tools, tool], ids: [...group.ids, toolUseId] };
  w.toolGroups = { ...w.toolGroups, [item.id]: next };
  w.feed[idx] = {
    ...item,
    label: item.helper ? "Claude used a helper" : toolLabel(next.tools),
    files: summary ? [...item.files, summary] : item.files,
  };
}

const textOf = (blocks) =>
  (blocks || [])
    .filter((b) => b && b.type === "text" && typeof b.text === "string")
    .map((b) => b.text)
    .join("\n\n");

function isPerson(origin) {
  if (!origin) return false;
  if (origin.kind === "composer") return true;
  return origin.kind === "plugin" && origin.name === "frontlot-live" && origin.asUser === true;
}

function holding(w) {
  if (w.down) return w.down;
  if (w.openWaits.size > 0) return "waiting";
  if (w.reloadPending) return "reload";
  return null;
}

function goLost(w) {
  w.down = "lost";
  w.view = "raw";
  w.rawReason = "lost";
  w.canReturn = false;
  w.notice = LOST_NOTICE;
}

function adaptHistory(w, history) {
  for (const msg of history) {
    if (msg.role === "user") {
      if (msg.text?.trim()) w.feed.push({ id: id(), kind: "you", text: msg.text, earlier: true });
      continue;
    }
    if (msg.text?.trim()) w.feed.push({ id: id(), kind: "prose", markdown: msg.text });
    for (const t of msg.toolUses ?? []) {
      const input = t.input ?? {};
      if (t.tool === MARK) {
        applyMark(w, { kind: "mark", toolUseId: t.tool_use_id, markKind: input.kind, text: input.text, items: input.items, results: input.results });
      } else {
        addTool(w, t.tool_use_id, t.tool, historySummary(t.tool, input));
      }
    }
  }
}

function applyEvent(w, ev) {
  switch (ev.kind) {
    case "row": {
      if (ev.agentId !== undefined) return;
      if (ev.role === "assistant" || ev.type === "assistant") {
        w.provisional = ""; // a committed main-loop assistant row clears live writing
        const text = textOf(ev.blocks);
        if (text.trim()) w.feed.push({ id: id(), kind: "prose", markdown: text });
        return;
      }
      if ((ev.role === "user" || ev.type === "user") && !ev.isMeta && isPerson(ev.origin)) {
        const text = textOf(ev.blocks);
        if (text.trim()) w.feed.push({ id: id(), kind: "you", text });
      }
      return;
    }
    case "delta":
      w.provisional += ev.text;
      return;
    case "turn": {
      if (ev.agentId !== undefined) return; // helpers never change working state
      if (ev.phase === "start") {
        w.working = { turnId: ev.turnId };
        return;
      }
      w.working = null;
      w.provisional = "";
      if (ev.isAborted) w.feed.push({ id: id(), kind: "interrupted" });
      if (w.reloadPending) {
        w.reloadPending = false; // a main-loop turn.complete is the known idle point
        if (w.view === "raw" && w.rawReason === "reload") w.canReturn = holding(w) === null;
      }
      return;
    }
    case "tool":
      if (ev.phase === "start") addTool(w, ev.toolUseId, ev.tool, ev.summary, ev.agentId);
      return;
    case "mark":
      applyMark(w, ev);
      return;
    case "waiting-for-input": {
      const waits = new Map(w.openWaits);
      waits.set(ev.requestId, ev.detail);
      w.openWaits = waits;
      if (w.down) return; // lost/unavailable is the stronger reason
      w.view = "raw";
      w.rawReason = "waiting";
      w.canReturn = false;
      w.notice = ev.detail;
      return;
    }
    case "input-done": {
      if (!w.openWaits.has(ev.requestId)) return;
      const waits = new Map(w.openWaits);
      waits.delete(ev.requestId);
      w.openWaits = waits;
      if (w.down) return;
      if (waits.size > 0) {
        w.notice = [...waits.values()].at(-1);
        return;
      }
      if (w.reloadPending) {
        w.rawReason = "reload";
        w.notice = RELOAD_NOTICE;
        w.canReturn = false;
        return;
      }
      w.canReturn = true;
      w.notice = null;
      return;
    }
    case "channel-error":
    case "queue-overflow":
      goLost(w);
      return;
    case "session-end":
      w.working = null;
      w.provisional = "";
      return;
  }
}

export function reduce(m, msg) {
  switch (msg.type) {
    case "hello": {
      const h = msg.hello;
      const reload = h.source === "reload" || h.historyUnavailable === true;
      const w = { ...initialModel(), epoch: h.epoch, view: "live", rawReason: null };
      if (reload) {
        w.view = "raw";
        w.rawReason = "reload";
        w.canReturn = false;
        w.notice = RELOAD_NOTICE;
        w.reloadPending = true;
      }
      if (h.history) adaptHistory(w, h.history);
      return w;
    }
    case "events": {
      if (msg.epoch !== m.epoch) return m;
      const w = { ...m, feed: m.feed.slice() };
      for (const ev of msg.events) applyEvent(w, ev);
      return w;
    }
    case "lost": {
      if (msg.epoch !== null && msg.epoch !== m.epoch) return m;
      const w = { ...m };
      goLost(w);
      if (msg.reason === "no-hello") { w.down = "unavailable"; w.rawReason = "unavailable"; w.notice = UNAVAILABLE_NOTICE; }
      return w;
    }
    case "recovered": {
      if (msg.epoch !== m.epoch || m.down !== "lost") return m;
      const w = { ...m, down: null };
      const held = holding(w);
      if (held === "waiting") { w.rawReason = "waiting"; w.notice = [...w.openWaits.values()].at(-1); w.canReturn = false; }
      else if (held === "reload") { w.rawReason = "reload"; w.notice = RELOAD_NOTICE; w.canReturn = false; }
      else { w.canReturn = true; w.notice = RECOVERED_NOTICE; }
      return w;
    }
    case "unavailable": {
      const old = msg.reason === "old-version";
      return {
        ...m,
        down: old ? "old-version" : "unavailable",
        view: "raw",
        rawReason: old ? "old-version" : "unavailable",
        canReturn: false,
        notice: old
          ? msg.version ? `The live view needs Claude Code ${msg.version} or later.` : "The live view needs a newer Claude Code."
          : UNAVAILABLE_NOTICE,
      };
    }
    case "composer":
      return m;
    case "session-ended":
      return { ...m, working: null, provisional: "", notice: ENDED_NOTICE, ended: true };
    case "user-raw": {
      const held = holding(m);
      if (held || m.rawReason === "before-hello") return { ...m, view: "raw" };
      return { ...m, view: "raw", rawReason: "user", canReturn: true };
    }
    case "user-live": {
      if (!m.canReturn || holding(m)) return m;
      return { ...m, view: "live", rawReason: null, notice: null };
    }
  }
  return m;
}

// -- Front Lot layer: one broker event onto the model ----------------------------------------------------

export function apply(m, e, seq) {
  const w = applyKind(m, e);
  return typeof seq === "number" ? { ...w, cursor: seq } : w;
}

function cardState(m, e) {
  const card = m.cards[e.requestId];
  return card ? { ...m.cards, [e.requestId]: { ...card, state: e.state } } : m.cards;
}

function applyKind(m, e) {
  switch (e.kind) {
    case "addon-hello": {  // a reload resets the conversation view but never the cards
      const w = reduce(m, { type: "hello", hello: e.hello });
      return { ...w, cards: m.cards, session: m.session, cursor: m.cursor };
    }
    case "addon-missing": return reduce(m, { type: "lost", epoch: null, reason: "no-hello" });
    case "addon-silent": return reduce(m, { type: "lost", epoch: m.epoch, reason: "silent" });
    case "addon-back": return reduce(m, { type: "recovered", epoch: m.epoch });
    case "session-state":
      return e.state === "ended" ? { ...reduce(m, { type: "session-ended" }), session: "ended" } : { ...m, session: e.state };
    case "spend-request": return { ...m, cards: { ...m.cards, [e.requestId]: { ...e } } };
    case "spend-decided":
    case "run-started":
      return { ...m, cards: cardState(m, e) };
    case "run-finished":
      return { ...m, cards: cardState(m, e),
        feed: [...m.feed, { id: `run-${e.requestId}`, kind: "run", summary: e.summary, state: e.state, entity: e.entity }] };
    case "notice": return { ...m, notice: e.plain };
    case "snapshot": {
      let w = initialModel();
      if (e.hello) {
        w = reduce(w, { type: "hello", hello: e.hello });
        w = reduce(w, { type: "events", epoch: e.hello.epoch, events: e.rows });
      }
      return { ...w, session: e.state, cursor: e.cursor,
        cards: Object.fromEntries(e.cards.map((c) => [c.requestId, c])) };
    }
    default:  // Story-drive add-on events carry their epoch
      return reduce(m, { type: "events", epoch: e.epoch, events: [e] });
  }
}
