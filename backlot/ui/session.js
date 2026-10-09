// The conversation column: Claude's live view, composer, Stop, spend cards and the raw terminal.
// Two page sockets share ONE page id (the broker's controller lease is per page): /claude/live carries
// JSON events, /claude/tty carries terminal bytes.

import { CAPABILITY_TOKEN_KEY, el } from "/ui/lib.js";
import { apply, initialModel, reduce, toolLabel } from "/ui/session_model.js";

const UNAVAILABLE_TEXT = {
  missing: "Install Claude Code, then reload.",
  "signed-out": "Open Terminal and run: claude auth login",
  "old-version": "Update Claude Code, then reload.",
  sandbox: "Front Lot couldn't confirm Claude's safety sandbox, so the session stays off. Tell Claude in your own terminal: the Front Lot sandbox check failed.",
  "previous-still-running": "An earlier Claude conversation for this film is still shutting down. Wait a minute and reload; if it stays, restart the Mac.",
  "missing-film": "This film's folder can't be found. Check it still exists, then reload.",
  "start-failed": "Claude couldn't start for this film. Reload to try again; if it keeps failing, tell Claude in your own terminal.",
  "no-start": "Claude couldn't start for this film. Reload to try again; if it keeps failing, tell Claude in your own terminal.",
};
const UNAVAILABLE_DEFAULT = "Claude isn't available for this film right now. Reload to try again.";

const PLAIN_STATE = { approved: "Go. Starting…", launching: "Starting…", running: "Running…", done: "Done",
  failed: "Failed. Claude will explain.", uncertain: "Not sure it ran. Check the board before trying again.",
  declined: "Not now", cancelled: "Cancelled", expired: "Expired" };
const RUN_WORD = { done: "done", failed: "failed", uncertain: "not sure it ran" };
const SESSION_WORD = { starting: "Starting", none: "Starting", ready: "Ready", working: "Working", ended: "Ended" };

// -- Markdown subset (ported from Story-drive's markdown.ts): paragraphs, lists, bold, code. Escape-first;
// links keep only their label, so nothing from the model ever becomes an href or an attribute.
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const CONTROL = /[\u0000-\u0003]/g;

function inline(raw) {
  const codes = [];
  let s = raw.replace(CONTROL, "").replace(/`([^`]+)`/g, (_m, c) => `\u0000${codes.push(c) - 1}\u0000`);
  s = esc(s);
  s = s.replace(/\[([^\]]{1,500})\]\(([^)\s]{1,2000})\)/g, (_m, label) => label);
  s = s.replace(/\*\*((?:[^*])+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^\w*])\*([^*\s](?:[^*]*[^*\s])?)\*/g, "$1<em>$2</em>");
  return s.replace(/\u0000(\d+)\u0000/g, (_m, i) => `<code>${esc(codes[Number(i)])}</code>`);
}

export function renderMarkdown(src) {
  const lines = String(src).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      out.push(`<pre><code>${esc(body.join("\n"))}</code></pre>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) { out.push(`<h${h[1].length + 3}>${inline(h[2])}</h${h[1].length + 3}>`); i++; continue; }
    if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const re = ordered ? /^\s*\d+\.\s+(.*)$/ : /^\s*[-*]\s+(.*)$/;
      const items = [];
      while (i < lines.length && re.test(lines[i])) items.push(`<li>${inline(re.exec(lines[i++])[1])}</li>`);
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
      continue;
    }
    const para = [lines[i++]];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|```|\s*[-*]\s+|\s*\d+\.\s+)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inline(para.join("\n")).replace(/\n/g, "<br>")}</p>`);
  }
  return out.join("");
}

// -- Spend card -------------------------------------------------------------------------------------------
function spendCard(card, send) {
  const cost = card.estimate_usd != null ? `up to $${Number(card.estimate_usd).toFixed(2)}` : "cost unknown";
  const go = el("button", { class: "sign-btn", type: "button", onclick: () => send({ type: "spend-decision", requestId: card.requestId, go: true }) }, "Go");
  const no = el("button", { class: "quiet-btn", type: "button", onclick: () => send({ type: "spend-decision", requestId: card.requestId, go: false }) }, "Not now");
  const waiting = card.state === "waiting-for-ben";
  return el("article", { class: `spend-card ${card.state}` },
    el("h3", {}, card.summary), el("p", {}, cost),
    waiting ? el("div", { class: "spend-actions" }, go, no) : el("p", { class: "spend-state" }, PLAIN_STATE[card.state] || ""));
}

function newPageId() {
  try {
    if (crypto.randomUUID) return crypto.randomUUID();
  } catch { /* fall through */ }
  return `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function readToken() {
  try { return sessionStorage.getItem(CAPABILITY_TOKEN_KEY); } catch { return null; }
}

export function mountSession({ projectId, feedEl, terminalEl, composerEl, stateEl, onRunFinished }) {
  const page = newPageId();             // one id per page load, shared by the live and tty sockets
  const base = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/project/${encodeURIComponent(projectId)}/claude`;
  const noteEl = document.getElementById("session-note");
  const textEl = composerEl.querySelector("textarea");
  const sendBtn = composerEl.querySelector("#composer-send");
  const stopBtn = composerEl.querySelector("#composer-stop");
  const toggleBtn = composerEl.querySelector("#terminal-toggle");

  let model = initialModel();
  let unavailable = null;               // reason string while Claude cannot run
  let controller = true;
  let confirmNew = false;
  let showTerminal = false;             // Ben's toggle; the model can also force the terminal
  let live = null;                      // live socket
  let liveRetry = 0;
  let expectLive = false;               // the first GET said a broker is running: none/ended may be a start-up blip
  let attachTries = 0;
  let replaying = true;                 // events arriving right after connect are history, not news
  let replayTimer = null;
  let reattachTimer = null;
  let ttySocket = null;
  let ttyTimer = null;
  let ttyTries = 0;
  const openDetails = new Set();
  let paintQueued = false;

  // -- terminal (one page-lifetime xterm) ---------------------------------------------------------------
  const TerminalClass = window.Terminal;
  const FitClass = window.FitAddon && window.FitAddon.FitAddon;
  const term = TerminalClass ? new TerminalClass({
    convertEol: true, cursorBlink: true, fontFamily: "'SF Mono', ui-monospace, Menlo, monospace", fontSize: 13,
    lineHeight: 1.22, minimumContrastRatio: 7, screenReaderMode: true, scrollback: 5000,
    theme: { background: "#1d1d1c", foreground: "#efece4", cursor: "#efece4", cursorAccent: "#1d1d1c",
      selectionBackground: "rgba(239, 236, 228, .26)" },
  }) : null;
  const fit = FitClass ? new FitClass() : null;
  if (term && fit) term.loadAddon(fit);
  if (term) {
    term.open(terminalEl);
    const input = terminalEl.querySelector(".xterm-helper-textarea");
    if (input) input.setAttribute("aria-label", "Claude's terminal input");
    term.onData((data) => {
      if (ttySocket && ttySocket.readyState === WebSocket.OPEN) ttySocket.send(new TextEncoder().encode(data));
    });
    term.onResize(({ cols, rows }) => {
      if (ttySocket && ttySocket.readyState === WebSocket.OPEN) ttySocket.send(JSON.stringify({ type: "resize", cols, rows }));
    });
  }
  function fitTerminal() {
    if (!term || !fit || terminalEl.hidden || !terminalEl.clientWidth) return;
    try { fit.fit(); } catch { /* not laid out yet */ }
    if (ttySocket && ttySocket.readyState === WebSocket.OPEN) ttySocket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
  }
  if (window.ResizeObserver) new ResizeObserver(() => fitTerminal()).observe(terminalEl);

  function sessionLive() {
    return !unavailable && model.session !== "ended" && !model.ended;
  }

  function closeTty() {
    clearTimeout(ttyTimer);
    const s = ttySocket;
    ttySocket = null;
    if (s) { try { s.close(); } catch { /* already closed */ } }
  }

  function scheduleTty(ms) {
    clearTimeout(ttyTimer);
    if (!sessionLive() || ttyTries > 12) return;
    ttyTimer = setTimeout(ensureTty, ms);
  }

  function ensureTty() {
    if (!term || !sessionLive()) return;
    if (ttySocket && ttySocket.readyState <= WebSocket.OPEN) return;
    const token = readToken();
    if (!token) return;
    const socket = new WebSocket(`${base}/tty`);
    socket.binaryType = "arraybuffer";
    ttySocket = socket;
    ttyTries++;
    socket.addEventListener("open", () => {
      if (ttySocket !== socket) return;
      socket.send(JSON.stringify({ k: token }));
      socket.send(JSON.stringify({ page }));
      term.reset();                      // the broker replays the screen's tail on attach
      fitTerminal();
    });
    socket.addEventListener("message", async (event) => {
      if (ttySocket !== socket) return;
      if (event.data instanceof ArrayBuffer) { ttyTries = 0; term.write(new Uint8Array(event.data)); return; }
      if (event.data instanceof Blob) { ttyTries = 0; term.write(new Uint8Array(await event.data.arrayBuffer())); return; }
      let msg = null;
      try { msg = JSON.parse(event.data); } catch { return; }
      // A status on the terminal socket means no broker was there to attach to yet: try again shortly.
      if (msg && msg.type === "status") { ttySocket = null; try { socket.close(); } catch { /* gone */ } scheduleTty(1500); }
    });
    socket.addEventListener("close", () => {
      if (ttySocket === socket) { ttySocket = null; scheduleTty(1500); }   // a bye closes it; New conversation reopens it
    });
  }

  // -- live socket ---------------------------------------------------------------------------------------
  function send(msg) {
    if (live && live.readyState === WebSocket.OPEN) { live.send(JSON.stringify({ ...msg, page })); return true; }
    return false;
  }

  function act(msg) {
    if (!send(msg)) connectLive(() => msg);
  }

  function dispatch(event, seq) {
    const before = model;
    model = apply(model, event, seq);
    if (event.kind === "run-finished" && event.entity && !replaying && onRunFinished) {
      try { onRunFinished(event.entity); } catch { /* the board's selection must never break the column */ }
    }
    if (before.session !== model.session && model.session === "ended") closeTty();
    paint();
  }

  function markReplaying() {
    replaying = true;
    clearTimeout(replayTimer);
    replayTimer = setTimeout(() => { replaying = false; }, 600);
  }

  function scheduleAttach(ms) {
    clearTimeout(reattachTimer);
    reattachTimer = setTimeout(() => send({ type: "attach", from: model.cursor }), ms);
  }

  function onLiveMessage(msg) {
    if (msg.type === "status") {
      const idle = msg.controller === false && (msg.state === "none" || msg.state === "ended");
      if (idle && expectLive && attachTries < 8) {   // attached while the broker was still starting
        attachTries++;
        scheduleAttach(1000);
        return;
      }
      clearTimeout(reattachTimer);
      controller = msg.controller !== false;
      if (msg.state && msg.state !== "none") {
        expectLive = false;
        model = { ...model, session: msg.state, ended: msg.state === "ended" ? true : model.ended };
        if (msg.state === "ended") closeTty();
        else { ttyTries = 0; ensureTty(); }
      } else if (msg.state === "none") {
        model = { ...model, session: "starting" };
      }
      paint();
    } else if (msg.type === "event" && msg.event) {
      markReplaying();
      if (msg.event.kind === "session-state" && msg.event.state && msg.event.state !== "ended") {
        // Sessions that come back after "ended" start a new conversation view.
        if (model.ended) model = { ...model, ended: false };
      }
      dispatch(msg.event, msg.seq);
    } else if (msg.type === "unavailable") {
      unavailable = msg.reason || "unknown";
      closeTty();
      paint();
    } else if (msg.type === "bye") {
      closeTty();
      if (msg.reason === "new" && msg.page !== page) {
        clearTimeout(reattachTimer);
        model = initialModel();
        scheduleAttach(1000);                   // another window started a new conversation: follow it
      } else {
        dispatch({ kind: "session-state", state: "ended" }, null);
      }
      paint();
    }
  }

  function connectLive(first) {
    const token = readToken();
    if (!token) { unavailable = unavailable || "no-token"; paint(); return; }
    if (live && live.readyState <= WebSocket.OPEN) { live.close(); }
    const socket = new WebSocket(`${base}/live`);
    live = socket;
    socket.addEventListener("open", () => {
      if (live !== socket) return;
      liveRetry = 0;
      markReplaying();
      socket.send(JSON.stringify({ k: token }));
      socket.send(JSON.stringify({ ...first(), page }));
    });
    socket.addEventListener("message", (event) => {
      if (live !== socket) return;
      try { onLiveMessage(JSON.parse(event.data)); } catch (err) { console.error(err); }
    });
    socket.addEventListener("close", () => {
      if (live !== socket) return;
      live = null;
      paint();
      if (unavailable) return;
      liveRetry++;                                // a server restart: re-attach from our cursor, nothing replayed twice
      setTimeout(() => { if (!live) connectLive(() => ({ type: "attach", from: model.cursor })); }, Math.min(5000, 800 * liveRetry));
    });
  }

  // -- rendering -------------------------------------------------------------------------------------------
  function paint() {
    if (paintQueued) return;
    paintQueued = true;
    requestAnimationFrame(() => { paintQueued = false; render(); });
  }

  function waitingForBen() {
    return Object.values(model.cards).some((c) => c.state === "waiting-for-ben")
      || (model.view === "raw" && model.rawReason === "waiting");
  }

  function stateWord() {
    if (unavailable) return "Unavailable";
    if (model.session === "ended" || model.ended) return "Ended";
    if (waitingForBen()) return "Waiting for you";
    if (!live) return model.session === "starting" ? "Starting" : "Reconnecting";
    return SESSION_WORD[model.session] || "Starting";
  }

  function renderState() {
    const word = stateWord();
    const kids = [el("strong", { class: "state-word" }, word)];
    if (unavailable) {
      kids.push(" ", el("span", {}, UNAVAILABLE_TEXT[unavailable] || (unavailable === "no-token"
        ? "Close this window and open Front Lot again from its icon." : UNAVAILABLE_DEFAULT)));
    } else if (word === "Ended") {
      if (confirmNew) {
        kids.push(" ", el("span", {}, "Start over? The old conversation can't be picked back up afterwards."), " ",
          el("button", { class: "quiet-btn", type: "button", onclick: () => startNew() }, "Yes, start a new one"), " ",
          el("button", { class: "quiet-btn", type: "button", onclick: () => { confirmNew = false; paint(); } }, "Keep it"));
      } else {
        kids.push(" ",
          el("button", { class: "quiet-btn", type: "button", onclick: () => pickBackUp() }, "Pick back up"), " ",
          el("button", { class: "quiet-btn", type: "button", onclick: () => { confirmNew = true; paint(); } }, "New conversation"));
      }
    } else if (!controller && sessionLive()) {
      kids.push(" ", el("span", {}, "Another window is in control."), " ",
        el("button", { class: "quiet-btn", type: "button", onclick: () => send({ type: "take-control" }) }, "Take control"));
    }
    stateEl.replaceChildren(...kids);
    stateEl.dataset.state = word.toLowerCase().replace(/\s+/g, "-");

    const backToLive = model.view === "raw" && model.canReturn && model.rawReason !== "user";
    if (model.notice || backToLive) {
      noteEl.hidden = false;
      noteEl.replaceChildren(model.notice || "",
        backToLive ? [" ", el("button", { class: "quiet-btn", type: "button", onclick: () => { model = reduce(model, { type: "user-live" }); paint(); } }, "Back to live view")] : null);
    } else {
      noteEl.hidden = true;
      noteEl.textContent = "";
    }
  }

  function pickBackUp() {
    confirmNew = false;
    model = { ...model, session: "starting", ended: false, notice: null };
    unavailable = null;
    attachTries = 0;
    ttyTries = 0;
    act({ type: "resume", from: model.cursor });
    paint();
  }

  function startNew() {
    confirmNew = false;
    model = initialModel();
    unavailable = null;
    attachTries = 0;
    ttyTries = 0;
    closeTty();                                 // the old terminal's bye closes it; reopen on the new session's status
    act({ type: "new", from: 0 });
    paint();
  }

  function feedItem(f) {
    switch (f.kind) {
      case "prose": {
        const box = el("div", { class: "msg claude" });
        box.innerHTML = renderMarkdown(f.markdown);
        return box;
      }
      case "you": return el("div", { class: `msg you${f.earlier ? " earlier" : ""}` }, f.text);
      case "tools": {
        const d = el("details", { class: "tool-step" }, el("summary", {}, el("span", { class: "mark", "aria-hidden": "true" }), f.label || "Claude is working"));
        if (openDetails.has(f.id)) d.open = true;
        d.addEventListener("toggle", () => { if (d.open) openDetails.add(f.id); else openDetails.delete(f.id); });
        if (f.files.length) d.append(el("ul", { class: "tool-files" }, f.files.map((x) => el("li", {}, x))));
        return d;
      }
      case "run": {
        const word = RUN_WORD[f.state] || f.state;
        return el("p", { class: `run-line ${f.state}` }, el("span", { class: "mark", "aria-hidden": "true" }), `${f.summary} — ${word}`);
      }
      case "interrupted": return el("p", { class: "run-line" }, el("span", { class: "mark", "aria-hidden": "true" }), "Stopped.");
      case "question": return el("div", { class: "msg claude" }, el("p", {}, f.text));
      case "draft": return el("p", { class: "run-line" }, el("span", { class: "mark", "aria-hidden": "true" }), f.label);
      case "checklist": return el("ul", { class: "tool-files" }, f.items.map((x) => el("li", {}, x.note ? `${x.item} — ${x.note}` : x.item)));
      default: return null;
    }
  }

  function renderFeed() {
    const near = feedEl.scrollHeight - feedEl.scrollTop - feedEl.clientHeight < 80;
    const nodes = model.feed.map(feedItem);
    if (model.provisional) {
      const box = el("div", { class: "msg claude writing" });
      box.innerHTML = renderMarkdown(model.provisional);
      nodes.push(box);
    }
    for (const card of Object.values(model.cards)) {
      if (card.state === "done" || card.state === "failed") continue;   // the run line says it
      nodes.push(spendCard(card, send));
    }
    feedEl.replaceChildren(...nodes.filter(Boolean));
    if (near) feedEl.scrollTop = feedEl.scrollHeight;
  }

  function render() {
    renderState();
    const forced = model.view === "raw";
    const terminalOn = Boolean(term) && (forced || showTerminal);
    terminalEl.hidden = !terminalOn;
    feedEl.hidden = terminalOn;
    toggleBtn.setAttribute("aria-pressed", terminalOn ? "true" : "false");
    toggleBtn.textContent = terminalOn ? "Hide terminal" : "Show terminal";
    toggleBtn.disabled = forced || !term;
    if (!terminalOn) renderFeed();
    else requestAnimationFrame(fitTerminal);
    const canType = controller && sessionLive();
    textEl.disabled = !canType;
    sendBtn.disabled = !canType;
    stopBtn.hidden = !(model.working && canType);
  }

  // -- composer ----------------------------------------------------------------------------------------------
  composerEl.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = textEl.value.trim();
    if (!text || textEl.disabled) return;
    if (send({ type: "submit", text })) textEl.value = "";
  });
  textEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); composerEl.requestSubmit(); }
  });
  stopBtn.addEventListener("click", () => send({ type: "stop" }));
  toggleBtn.addEventListener("click", () => { showTerminal = !showTerminal; paint(); if (showTerminal && term) setTimeout(() => term.focus(), 50); });

  // -- start ---------------------------------------------------------------------------------------------------
  render();
  (async () => {
    let info = { state: "none" };
    try {
      const res = await fetch(`/api/project/${encodeURIComponent(projectId)}/claude`);
      if (res.ok) info = await res.json();
    } catch { /* the socket will tell */ }
    if (info.state === "unavailable") { unavailable = info.reason || "unknown"; paint(); }
    expectLive = info.state === "running";
    const first = info.state === "none" ? () => ({ type: "start", from: 0 }) : () => ({ type: "attach", from: model.cursor });
    connectLive(first);
  })();

  return { page, get model() { return model; } };
}
