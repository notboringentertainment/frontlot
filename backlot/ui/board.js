// Front Lot film board (the cutting room) — renders BoardState and stays live via SSE.

import { mountSession } from "/ui/session.js";
import {
  CAPABILITY_TOKEN_KEY, el, fmtAgo, fmtClock, fmtDuration, fmtMoney,
  getJSON, mediaURL, subscribe, thumbURL, waveBars,
} from "/ui/lib.js";

const rawProjectPath = location.pathname.split("/p/")[1] || "";
const projectId = decodeURIComponent(rawProjectPath);
const encodedProjectId = encodeURIComponent(projectId);
const app = document.getElementById("app");
const modal = document.getElementById("modal");
const player = document.getElementById("player");
const terminalShell = document.getElementById("gate-terminal-shell");
const terminalMount = document.getElementById("gate-terminal");
const terminalState = document.getElementById("gate-terminal-state");
const terminalMessage = document.getElementById("gate-terminal-message");

let state = null;
let selectedStage = null;   // stage drawer open for this stage name
let activeRender = 0;
let replay = null;          // {t0, t1, t, playing} — replay mode when non-null
let firstPaint = true;
let selectedGateId = null;
let gateDetail = null;
let gateDetailState = "idle";
let gateDetailError = "";
let openGateSessions = [];
let copyStatus = "";
let refreshGeneration = 0;
let refreshController = null;
let gateSelectionGeneration = 0;
let gateDetailController = null;
let selectedEntity = null;  // trim-bin selection: "character:ace" style key
let selectedFrame = 0;      // index into the viewer's frame strip
let logShowAll = false;
const logFeed = document.getElementById("log-feed");
const tableEl = document.getElementById("table");

const TERMINAL_THEME = {
  background: "#171716",
  foreground: "#efece4",
  cursor: "#efece4",
  cursorAccent: "#171716",
  selectionBackground: "rgba(239, 236, 228, .26)",
  black: "#171716",
  red: "#d65d4e",
  green: "#74b46c",
  yellow: "#f1cb3c",
  blue: "#8fb0c9",
  magenta: "#c7a2c4",
  cyan: "#8fc4bd",
  white: "#e6e3d8",
  brightBlack: "#80857a",
  brightWhite: "#ffffff",
};

function makeGateSession() {
  const TerminalClass = window.Terminal;
  const FitClass = window.FitAddon && window.FitAddon.FitAddon;
  const terminal = TerminalClass ? new TerminalClass({
    convertEol: true,
    cursorBlink: true,
    cursorStyle: "block",
    fontFamily: "'SF Mono', ui-monospace, Menlo, monospace",
    fontSize: 13,
    lineHeight: 1.22,
    minimumContrastRatio: 7,
    screenReaderMode: true,
    scrollback: 5000,
    tabStopWidth: 4,
    theme: TERMINAL_THEME,
  }) : null;
  const fitAddon = FitClass ? new FitClass() : null;
  if (terminal && fitAddon) terminal.loadAddon(fitAddon);

  const session = {
    terminal,
    fitAddon,
    socket: null,
    requestId: null,
    inputReady: false,
    receivedRefresh: false,
    evidenceChanged: false,
    fitFrame: null,
  };

  if (terminal) {
    terminal.open(terminalMount);
    const input = terminalMount.querySelector(".xterm-helper-textarea");
    if (input) input.setAttribute("aria-label", "Signing program input");
    terminal.onData((data) => {
      if (!session.inputReady || !session.socket || session.socket.readyState !== WebSocket.OPEN) return;
      session.socket.send(new TextEncoder().encode(data));
    });
  }

  return session;
}

// One page-lifetime object owns exactly one Terminal, FitAddon, and WebSocket.
// Re-importing this module under a cache-busted URL reuses the same singleton.
// Neither render() nor the SSE refresh path replaces this object or its mount.
export const gateSession = window.__backlotGateSession || makeGateSession();
if (!window.__backlotGateSession) {
  Object.defineProperty(window, "__backlotGateSession", {
    value: gateSession,
    writable: false,
    configurable: false,
    enumerable: false,
  });
}

function setTerminalStatus(label, message, tone = "") {
  terminalState.textContent = label;
  terminalState.className = `terminal-state${tone ? ` ${tone}` : ""}`;
  terminalMessage.textContent = message;
}

function scheduleTerminalFit(sendResize = true) {
  if (!gateSession.terminal || !gateSession.fitAddon || terminalShell.hidden) return;
  cancelAnimationFrame(gateSession.fitFrame);
  gateSession.fitFrame = requestAnimationFrame(() => {
    try {
      gateSession.fitAddon.fit();
    } catch {
      return;
    }
    if (!sendResize || !gateSession.socket || gateSession.socket.readyState !== WebSocket.OPEN) return;
    const cols = Math.max(10, Math.min(500, gateSession.terminal.cols));
    const rows = Math.max(3, Math.min(300, gateSession.terminal.rows));
    gateSession.socket.send(JSON.stringify({ type: "resize", cols, rows }));
  });
}

if (window.ResizeObserver) {
  new ResizeObserver(() => scheduleTerminalFit()).observe(terminalShell);
}

function stableSerialize(value) {
  if (Array.isArray(value)) return `[${value.map(stableSerialize).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableSerialize(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function stablePacketContent(detail) {
  if (!detail || typeof detail !== "object") return stableSerialize(detail);
  const { snapshot_at: _snapshotAt, ...content } = detail;
  return stableSerialize(content);
}

// ---------------------------------------------------------------------------
// machine bar: maker, film title, stage counter, spend, status
// ---------------------------------------------------------------------------

const MAKER_MARK = '<svg class="maker-mark" viewBox="0 0 26 26" aria-hidden="true"><circle cx="13" cy="13" r="11.5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="13" cy="13" r="2.5" fill="currentColor"/><circle cx="13" cy="6.5" r="2.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="19.2" cy="15" r="2.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="6.8" cy="15" r="2.6" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';

function pendingGates(s) {
  if (!hasAuthoredGates(s)) return [];
  return (s.gates.requests || []).filter((row) => row.state === "pending");
}

function renderStatus(s) {
  const awaiting = s.stages.find((x) => x.status === "awaiting_human");
  const inProgress = s.stages.find((x) => x.status === "in_progress");
  const stalled = s.stages.find((x) => x.stalled);
  const needs = pendingGates(s).length || awaiting;
  let cls = "";
  let label = `Idle${s.last_activity ? ` · ${fmtAgo(s.last_activity)}` : ""}`;
  if (needs) { cls = "needs"; label = "Needs you"; }
  else if (stalled) { cls = "stalled"; label = "Stalled?"; }
  else if (s.live) { cls = "running"; label = "Working"; }
  else if (inProgress) { label = `Paused${s.last_activity ? ` · ${fmtAgo(s.last_activity)}` : ""}`; }
  const hint = cls === "stalled" ? "A stage says it is running, but nothing has happened for a while." : null;
  return el("span", { class: `status ${cls}`.trim(), role: "status", title: hint }, el("span", { class: "lamp" }), label);
}

function renderMeter(s) {
  let spent = null;
  let budget = null;
  if (hasAuthoredGates(s) && s.gates.cost && s.gates.cost.total_spent_usd != null) {
    spent = s.gates.cost.total_spent_usd;
  } else if (s.cost) {
    spent = s.cost.total_spent_usd ?? 0;
    if (s.cost.budget_remaining_usd != null) budget = spent + s.cost.budget_remaining_usd;
  }
  if (spent == null) return null;
  return el("div", { class: "meter", title: "Money spent on generation so far" },
    el("b", {}, fmtMoney(spent)),
    el("span", {}, budget != null ? `spent of ${fmtMoney(budget)}` : "spent"));
}

function stageSub(st) {
  if (st.status === "awaiting_human") return "waiting for your approval";
  if (st.status === "in_progress" && st.stalled) return `no activity for ${st.stalled_minutes} minutes`;
  if (st.status === "in_progress" && st.partial_progress) {
    const done = st.partial_progress.completed_scene_ids;
    if (Array.isArray(done)) return `${done.length} scene${done.length === 1 ? "" : "s"} done`;
    return "in progress";
  }
  if (st.status === "in_progress") return "in progress";
  if (st.status === "failed") return st.error ? String(st.error).slice(0, 80) : "failed";
  if (st.status === "completed") return st.gated && st.human_approved ? "done, approved by you" : "done";
  return "not started";
}

function gatesCTA(label = "See what needs you") {
  return el("a", { class: "gates-cta", href: "#gates" }, label);
}

function renderBar(s) {
  const counter = el("nav", { class: "counter", "aria-label": "Production stages" });
  for (const st of s.stages) {
    const cls = st.status === "completed" ? "done"
      : st.status === "in_progress" ? (st.stalled ? "active stalled" : "active")
      : st.status === "awaiting_human" ? "await"
      : st.status === "failed" ? "failed" : "";
    counter.append(el("div", {
      class: `stage ${cls}${selectedStage === st.name ? " selected" : ""}`.trim(),
      title: `${humanize(st.name)}: ${stageSub(st)}${st.undeclared ? " (not part of this pipeline's plan)" : ""}`,
    },
      el("button", {
        class: "stage-select",
        type: "button",
        "aria-expanded": selectedStage === st.name ? "true" : "false",
        "aria-label": `${humanize(st.name)}: ${stageSub(st)}. Show details`,
        onclick: () => toggleDrawer(st.name),
      },
        el("span", { class: "name" }, humanize(st.name)),
        el("span", { class: "tick", "aria-hidden": "true" }))));
  }
  const maker = el("a", { class: "maker", href: "/", "aria-label": "All films" });
  maker.innerHTML = MAKER_MARK;
  maker.append(el("span", { class: "maker-name" }, "Front Lot"));
  const board = s.storyboard;
  const kind = board && board.scenes.length
    ? `${board.scenes.length} scenes${board.total_duration_seconds ? ` · ${fmtDuration(board.total_duration_seconds)}` : ""}`
    : null;
  return el("header", { class: "bar" },
    maker,
    el("div", { class: "title-plate" },
      el("h1", {}, s.title),
      kind ? el("span", { class: "kind" }, kind) : null),
    s.stages.length ? counter : el("span", { style: "margin-left:auto" }),
    renderMeter(s),
    renderStatus(s));
}

function toggleDrawer(stageName) {
  selectedStage = selectedStage === stageName ? null : stageName;
  render();
}

const STAGE_ARTIFACTS = {
  research: ["research_brief"],
  proposal: ["proposal_packet"],
  idea: ["brief"],
  script: ["script"],
  scene_plan: ["scene_plan"],
  assets: ["asset_manifest"],
  edit: ["edit_decisions"],
  compose: ["render_report", "final_review"],
  publish: ["publish_log"],
};

function artifactNamesForStage(st) {
  const declared = Array.isArray(st.produces) ? st.produces : [];
  const fallback = STAGE_ARTIFACTS[st.name] || [];
  return [...new Set([...declared, ...fallback].filter(Boolean))];
}

function reviewMetrics(review) {
  const nested = review && review.summary && typeof review.summary === "object"
    ? review.summary : {};
  return {
    critical: Number((review && review.critical) ?? nested.critical ?? 0),
    suggestions: Number((review && review.suggestions) ?? nested.suggestions ?? 0),
    nitpicks: Number((review && review.nitpicks) ?? nested.nitpicks ?? 0),
  };
}

function reviewSummaryText(review) {
  if (!review) return "";
  if (typeof review.summary === "string") return review.summary;
  const nested = review.summary && typeof review.summary === "object" ? review.summary : {};
  const counts = reviewMetrics(review);
  return [
    review.decision,
    `${counts.critical} critical`,
    `${counts.suggestions} suggestion${counts.suggestions === 1 ? "" : "s"}`,
    nested.review_focus_met ? `review focus ${nested.review_focus_met}` : null,
    nested.schema_validation,
  ].filter(Boolean).join(" · ");
}

function renderDrawer(s) {
  if (!selectedStage) return null;
  const st = s.stages.find((x) => x.name === selectedStage);
  if (!st) return null;

  const body = el("div", { class: "drawer-body" });

  if (st.review) {
    const metrics = reviewMetrics(st.review);
    const summary = reviewSummaryText(st.review);
    body.append(el("div", { class: "findings", style: "margin-bottom:12px" },
      el("span", { class: `f ${metrics.critical ? "crit" : ""}` }, `${metrics.critical} critical`),
      el("span", { class: `f ${metrics.suggestions ? "sugg" : ""}` }, `${metrics.suggestions} suggestions`),
      el("span", { class: "f" }, `${metrics.nitpicks} nitpicks`),
      summary ? el("span", { style: "font-size:11.5px;color:var(--text-2);margin-left:8px" }, summary) : null,
    ));
  }

  const names = artifactNamesForStage(st);
  let shown = false;
  for (const name of names) {
    const artifact = s.artifacts[name];
    if (!artifact) continue;
    shown = true;
    body.append(
      el("div", { class: "d-cat" }, humanize(name)),
      readableRecord(artifact, { strip: true }),
    );
  }
  if (!shown) {
    body.append(el("div", { class: "hint" },
      st.status === "pending" ? "This stage hasn't run yet." : "No canonical artifact found on disk for this stage."));
  }

  return el("div", { class: "drawer" },
    el("div", { class: "drawer-head" },
      el("h3", {}, `${humanize(st.name)} — ${humanize(st.status)}`),
      st.gate_skipped ? el("span", { class: "gate-chip" }, "Approval skipped") : null,
      st.versions > 1 ? el("span", { class: "ver-chip" }, `v${st.versions}`) : null,
      st.timestamp ? el("span", { class: "meta" }, readableTime(st.timestamp)) : null,
      el("button", { class: "close quiet-btn", type: "button", onclick: () => toggleDrawer(st.name) }, "Close"),
    ),
    body,
  );
}

// ---------------------------------------------------------------------------
// script card
// ---------------------------------------------------------------------------

function scriptSections(script, limit) {
  const sections = script.sections || [];
  const shown = limit ? sections.slice(0, limit) : sections;
  const nodes = [];
  for (const sec of shown) {
    nodes.push(el("div", { class: "sp-slug" },
      `${(sec.id || "").toUpperCase()} — ${sec.label || "Section"} `,
      el("span", { class: "tc" }, `${fmtDuration(sec.start_seconds)} – ${fmtDuration(sec.end_seconds)}`)));
    if (sec.text) nodes.push(el("div", { class: "sp-action" }, sec.text));
    if (sec.speaker_directions) nodes.push(el("div", { class: "sp-paren" }, `(${sec.speaker_directions})`));
    const cues = sec.enhancement_cues || [];
    if (cues.length) {
      nodes.push(el("div", { style: "margin-left:42px" },
        cues.map((c) => el("span", { class: "sp-cue" }, `▸ ${c.type} · ${String(c.description || "").slice(0, 60)}`))));
    }
  }
  if (limit && sections.length > limit) {
    nodes.push(el("div", { class: "sp-fade" }, `… ${sections.length - limit} more sections`));
  }
  return nodes;
}

function renderScriptCard(s) {
  const script = s.artifacts.script;
  if (!script) return null;
  const scriptStage = s.stages.find((x) => x.name === "script");
  const status = scriptStage ? scriptStage.status : "unknown";
  const stamp = status === "completed"
    ? el("span", { class: "script-status script-approved" }, "APPROVED")
    : status === "awaiting_human"
      ? el("span", { class: "script-status script-pending" }, "PENDING APPROVAL")
      : status === "in_progress"
        ? el("span", { class: "script-status script-draft" }, "DRAFTING")
        : null;

  const card = el("div", { class: "script-card script-preview", title: "Click to expand full script", onclick: openScriptModal },
    stamp,
    el("div", { class: "sp-title" }, script.title || s.title),
    el("div", { class: "sp-meta" },
      `script · ${fmtDuration(script.total_duration_seconds)} · ${(script.sections || []).length} sections`),
    ...scriptSections(script, 4),
    el("span", { class: "sp-expand" }, icon("expand"), "Read the whole script"),
  );
  return card;
}

function humanize(value) {
  return String(value || "artifact").replaceAll("_", " ");
}

// ---------------------------------------------------------------------------
// Readable records: stage artifacts and gate evidence drawn as plain
// headings, sentences, and lists instead of raw JSON. With `strip`, the
// fields that only prove a record (hashes, receipts, file paths, schema
// versions) are left out; they stay on disk and in the signing terminal.

const RECORD_MACHINE_KEYS = new Set([
  "version", "builder_version", "provenance", "qc_receipts",
  "prompt_recipe", "decision_log_ref", "pipeline_manifest",
]);
const RECORD_MACHINE_KEY = /(^|_)(sha256|hash|digest|receipt_id|receipt_ids|asset_id|uuid)$/;
const RECORD_MACHINE_VALUE = /^([0-9a-f]{40,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;
const RECORD_TITLE_KEYS = [
  "name", "title", "label", "decision", "question", "rule", "line", "tradeoff",
  "operation", "stage", "tool", "description", "entity_id", "id", "path",
];

function recordFileName(text) {
  return text.split("/").pop().replace(/\.[a-z0-9]{1,5}$/i, "");
}

function recordKeyHidden(key, value, strip) {
  if (!strip) return false;
  if (RECORD_MACHINE_KEYS.has(key) || RECORD_MACHINE_KEY.test(key)) return true;
  if (typeof value !== "string") return false;
  const text = /(^|_)path$/.test(key) ? recordFileName(value) : value.trim();
  return RECORD_MACHINE_VALUE.test(text);
}

function recordLabel(key) {
  const text = humanize(key.replace(/_path$/, "")).replace(/\busd\b/i, "(USD)").replace(/\bids?\b/i, (m) => m.toUpperCase());
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const RECORD_INLINE_MACHINE = /\b(?:[a-z0-9_]*(?:sha256|hash|digest)[:=]\s*)?(?:[0-9a-f]{32,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b/gi;

function recordScalar(key, value, strip = false) {
  const shown = recordScalarText(key, value);
  return strip ? shown.replace(RECORD_INLINE_MACHINE, "(fingerprint)") : shown;
}

function recordScalarText(key, value) {
  if (value === true || value === "True" || value === "true") return "Yes";
  if (value === false || value === "False" || value === "false") return "No";
  if (/(^|_)usd$/.test(key) && Number.isFinite(Number(value))) return fmtMoney(value);
  const text = String(value);
  if (key === "path") return recordFileName(text).replaceAll("-", " ");
  if (/_path$/.test(key) && text.includes("/")) return text.split("/").pop();
  if (/^[a-z0-9]+(_[a-z0-9]+)+$/.test(text)) return humanize(text);
  return text;
}

function readableTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value)
    : date.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function recordIsEmpty(value) {
  if (value == null || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

function recordEntries(obj, strip) {
  return Object.entries(obj).filter(([key, value]) =>
    !recordIsEmpty(value) && !recordKeyHidden(key, value, strip));
}

function recordItemTitle(item, strip) {
  for (const key of RECORD_TITLE_KEYS) {
    if (typeof item[key] === "string" && item[key].trim() && !recordKeyHidden(key, item[key], strip)) return [key, item[key]];
  }
  return [null, null];
}

function recordFields(obj, strip, skip = new Set()) {
  const short = el("dl", { class: "record-facts" });
  const rest = [];
  for (const [key, value] of recordEntries(obj, strip)) {
    if (skip.has(key)) continue;
    if (value && typeof value === "object") {
      rest.push(el("section", { class: "record-sub" },
        el("h5", {}, recordLabel(key)),
        recordValue(key, value, strip)));
      continue;
    }
    const text = recordScalar(key, value, strip);
    if (text.length > 70) {
      rest.push(el("section", { class: "record-sub" },
        el("h5", {}, recordLabel(key)),
        el("p", {}, text)));
    } else {
      short.append(el("div", {}, el("dt", {}, recordLabel(key)), el("dd", {}, text)));
    }
  }
  return [short.childElementCount ? short : null, ...rest].filter(Boolean);
}

function recordValue(key, value, strip) {
  if (Array.isArray(value)) {
    const items = value.filter((item) => !recordIsEmpty(item));
    if (items.every((item) => item === null || typeof item !== "object")) {
      return el("ul", { class: "record-list" },
        items.filter((item) => !recordKeyHidden(key, item, strip))
          .map((item) => el("li", {}, recordScalar(key, item, strip))));
    }
    return el("div", { class: "record-items" }, items.map((item) => {
      if (!item || typeof item !== "object") return el("p", {}, recordScalar(key, item, strip));
      const [titleKey, title] = recordItemTitle(item, strip);
      const ref = titleKey !== "id" && typeof item.id === "string" ? item.id : null;
      const skip = new Set([titleKey, ref ? "id" : null].filter(Boolean));
      const fields = recordFields(item, strip, skip);
      if (!title && !fields.length) return null;
      return el("article", { class: "record-item" },
        title ? el("h6", {},
          ref ? el("span", { class: "record-ref" }, ref) : null,
          recordScalar(titleKey, title, strip)) : null,
        ...fields);
    }).filter(Boolean));
  }
  if (value && typeof value === "object") {
    return el("div", { class: "record-group" }, recordFields(value, strip));
  }
  return el("p", {}, recordScalar(key, value));
}

function readableRecord(record, { strip = true } = {}) {
  if (record == null || typeof record !== "object") {
    return el("p", { class: "record-lead" }, record == null ? "No value supplied." : String(record));
  }
  if (Array.isArray(record)) return el("div", { class: "record" }, recordValue("items", record, strip));
  const scalars = {};
  const sections = [];
  for (const [key, value] of recordEntries(record, strip)) {
    if (value && typeof value === "object") {
      const count = Array.isArray(value) ? value.length : null;
      sections.push(el("details", { class: "record-section", open: count != null && count <= 4 ? "" : null },
        el("summary", {}, recordLabel(key), count != null ? el("span", { class: "record-count" }, String(count)) : null),
        recordValue(key, value, strip)));
    } else {
      scalars[key] = value;
    }
  }
  return el("div", { class: "record" }, ...recordFields(scalars, strip), ...sections);
}

function shortText(value, limit = 180) {
  const text = String(value || "").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function reviewFact(label, value) {
  if (value == null || value === "") return null;
  return el("div", { class: "approval-fact" },
    el("span", {}, label),
    el("b", {}, value),
  );
}

function reviewFacts(items) {
  const facts = items.filter(Boolean);
  return facts.length ? el("div", { class: "approval-facts" }, facts) : null;
}

function titledItems(items, selectedId = null) {
  const rows = (items || []).slice(0, 4).map((item, index) => {
    if (item == null) return null;
    if (typeof item !== "object") {
      return el("li", {}, shortText(item));
    }
    const id = item.id || item.concept_id || item.option_id;
    const title = item.title || item.name || item.display_name || item.label || id || item.path || item.platform || item.description || `Item ${index + 1}`;
    const detail = item.hook || item.why_this_works || item.summary || item.description || item.silhouette_notes;
    return el("li", { class: id && id === selectedId ? "selected" : "" },
      el("div", { class: "approval-item-title" }, shortText(title, 100),
        id && id === selectedId ? el("span", { class: "approval-selected" }, "SELECTED") : null),
      detail && detail !== title ? el("p", {}, shortText(detail)) : null,
    );
  }).filter(Boolean);
  return rows.length ? el("ul", { class: "approval-items" }, rows) : null;
}

function genericArtifactSummary(artifact) {
  const facts = [];
  const items = [];
  for (const [key, value] of Object.entries(artifact || {})) {
    if (["version", "decision_log_ref"].includes(key)) continue;
    if (["string", "number", "boolean"].includes(typeof value)) {
      facts.push(reviewFact(humanize(key), shortText(value, 90)));
    } else if (Array.isArray(value)) {
      facts.push(reviewFact(humanize(key), `${value.length} item${value.length === 1 ? "" : "s"}`));
      if (!items.length && value.length) items.push(titledItems(value));
    }
    if (facts.length >= 6) break;
  }
  return [reviewFacts(facts), ...items].filter(Boolean);
}

function artifactReviewContent(name, artifact) {
  if (name === "brief") {
    return [
      artifact.hook ? el("p", { class: "approval-lead" }, artifact.hook) : null,
      reviewFacts([
        reviewFact("platform", artifact.target_platform),
        reviewFact("duration", artifact.target_duration_seconds != null ? fmtDuration(artifact.target_duration_seconds) : null),
        reviewFact("tone", artifact.tone),
        reviewFact("style", artifact.style),
      ]),
      titledItems(artifact.key_points),
    ].filter(Boolean);
  }
  if (name === "proposal_packet") {
    const selected = (artifact.selected_concept || {}).concept_id;
    const plan = artifact.production_plan || {};
    const cost = artifact.cost_estimate || {};
    return [
      reviewFacts([
        reviewFact("runtime", plan.render_runtime),
        reviewFact("pipeline", plan.pipeline),
        reviewFact("estimated cost", cost.total_estimated_usd != null ? fmtMoney(cost.total_estimated_usd) : null),
        reviewFact("concepts", Array.isArray(artifact.concept_options) ? artifact.concept_options.length : null),
      ]),
      titledItems(artifact.concept_options, selected),
      (artifact.selected_concept || {}).rationale
        ? el("p", { class: "approval-rationale" },
          el("b", {}, "WHY THIS CONCEPT  "), shortText(artifact.selected_concept.rationale))
        : null,
    ].filter(Boolean);
  }
  if (name === "research_brief") {
    return [
      artifact.topic ? el("p", { class: "approval-lead" }, artifact.topic) : null,
      reviewFacts([
        reviewFact("sources", Array.isArray(artifact.sources) ? artifact.sources.length : null),
        reviewFact("data points", Array.isArray(artifact.data_points) ? artifact.data_points.length : null),
        reviewFact("angles", Array.isArray(artifact.angles_discovered) ? artifact.angles_discovered.length : null),
      ]),
      titledItems(artifact.angles_discovered),
    ].filter(Boolean);
  }
  if (name === "script") {
    const first = (artifact.sections || [])[0];
    return [
      reviewFacts([
        reviewFact("duration", fmtDuration(artifact.total_duration_seconds)),
        reviewFact("sections", (artifact.sections || []).length),
      ]),
      first && first.text ? el("p", { class: "approval-lead" }, shortText(first.text, 220)) : null,
      el("p", { class: "approval-guidance" }, "The complete script preview is shown directly below."),
    ].filter(Boolean);
  }
  if (name === "scene_plan") {
    const scenes = artifact.scenes || [];
    const end = scenes.reduce((max, scene) => Math.max(max, Number(scene.end_seconds) || 0), 0);
    return [
      reviewFacts([
        reviewFact("scenes", scenes.length),
        reviewFact("duration", end ? fmtDuration(end) : null),
      ]),
      titledItems(scenes),
      el("p", { class: "approval-guidance" }, "Review timing and shot coverage in the storyboard below."),
    ].filter(Boolean);
  }
  if (name === "asset_manifest") {
    const assets = artifact.assets || [];
    const types = [...new Set(assets.map((asset) => asset.type).filter(Boolean))];
    return [
      reviewFacts([
        reviewFact("assets", assets.length),
        reviewFact("types", types.join(", ")),
        reviewFact("generation cost", artifact.total_cost_usd != null ? fmtMoney(artifact.total_cost_usd) : null),
      ]),
      titledItems(assets),
      el("p", { class: "approval-guidance" }, "Inspect every generated take in the filmstrip below before approving compose."),
    ].filter(Boolean);
  }
  if (name === "edit_decisions") {
    return [
      reviewFacts([
        reviewFact("cuts", Array.isArray(artifact.cuts) ? artifact.cuts.length : null),
        reviewFact("runtime", artifact.render_runtime || (artifact.metadata || {}).render_runtime),
      ]),
      titledItems(artifact.cuts),
    ].filter(Boolean);
  }
  if (name === "render_report") {
    return [
      reviewFacts([
        reviewFact("outputs", Array.isArray(artifact.outputs) ? artifact.outputs.length : null),
        reviewFact("duration", artifact.duration_seconds != null ? fmtDuration(artifact.duration_seconds) : null),
      ]),
      titledItems(artifact.outputs),
    ].filter(Boolean);
  }
  if (name === "publish_log") {
    return [
      reviewFacts([reviewFact("destinations", Array.isArray(artifact.entries) ? artifact.entries.length : null)]),
      titledItems((artifact.entries || []).map((entry) => ({
        title: entry.platform || entry.destination || "Publish destination",
        description: [entry.status, entry.url].filter(Boolean).join(" · "),
      }))),
    ].filter(Boolean);
  }
  return genericArtifactSummary(artifact);
}

function artifactReviewTitle(name, artifact, s) {
  if (name === "proposal_packet") {
    const selected = (artifact.selected_concept || {}).concept_id;
    const concept = (artifact.concept_options || []).find((item) => item.id === selected);
    return (concept && concept.title) || "Production proposal";
  }
  if (name === "research_brief") return artifact.topic || "Research brief";
  if (name === "scene_plan") return "Scene plan";
  if (name === "asset_manifest") return "Generated assets";
  if (name === "edit_decisions") return "Edit decisions";
  if (name === "render_report") return "Render report";
  if (name === "publish_log") return "Publish plan";
  return artifact.title || artifact.name || s.title;
}

function renderApprovalReview(s) {
  const awaiting = s.stages.find((item) => item.status === "awaiting_human");
  if (!awaiting) return null;

  const names = artifactNamesForStage(awaiting);
  const entries = names
    .filter((name) => name !== "decision_log")
    .map((name) => [name, s.artifacts[name]])
    .filter(([, artifact]) => artifact && typeof artifact === "object");
  const stageIndex = s.stages.findIndex((item) => item.name === awaiting.name);
  const nextStage = stageIndex >= 0 ? s.stages[stageIndex + 1] : null;
  const review = awaiting.review || {};
  const reviewSummary = reviewSummaryText(review);

  const artifacts = entries.map(([name, artifact]) => el("article", {
    class: "approval-artifact",
    "data-artifact": name,
  },
    el("div", { class: "approval-artifact-kicker" }, humanize(name)),
    el("h2", {}, artifactReviewTitle(name, artifact, s)),
    ...artifactReviewContent(name, artifact),
  ));

  if (!artifacts.length) {
    artifacts.push(el("div", { class: "approval-missing", role: "alert" },
      el("b", {}, "Nothing reviewable was found. "),
      names.length
        ? `The ${awaiting.name} checkpoint declares ${names.map(humanize).join(", ")}, but Front Lot could not load it.`
        : `The ${awaiting.name} checkpoint does not declare an artifact.`,
    ));
  }

  return el("section", { class: "approval-review", "data-stage": awaiting.name },
    el("div", { class: "approval-review-head" },
      el("div", {},
        el("div", { class: "approval-eyebrow" }, "REVIEW GATE"),
        el("h2", {}, `${humanize(awaiting.name)} is ready for your review`),
        el("p", {},
          "Review the artifact here. ",
          hasAuthoredGates(s) ? gatesCTA("GO TO GATES") : "Continue with your agent when the review is complete."),
      ),
      el("span", { class: "approval-status" }, "PENDING APPROVAL"),
    ),
    reviewSummary ? el("div", { class: "approval-review-note" },
      el("b", {}, "SELF-REVIEW  "), shortText(reviewSummary, 260)) : null,
    el("div", { class: "approval-artifacts" }, artifacts),
    el("div", { class: "approval-review-foot" },
      el("span", {}, nextStage
        ? `Approval unlocks ${humanize(nextStage.name)}.`
        : "This is the final approval gate."),
      el("button", { type: "button", onclick: () => toggleDrawer(awaiting.name) }, "OPEN FULL ARTIFACT"),
    ),
  );
}

function openScriptModal() {
  const script = state && state.artifacts.script;
  if (!script) return;
  modal.innerHTML = "";
  modal.append(
    el("span", { class: "modal-close", onclick: closeModal }, "ESC · CLOSE"),
    el("div", { class: "modal-page" },
      el("div", { class: "script-card", style: "cursor:default" },
        el("div", { class: "sp-title" }, script.title || state.title),
        el("div", { class: "sp-meta" },
          `script · ${fmtDuration(script.total_duration_seconds)} · ${(script.sections || []).length} sections`),
        ...scriptSections(script, 0),
        el("div", { class: "sp-fade" }, "END"),
      )),
  );
  modal.classList.add("open");
}

function openNarrModal(card) {
  modal.innerHTML = "";
  const meta = [sceneLabel(card.id), card.section_label, fmtDuration(card.duration_seconds)]
    .filter(Boolean).join(" · ");
  modal.append(
    el("span", { class: "modal-close", onclick: closeModal }, "ESC · CLOSE"),
    el("div", { class: "modal-page" },
      el("div", { class: "script-card", style: "cursor:default" },
        el("div", { class: "sp-meta" }, meta),
        card.narration ? el("div", { class: "sp-action", style: "margin-left:0" }, card.narration) : null,
        card.shot_intent ? el("div", { class: "sp-paren", style: "margin-left:0" }, `Intent — ${card.shot_intent}`) : null,
        card.description ? el("div", { class: "sp-paren", style: "margin-left:0" }, card.description) : null,
      )),
  );
  modal.classList.add("open");
}

function closeModal() { modal.classList.remove("open"); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

// ---------------------------------------------------------------------------
// log column: production decisions, recent work
// ---------------------------------------------------------------------------

function renderDecisions(s) {
  const log = s.artifacts.decision_log;
  const decisions = (log && log.decisions) || [];
  if (!decisions.length) return null;
  const body = el("ol", { class: "entries" });
  // Collapse by category+subject: a decision that changed mid-run (e.g. voice
  // openai_onyx → chirp3) is superseded by the later entry — show the CURRENT
  // choice, not the first one recorded, and mark that it was revised.
  const current = new Map();
  decisions.forEach((d, i) => {
    const key = `${d.category || "decision"}::${d.subject || ""}`;
    const prev = current.get(key);
    current.set(key, { d, order: i, revised: prev ? prev.revised + 1 : 0 });
  });
  const shown = [...current.values()].sort((a, b) => b.order - a.order).slice(0, 8);
  for (const { d, revised } of shown) {
    const selLabel = (() => {
      // Prefer the human label of the selected option over its bare id.
      const opt = (d.options_considered || []).find((o) => (o.option_id ?? o.label) === d.selected);
      return (opt && opt.label) || d.selected || "";
    })();
    const alts = (d.options_considered || [])
      .filter((o) => (o.option_id ?? o.label) !== d.selected && (o.option_id || o.label));
    body.append(el("li", { class: "entry done decision" },
      el("span", { class: "mark", "aria-hidden": "true" }),
      el("div", {},
        el("span", { class: "text" }, `${plain(d.subject) || humanize(d.category)}: ${plain(selLabel)}`),
        d.reason ? el("span", { class: "why" }, shortText(plain(d.reason), 220)) : null,
        el("small", {}, [humanize(d.category || "decision"), revised ? "changed since" : null,
          alts.length ? `also considered ${alts.slice(0, 2).map((o) => plain(o.label || o.option_id)).join(", ")}` : null]
          .filter(Boolean).join(" · ")))));
  }
  return el("details", { class: "log-section log-more" },
    el("summary", {}, "Production decisions"),
    body);
}

function renderActivity(s) {
  const events = s.events || [];
  if (!events.length) return null;
  const body = el("ol", { class: "entries" });
  // A start is "running" only until a later finish/error for the same
  // tool+scene closes it — closed starts are dropped (the finish row tells
  // the story), unmatched starts render as live. Counted (not keyed-single)
  // so parallel runs of the same tool on the same scene stay visible.
  const open = new Map(); // key -> {count, ev}
  const rows = [];
  for (const ev of events) {
    const key = `${ev.tool}:${ev.scene_id || ""}`;
    if (ev.event === "start") {
      const slot = open.get(key) || { count: 0, ev };
      slot.count += 1;
      slot.ev = ev;
      open.set(key, slot);
    } else {
      const slot = open.get(key);
      if (slot) {
        slot.count -= 1;
        if (slot.count <= 0) open.delete(key);
      }
      rows.push(ev);
    }
  }
  for (const slot of open.values()) rows.push(slot.ev);
  rows.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  let running = 0;
  for (const ev of rows.slice(-10).reverse()) {
    const failed = ev.event === "error" || (ev.event === "finish" && ev.success === false);
    const live = ev.event === "start";
    if (live) running += 1;
    const detail = live ? "running now"
      : failed ? "failed"
      : [ev.duration_s != null ? `${Number(ev.duration_s).toFixed(1)} s` : null, ev.cost_usd ? fmtMoney(ev.cost_usd) : null].filter(Boolean).join(" · ") || "done";
    body.append(el("li", { class: `entry ${live ? "running" : failed ? "declined" : "done"}` },
      el("span", { class: "mark", "aria-hidden": "true" }),
      el("div", {},
        el("span", { class: "text" }, `${titleCase(ev.tool || "tool")}${ev.scene_id ? `, ${sceneLabel(ev.scene_id)}` : ""}`),
        el("small", {}, `${fmtClock(ev.ts)} · ${detail}`))));
  }
  const section = el("details", { class: "log-section log-more" },
    el("summary", {}, running ? `Machine room · ${running} running` : "Machine room"),
    body);
  if (running) section.open = true;
  return section;
}

// ---------------------------------------------------------------------------
// storyboard filmstrip
// ---------------------------------------------------------------------------

function sceneLabel(id) {
  // "sc4" → "SC 04", "scene-11" → "SC 11", anything else → uppercased id
  const m = String(id).match(/(\d+)\s*$/);
  if (m) return `SC ${m[1].padStart(2, "0")}`;
  return String(id).toUpperCase().slice(0, 10);
}

function sceneCard(s, card) {
  const dur = card.duration_seconds;
  const width = Math.max(132, Math.min(300, 70 + (dur || 3) * 26));
  const wrap = el("div", { class: "scene-card", style: `width:${width}px` });

  const slate = el("div", { class: "sc-slate" },
    el("span", { class: "num" }, sceneLabel(card.id)),
    card.takes.length > 1 ? el("span", { class: "take" }, `T${card.takes.length}`) : null,
    card.hero_moment ? el("span", { class: "hero" }, icon("star"), "Hero") : null,
    el("span", { class: "dur" }, fmtDuration(dur)),
  );
  wrap.append(slate);

  // visual slot
  let thumb;
  if (card.generating) {
    thumb = el("div", { class: "thumb generating" },
      el("div", { class: "shimmer" }),
      el("div", { class: "gen-label" },
        el("span", {}, "Making this now"),
        el("span", { class: "sub" }, card.generating_tool || "")));
  } else if (card.visual && card.visual.exists) {
    const v = card.visual;
    const badge = [v.model || v.source_tool, v.cost_usd != null ? fmtMoney(v.cost_usd) : null,
      v.quality_score != null ? `q ${v.quality_score}` : null].filter(Boolean).join(" · ");
    if (v.type === "video") {
      thumb = el("div", { class: "thumb approved" },
        el("video", { src: mediaURL(s.project_id, v.path), muted: "", preload: "metadata", playsinline: "" }),
        el("span", { class: "play" }, icon("play")),
        badge ? el("span", { class: "badge" }, badge) : null);
      thumb.onclick = () => {
        const vid = thumb.querySelector("video");
        if (vid.paused) vid.play(); else vid.pause();
      };
    } else {
      const img = el("img", { src: thumbURL(s.project_id, v.path, 640), loading: "lazy", alt: "" });
      // A thumbnail that fails to load must never show a broken-image icon —
      // fall back to the shot spec in place (F: broken links).
      img.onerror = () => {
        const t = img.closest(".thumb");
        if (!t) return;
        t.className = "thumb spec";
        t.innerHTML = "";
        t.append(el("div", { class: "spec-in" },
          el("div", { class: "spec-desc" }, card.description || "asset unavailable"),
          el("div", { class: "spec-shot" }, [card.framing, card.movement].filter(Boolean).join(" · ").slice(0, 70))));
      };
      thumb = el("div", { class: "thumb approved" }, img,
        v.snapshot ? el("span", { class: "badge" }, "snapshot") : (badge ? el("span", { class: "badge" }, badge) : null));
    }
  } else if (card.type === "animation") {
    // Bespoke/atelier scene with no snapshot yet — name it as such rather
    // than "no asset yet" (the composition IS the asset).
    thumb = el("div", { class: "thumb spec bespoke" },
      el("div", { class: "spec-in" },
        el("span", { class: "bespoke-tag" }, "Hand-built"),
        el("div", { class: "spec-desc" }, card.description || ""),
        el("div", { class: "spec-shot" }, "hand-authored composition")));
  } else if (card.visual && !card.visual.exists) {
    thumb = el("div", { class: "thumb missing" },
      el("div", { class: "spec-in" },
        el("span", { class: "warn-ic" }, "Missing"),
        el("div", { class: "spec-desc" }, "asset in manifest, file missing"),
        el("div", { class: "spec-shot" }, card.visual.path || "")));
  } else if (card.type === "text_card") {
    thumb = el("div", { class: "thumb textcard" },
      el("div", { class: "tc-copy" }, (card.narration || card.description || "").slice(0, 48)));
  } else if (card.required_assets.length) {
    thumb = el("div", { class: "thumb missing" },
      el("div", { class: "spec-in" },
        el("span", { class: "warn-ic" }, "Missing"),
        el("div", { class: "spec-desc" }, "no asset yet"),
        el("div", { class: "spec-shot" }, (card.required_assets[0].description || "").slice(0, 60))));
  } else {
    thumb = el("div", { class: "thumb spec" },
      el("div", { class: "spec-in" },
        el("div", { class: "spec-desc" }, card.description || ""),
        el("div", { class: "spec-shot" }, [card.framing, card.movement].filter(Boolean).join(" · ").slice(0, 70))));
  }
  wrap.append(thumb);

  // shot language chips
  const sl = card.shot_language;
  if (sl) {
    wrap.append(el("div", { class: "shotchips", style: "display:flex;flex-wrap:wrap;gap:4px;padding:7px 2px 0" },
      [sl.shot_size, sl.camera_movement, sl.lens_mm ? `${sl.lens_mm}mm` : null, sl.lighting_key]
        .filter(Boolean)
        .map((t) => el("span", { style: "font-family:var(--mono);font-size:8.5px;letter-spacing:.04em;color:#62626c;border:1px solid #212129;border-radius:3px;padding:1px 5px" }, String(t).replaceAll("_", " ")))));
  }

  // takes drawer
  if (card.takes.length > 1) {
    const takes = el("div", { class: "takes" });
    card.takes.forEach((t, i) => {
      const isActive = card.visual && (
        t === card.visual
        || (t.path && t.path === card.visual.path)
        || (t.id && t.id === card.visual.id)
      );
      const tk = el("span", { class: `tk${isActive ? " active" : ""}`, title: `take ${i + 1}` });
      if (t.exists && t.type === "image") tk.append(el("img", { src: thumbURL(s.project_id, t.path, 320), loading: "lazy", alt: "" }));
      takes.append(tk);
    });
    takes.append(el("span", { class: "tk-label" }, `${card.takes.length} TAKES`));
    wrap.append(takes);
  }

  // narration + audio — clickable to read in full (F: narration text cut off)
  if (card.narration) {
    const long = card.narration.length > 90;
    wrap.append(el("div", {
      class: `narr${long ? " clip" : ""}`,
      title: "Click to read the full narration",
      onclick: () => openNarrModal(card),
    }, card.narration, long ? el("span", { class: "narr-more" }, icon("expand")) : null));
  } else if (card.shot_intent || card.description) {
    wrap.append(el("div", { class: "narr tc-note" }, (card.shot_intent || card.description || "").slice(0, 110)));
  }
  const narrAudio = card.audio.find((a) => a.exists && (a.type === "narration" || a.type === "audio"));
  if (narrAudio) {
    const wave = el("div", { class: "wave", style: "cursor:pointer", title: "Play narration" });
    waveBars(wave, card.id + narrAudio.path);
    wave.append(el("span", { class: "wv-time" }, narrAudio.duration_seconds ? fmtDuration(narrAudio.duration_seconds) : ""));
    wave.onclick = () => {
      player.src = mediaURL(s.project_id, narrAudio.path);
      player.play();
    };
    wrap.append(wave);
  }
  return wrap;
}

function renderStoryboard(s) {
  const board = s.storyboard;
  if (!board) return null;
  const strip = el("div", { class: "filmstrip" });
  for (const card of board.scenes) strip.append(sceneCard(s, card));
  return el("div", {},
    el("div", { class: "section-title" }, "Storyboard",
      el("span", { class: "meta" },
        `${board.scenes.length} scenes${board.total_duration_seconds ? ` · ${fmtDuration(board.total_duration_seconds)}` : ""} · card width ∝ duration`)),
    el("div", { class: "strip-outer" }, strip));
}

// ---------------------------------------------------------------------------
// renders + degraded media
// ---------------------------------------------------------------------------

function renderRenders(s) {
  const renders = s.media.renders;
  if (!renders.length) return null;
  if (activeRender >= renders.length) activeRender = 0;
  const current = renders[activeRender];
  // Full re-renders (every SSE refresh) must not reset an in-progress
  // watch: carry playback position/state over to the recreated element.
  const prev = document.querySelector(".render-hero video");
  const src = mediaURL(s.project_id, current.path);
  // preload="metadata" gives the element its intrinsic aspect ratio (and a
  // poster frame) before playback — without it a portrait 9:16 render sits
  // in a letterboxed 100%-wide black box that reads as landscape.
  const video = el("video", { src, controls: "", preload: "metadata" });
  // Click the frame to start playback (controls handle pause/scrub) — the
  // big player was inert to a click on the picture itself.
  video.addEventListener("click", () => { if (video.paused) video.play().catch(() => {}); });
  if (prev && prev.getAttribute("src") === src && (prev.currentTime > 0 || !prev.paused)) {
    const t = prev.currentTime;
    const wasPlaying = !prev.paused && !prev.ended;
    video.addEventListener("loadedmetadata", () => { video.currentTime = t; }, { once: true });
    video.setAttribute("preload", "metadata");
    if (wasPlaying) video.autoplay = true;
  }
  const versions = el("div", { class: "render-meta" },
    renders.map((r, i) => el("span", {
      class: `v${i === activeRender ? " active" : ""}`,
      onclick: () => { activeRender = i; render(); },
    }, `${r.path.split("/").pop()}${r.at_root ? " · root" : ""}`)),
    el("span", { style: "margin-left:auto" }, `${(current.size / 1048576).toFixed(1)} MB`),
  );
  return el("div", {},
    el("div", { class: "section-title" }, "Renders",
      el("span", { class: "meta" }, `${renders.length} version${renders.length === 1 ? "" : "s"}`)),
    el("div", { class: "render-hero" }, video),
    versions);
}

function renderFoundMedia(s) {
  // Degraded view: show discovered snapshots when there's no storyboard.
  if (s.storyboard || !s.media.snapshots.length) return null;
  const grid = el("div", { class: "found-grid" });
  for (const snap of s.media.snapshots.slice(0, 12)) {
    grid.append(el("div", { class: "thumb" },
      el("img", { src: thumbURL(s.project_id, snap.path, 640), loading: "lazy", alt: "" })));
  }
  return el("div", {},
    el("div", { class: "section-title" }, "What the watcher found",
      el("span", { class: "meta" }, "snapshots / verification frames")),
    grid);
}

function renderNoState(s) {
  if (s.has_pipeline_state) return null;
  return el("div", { class: "notice", style: "border-color:#2b2b33;background:var(--surface-2);color:var(--text-3)" },
    el("span", { style: "font-size:15px" }, "◌"),
    el("span", {},
      el("b", { style: "color:var(--text-2)" }, "No pipeline state. "),
      "This project has no checkpoints — Front Lot is showing what it found on disk. ",
      "Runs that follow the checkpoint protocol get the full board."));
}

function renderAwaitingNotice(s) {
  const awaiting = s.stages.find((x) => x.status === "awaiting_human");
  if (!awaiting) return null;
  return el("div", { class: "notice" },
    
    el("span", {},
      el("b", {}, `The ${awaiting.name} stage is waiting for your review. `),
      "The agent is paused at this gate. ",
      hasAuthoredGates(s) ? gatesCTA("OPEN GATES TO REVIEW AND SIGN") : "Continue the review with your agent."));
}

// ---------------------------------------------------------------------------
// Authored-film gates workbench
// ---------------------------------------------------------------------------

function hasAuthoredGates(s) {
  return s.pipeline.pipeline_type === "authored-film" && s.gates != null;
}

function fmtGateTime(value) {
  if (value == null || value === "") return "—";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (!Number.isFinite(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function gateStateChip(value) {
  const stateName = String(value || "unknown");
  return el("span", { class: `gate-state-chip ${stateName}` }, humanize(stateName));
}

function gateRowById(s, requestId) {
  return ((s.gates && s.gates.requests) || []).find((row) => row.request_id === requestId) || null;
}

function focusGateDetail(preferHeading = false) {
  requestAnimationFrame(() => {
    const target = preferHeading
      ? document.getElementById("gate-detail-heading") || document.getElementById("gate-detail")
      : document.getElementById("gate-detail");
    if (target) target.focus();
  });
}

async function selectGate(requestId, { background = false } = {}) {
  const generation = ++gateSelectionGeneration;
  if (gateDetailController) gateDetailController.abort();
  const controller = new AbortController();
  gateDetailController = controller;
  selectedGateId = requestId;
  if (!background) {
    gateDetail = null;
    gateDetailError = "";
    gateDetailState = "loading";
    copyStatus = "";
    render();
    focusGateDetail(false);
  }
  try {
    const detail = await getJSON(
      `/api/project/${encodedProjectId}/gate/${encodeURIComponent(requestId)}`,
      { signal: controller.signal },
    );
    if (generation !== gateSelectionGeneration || selectedGateId !== requestId) return;
    gateDetail = detail;
    gateDetailState = "ready";
  } catch (error) {
    if (error && error.name === "AbortError") return;
    if (generation !== gateSelectionGeneration || selectedGateId !== requestId) return;
    gateDetailError = String(error);
    gateDetailState = "error";
  } finally {
    if (gateDetailController === controller) gateDetailController = null;
  }
  render();
  if (!background) focusGateDetail(true);
}

function visualFigure(s, visual, label, ordinal = null) {
  const name = ordinal == null ? String(label || visual.label || "evidence") : `[${ordinal}]`;
  const description = ordinal == null
    ? `${humanize(label || visual.label || "evidence")} for ${selectedGateId}`
    : `Candidate ${ordinal} for ${selectedGateId}`;
  const meta = [visual.asset_id ? `asset ${String(visual.asset_id).slice(0, 12)}` : null, visual.error]
    .filter(Boolean).join(" · ");
  const frame = visual.path && !visual.error
    ? el("a", {
      class: "gate-evidence-link",
      href: mediaURL(s.project_id, visual.path),
      target: "_blank",
      rel: "noreferrer",
      "aria-label": `Open full-size ${description}`,
    }, el("img", {
      class: "gate-evidence-image",
      src: thumbURL(s.project_id, visual.path, 640),
      width: "640",
      height: "480",
      loading: "lazy",
      alt: description,
    }))
    : el("div", { class: "gate-evidence-missing", role: visual.error ? "alert" : null },
      visual.error || "Evidence image unavailable");
  return el("figure", { class: `gate-evidence-card${visual.error ? " error" : ""}` },
    frame,
    el("figcaption", {},
      el("b", {}, name),
      label && name !== label ? el("span", {}, humanize(label)) : null,
      meta ? el("span", {}, meta) : null));
}

function textEvidence(label, value) {
  return el("section", { class: "gate-text-evidence" },
    el("h4", {}, humanize(label)),
    value && typeof value === "object"
      ? readableRecord(value, { strip: false })
      : el("p", {}, value == null || value === "" ? "No value supplied." : recordScalar(label, value)));
}

function renderPacketEvidence(s, packet, { visuals: showVisuals = true } = {}) {
  if (!packet || typeof packet !== "object") {
    return el("div", { class: "gate-empty" }, "This request has no evidence packet.");
  }
  const output = el("div", { class: "gate-packet" });
  if (packet.packet_error || packet.error) {
    output.append(el("div", { class: "gate-packet-error", role: "alert" },
      el("b", {}, "Evidence problem"),
      el("span", {}, packet.error || "One or more evidence records failed verification.")));
  }

  const visuals = [];
  if (Array.isArray(packet.candidates)) {
    for (const candidate of packet.candidates) {
      if (!candidate || !candidate.visual) continue;
      visuals.push(visualFigure(
        s,
        candidate.visual,
        candidate.generator_kind || candidate.visual.label || "candidate",
        candidate.number,
      ));
    }
  }
  if (Array.isArray(packet.visuals)) {
    for (const visual of packet.visuals) {
      if (visual) visuals.push(visualFigure(s, visual, visual.label));
    }
  }
  if (packet.visual) visuals.push(visualFigure(s, packet.visual, packet.visual.label));
  if (Array.isArray(packet.field_rows)) {
    // Batch hero waiver (authored-film 1.5): the whole reviewed field —
    // every failed candidate with its failing items. The board only shows;
    // the signer reconstructs, previews the unlock, and binds the digest.
    let n = 0;
    for (const row of packet.field_rows) {
      if (!row || !row.visual) continue;
      n += 1;
      const items = Array.isArray(row.failing_items) ? row.failing_items.join(", ") : "";
      visuals.push(visualFigure(s, row.visual, `FAIL ${items || "(unknown items)"}`, n));
    }
  }
  if (showVisuals && visuals.length) {
    output.append(el("div", { class: "gate-contact-sheet" }, visuals));
  }

  if (Array.isArray(packet.qc_rows)) {
    const qc = el("div", { class: "gate-qc-list" });
    for (const row of packet.qc_rows) {
      qc.append(el("section", { class: "gate-qc-row" },
        el("div", { class: "gate-qc-head" },
          el("b", {}, humanize(row.role || "sheet role")),
          el("span", {}, row.label || "unverified")),
        readableRecord(row.row, { strip: false })));
    }
    output.append(qc);
  }

  const visualKeys = new Set(["candidates", "visuals", "visual", "qc_rows", "field_rows", "packet_error", "error"]);
  // Short scalar fields (counts, hashes, flags) read as one quiet chip strip;
  // anything long or structured keeps its own labelled block.
  const chips = [];
  const blocks = [];
  for (const [key, value] of Object.entries(packet)) {
    if (visualKeys.has(key)) continue;
    const scalar = ["string", "number", "boolean"].includes(typeof value);
    const text = scalar ? String(value) : null;
    if (scalar && text.length <= 64 && !text.includes("\n")) {
      chips.push(el("div", { class: "gate-meta-chip" },
        el("dt", {}, humanize(key)), el("dd", {}, text === "" ? "—" : text)));
    } else {
      blocks.push(textEvidence(key, value));
    }
  }
  if (chips.length) output.append(el("dl", { class: "gate-meta-strip" }, chips));
  for (const block of blocks) output.append(block);
  if (!output.childNodes.length) output.append(el("div", { class: "gate-empty" }, "The evidence packet is empty."));
  return output;
}

function websocketCloseMessage(code, reason) {
  if (code === 4401) return ["Can't sign from this window", "Close this window and open Front Lot again from its icon, then try again.", "error"];
  if (code === 4409 && /identity mismatch/i.test(reason || "")) return ["Wrong request", "The open signing program belongs to a different request.", "error"];
  if (code === 4409) return ["Already signing", "Another signing session is open for this film. Finish or close it first.", "warn"];
  if (code === 4423) return ["Production running", "A production run is working on this film. Signing opens when it stops.", "warn"];
  if (code === 4422) return ["Evidence missing", "The signing program is open, but typing stays off until every picture can be shown.", "error"];
  if (code === 4400) return ["Request changed", "This request changed before signing could start. Look again, then retry.", "warn"];
  if (code === 4403) return ["Refused", "Front Lot refused this window. Open it again from its icon.", "error"];
  if (code === 4404) return ["Request gone", "This film or request is no longer there.", "error"];
  if (code === 1011) return ["Signing unavailable", reason || "The signing program couldn't start.", "error"];
  if (code === 1000) return ["Closed", "Signing closed.", ""];
  return ["Disconnected", reason || `The signing connection closed (${code}).`, "warn"];
}

function startSigning(requestId) {
  if (gateSession.socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(gateSession.socket.readyState)) return;
  let token = null;
  try {
    token = sessionStorage.getItem(CAPABILITY_TOKEN_KEY);
  } catch {
    token = null;
  }
  terminalShell.hidden = false;
  terminalShell.dataset.used = "1";
  if (!token) {
    setTerminalStatus("Can't sign from this window", "Close this window and open Front Lot again from its icon, then try again.", "error");
    terminalShell.scrollIntoView({ behavior: "auto", block: "start" });
    return;
  }
  if (!gateSession.terminal) {
    setTerminalStatus("Signing unavailable", "The signing panel didn't load. Reload the page.", "error");
    return;
  }

  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${scheme}//${location.host}/api/project/${encodedProjectId}/gate/${encodeURIComponent(requestId)}/tty`);
  socket.binaryType = "arraybuffer";
  gateSession.socket = socket;
  gateSession.requestId = requestId;
  gateSession.inputReady = false;
  gateSession.receivedRefresh = false;
  gateSession.evidenceChanged = false;
  setTerminalStatus("Connecting", "Opening the signing program…", "warn");
  terminalShell.scrollIntoView({ behavior: "auto", block: "start" });

  socket.addEventListener("open", () => {
    if (gateSession.socket !== socket) return;
    socket.send(JSON.stringify({ k: token }));
    setTerminalStatus("Checking", "Re-reading the pictures before you can type…", "warn");
    scheduleTerminalFit();
  });
  socket.addEventListener("message", async (event) => {
    if (gateSession.socket !== socket) return;
    if (event.data instanceof ArrayBuffer) {
      gateSession.terminal.write(new Uint8Array(event.data));
      return;
    }
    if (event.data instanceof Blob) {
      gateSession.terminal.write(new Uint8Array(await event.data.arrayBuffer()));
      return;
    }
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === "packet_refreshed" && message.packet) {
      const changed = gateDetail && stablePacketContent(gateDetail) !== stablePacketContent(message.packet);
      gateDetail = message.packet;
      gateDetailState = "ready";
      gateDetailError = "";
      selectedGateId = requestId;
      gateSession.receivedRefresh = true;
      gateSession.evidenceChanged = Boolean(changed);
      gateSession.inputReady = false;
      render();
      const packetError = Boolean(
        gateDetail.packet_error || gateDetail.error || gateDetail.packet?.packet_error,
      );
      setTerminalStatus(
        packetError ? "Evidence missing" : changed ? "Pictures changed" : "Pictures checked",
        packetError
          ? "Some evidence can't be shown, so typing stays off. The signing program is still open."
          : changed
          ? "The pictures changed since you opened this. Look at them again in the viewer before you answer."
          : "Pictures re-checked. Opening the signing program…",
        packetError ? "error" : "warn",
      );
      return;
    }
    if (message.type === "input_ready") {
      const packetError = Boolean(
        gateDetail?.packet_error || gateDetail?.error || gateDetail?.packet?.packet_error,
      );
      if (!gateSession.receivedRefresh || packetError) return;
      setTerminalStatus(
        gateSession.evidenceChanged ? "Pictures changed" : "Ready",
        gateSession.evidenceChanged
          ? "The pictures changed since you opened this. Look at them again in the viewer before you answer."
          : "Answer the questions below. Nothing is decided until you do.",
        gateSession.evidenceChanged ? "warn" : "ready",
      );
      requestAnimationFrame(() => {
        if (gateSession.socket !== socket || !gateSession.receivedRefresh) return;
        gateSession.inputReady = true;
        scheduleTerminalFit();
        gateSession.terminal.focus();
      });
      return;
    }
    if (message.type === "status") {
      if (message.signer === "exited") gateSession.inputReady = false;
      setTerminalStatus(
        message.signer === "exited" ? "Finished" : "Signing",
        message.exit_code == null ? "The signing program is running." : message.exit_code ? `The signing program stopped with an error (code ${message.exit_code}).` : "The signing program finished.",
        message.exit_code ? "error" : "ready",
      );
      return;
    }
    if (message.type === "lifecycle") {
      setTerminalStatus("Signing", message.message || message.status || "The signing program is running.", "ready");
      return;
    }
    if (message.type === "bye") {
      gateSession.inputReady = false;
      setTerminalStatus("Done", message.reason ? `Signing closed: ${message.reason}.` : "Signing is done.", "");
    }
  });
  socket.addEventListener("error", () => {
    if (gateSession.socket === socket) setTerminalStatus("Connection problem", "Couldn't reach the signing program. Try again.", "error");
  });
  socket.addEventListener("close", (event) => {
    if (gateSession.socket !== socket) return;
    gateSession.inputReady = false;
    const [label, message, tone] = websocketCloseMessage(event.code, event.reason);
    setTerminalStatus(label, message, tone);
    gateSession.socket = null;
    refresh().catch(() => {});
  });
}

async function copyCommand(command) {
  try {
    await navigator.clipboard.writeText(command);
    copyStatus = "Command copied.";
  } catch {
    copyStatus = "Copy failed. Select the command text manually.";
  }
  const status = document.querySelector("[data-gate-copy-status]");
  if (status) status.textContent = copyStatus;
}

// ---------------------------------------------------------------------------
// The cutting room: trim bin (cast and places), viewer, log
// ---------------------------------------------------------------------------

// Machine text (hashes, receipt ids, config digests) never reaches the main
// view. The full record stays one click away under "Everything in this request".
const ICONS = {
  play: '<svg class="ic solid" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 1.8v8.4L10 6z"/></svg>',
  pause: '<svg class="ic solid" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 2h2.4v8H2.5zM7.1 2h2.4v8H7.1z"/></svg>',
  expand: '<svg class="ic" viewBox="0 0 12 12" aria-hidden="true"><path d="M7 1.5h3.5V5M5 10.5H1.5V7M10.5 1.5 6.8 5.2M1.5 10.5l3.7-3.7"/></svg>',
  star: '<svg class="ic solid" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 .9l1.5 3.3 3.6.4-2.7 2.4.8 3.6L6 8.8l-3.2 1.8.8-3.6L.9 4.6l3.6-.4z"/></svg>',
};
function icon(name) {
  const span = document.createElement("span");
  span.innerHTML = ICONS[name];
  return span.firstChild;
}

function plain(text) {
  return String(text || "")
    .replace(/\b(?:sha256[:=]\s*)?[0-9a-f]{16,}\b/gi, "")
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, "")
    .replace(/\b(?:receipt|hash|digest|config_sha256|look_hash)\s*[:=]?\s*(?=[,;)\s]|$)/gi, "")
    .replace(/\(\s*[,;]?\s*\)/g, "")
    .replace(/\s+([,.;:)])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function titleCase(id) {
  return String(id || "").split(/[-_]/).filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

const GATE_WORDS = {
  look_lock: "Lock a look",
  headshot: "Pick a headshot",
  headshot_grandfather: "Confirm an existing headshot",
  reference_import: "Import a reference image",
  sheet: "Approve a character sheet",
  qc_override: "Overrule a quality check",
  config: "Approve the production plan",
  pipeline_migration: "Move to a newer pipeline",
};
const gateWords = (kind) => GATE_WORDS[kind] || titleCase(kind);

const SLOT_OF_KIND = {
  look_lock: "look",
  headshot: "headshot", headshot_grandfather: "headshot", reference_import: "headshot",
  sheet: "sheet", qc_override: "sheet",
};
const FRAME_ROLES = [
  ["hero", "Headshot"],
  ["turnaround", "Turnaround"],
  ["expressions", "Expressions"],
];

function entityKeyOf(row) {
  const scope = String(row.scope || "");
  const m = scope.match(/^(character|location):(.+)$/);
  if (m) return `${m[1]}:${m[2]}`;
  // Older requests carried no scope; they always named a character.
  if (!scope && SLOT_OF_KIND[row.kind] && row.entity_id && !/^[0-9a-f]{16,}$/i.test(row.entity_id)) {
    return `character:${row.entity_id}`;
  }
  return null;
}

// Every character and place the film knows about, each with the same three
// slots (look, headshot, sheet), filled from what is on disk.
function buildRoster(s) {
  const a = s.artifacts || {};
  const canonPacket = a.canon_packet || {};
  const looksPacket = (a.look_packet && a.look_packet.looks) || [];
  const gates = s.gates || {};
  const map = new Map();
  const add = (kind, id, name) => {
    if (!id) return null;
    const key = `${kind}:${id}`;
    if (!map.has(key)) {
      map.set(key, {
        key, kind, id, name: name || titleCase(id),
        frames: [], look: null, lookSpec: null, pending: [], history: [],
      });
    } else if (name) {
      map.get(key).name = name;
    }
    return map.get(key);
  };
  for (const c of canonPacket.characters || []) add("character", c.id, c.name);
  for (const l of canonPacket.locations || []) add("location", l.id, l.name);
  for (const look of gates.looks || []) {
    const e = add(look.entity_kind === "location" ? "location" : "character", look.entity);
    if (e) e.look = look;
  }
  for (const entry of looksPacket) {
    const e = add(entry.entity_kind === "location" ? "location" : "character", entry.entity_id);
    if (e) e.lookSpec = entry.look_spec || null;
  }
  for (const c of gates.canon || []) {
    const e = add("character", c.entity);
    if (e) e.frames.push(c);
  }
  for (const row of gates.requests || []) {
    const key = entityKeyOf(row);
    if (!key) continue;
    const [kind, id] = key.split(/:(.+)/);
    const e = add(kind, id);
    if (!e) continue;
    if (row.state === "pending") e.pending.push(row);
    else e.history.push(row);
  }
  for (const e of map.values()) {
    e.frames.sort((x, y) => FRAME_ROLES.findIndex(([r]) => r === x.role) - FRAME_ROLES.findIndex(([r]) => r === y.role));
    const has = (role) => e.frames.some((f) => f.role === role);
    const waiting = (slot) => e.pending.some((r) => SLOT_OF_KIND[r.kind] === slot);
    const lastDeclined = (slot) => {
      const rows = e.history.filter((r) => SLOT_OF_KIND[r.kind] === slot);
      rows.sort((x, y) => (y.mtime || 0) - (x.mtime || 0));
      return rows[0] && rows[0].state === "declined";
    };
    const slot = (slotName, done) => (waiting(slotName) ? "waiting" : done ? "done" : lastDeclined(slotName) ? "declined" : "none");
    e.slots = {
      look: slot("look", Boolean(e.look)),
      headshot: slot("headshot", has("hero")),
      sheet: slot("sheet", has("turnaround") || has("expressions")),
    };
    e.active = Boolean(e.look || e.frames.length || e.pending.length || e.history.length);
  }
  const ordered = [...map.values()];
  ordered.sort((x, y) => (y.pending.length > 0) - (x.pending.length > 0) || (y.active - x.active));
  return ordered;
}

function hasRoster(s) {
  return hasAuthoredGates(s) && buildRoster(s).length > 0;
}

function selectEntity(key) {
  selectedEntity = key;
  selectedGateId = null;
  gateDetail = null;
  gateDetailState = "idle";
  selectedFrame = 0;
  render();
  focusViewer();
}

function focusViewer() {
  requestAnimationFrame(() => {
    const viewer = document.getElementById("viewer");
    if (viewer) { viewer.scrollTop = 0; viewer.focus({ preventScroll: true }); }
  });
}

const SLOT_LABEL = { look: "Look", headshot: "Headshot", sheet: "Sheet" };
const SLOT_STATE = { done: "done", waiting: "waiting for you", declined: "last one declined", none: "not made yet" };

function renderBin(s, roster) {
  const bin = el("nav", { class: "bin", "aria-label": "Characters and places" });
  const groups = [["character", "Characters"], ["location", "Places"]];
  for (const [kind, label] of groups) {
    const members = roster.filter((e) => e.kind === kind);
    if (!members.length) continue;
    const list = el("ul", { class: "bin-list" });
    for (const e of members) {
      const hero = e.frames.find((f) => f.role === "hero") || e.frames[0];
      const face = el("span", { class: "clip-face", "aria-hidden": "true" },
        hero
          ? el("img", { src: thumbURL(s.project_id, hero.object_rel, 320), alt: "", loading: "lazy" })
          : el("span", { class: "initial" }, e.name.charAt(0)));
      const slots = el("span", { class: "slots", "aria-hidden": "true" },
        ["look", "headshot", "sheet"].map((k) => el("span", { class: `slot ${e.slots[k]}` })));
      const described = ["look", "headshot", "sheet"].map((k) => `${SLOT_LABEL[k]} ${SLOT_STATE[e.slots[k]]}`).join(", ");
      const current = selectedEntity === e.key && !selectedGateId;
      list.append(el("li", {}, el("button", {
        class: `clip${e.active ? "" : " quiet"}`,
        type: "button",
        "aria-current": current ? "true" : null,
        "aria-label": `${e.name}. ${described}${e.pending.length ? ". Needs you" : ""}`,
        onclick: () => selectEntity(e.key),
      }, face, el("span", { class: "clip-name" }, e.name), slots,
        e.pending.length ? el("span", { class: "needs-mark", "aria-hidden": "true" }, "You") : null)));
    }
    bin.append(el("div", { class: "bin-group" }, el("span", { class: "plate-label" }, label), list));
  }
  bin.append(el("div", { class: "bin-legend", "aria-hidden": "true" },
    el("div", {}, el("span", { class: "slot done" }), "Done"),
    el("div", {}, el("span", { class: "slot waiting" }), "Waiting for you"),
    el("div", {}, el("span", { class: "slot" }), "Not made yet"),
    el("div", {}, "Tabs read look, headshot, sheet")));
  return bin;
}

function tape(state, text) {
  return el("span", { class: `tape ${state}` }, text);
}

function renderScreen(s, frame, fallback) {
  if (!frame) return fallback;
  const screen = el("figure", { class: "screen" });
  if (frame.missing) {
    screen.classList.add("blank");
    screen.append(el("div", {}, el("b", {}, "Picture missing"), el("p", {}, frame.missing)));
    return screen;
  }
  // Register the picture only when it changes; live refreshes must not
  // replay the motion on a frame Ben is already studying.
  const fresh = renderScreen.last !== frame.path;
  renderScreen.last = frame.path;
  screen.append(
    el("img", { src: mediaURL(s.project_id, frame.path), alt: frame.alt || frame.label, class: fresh ? null : "settled" }),
    el("figcaption", { class: "frame-label" }, frame.label),
    el("a", { class: "open-full", href: mediaURL(s.project_id, frame.path), target: "_blank", rel: "noreferrer" }, "Open full size"));
  return screen;
}

function renderFrameStrip(s, frames, labelPrefix) {
  if (frames.length < 2) return null;
  const strip = el("div", { class: "strip", role: "list", "aria-label": `${labelPrefix} frames` });
  frames.forEach((frame, index) => {
    const current = index === selectedFrame;
    const pic = el("span", { class: "frame-pic" },
      frame.missing
        ? el("span", { class: "missing" }, "Missing")
        : el("img", { src: thumbURL(s.project_id, frame.path, 320), alt: "", loading: "lazy" }),
      el("span", { class: "leader", "aria-hidden": "true" }));
    strip.append(el("div", { role: "listitem" }, el("button", {
      class: `frame ${frame.state || ""}`.trim(),
      type: "button",
      "aria-current": current ? "true" : null,
      "aria-label": `Show ${frame.label} in the viewer`,
      onclick: () => { selectedFrame = index; render(); },
    }, pic, el("span", { class: "frame-cap" }, frame.label))));
  });
  // Advance the strip sideways only; never scroll the viewer itself.
  requestAnimationFrame(() => {
    const active = strip.querySelector('.frame[aria-current="true"]');
    if (!active) return;
    const target = active.offsetLeft - (strip.clientWidth - active.offsetWidth) / 2;
    strip.scrollTo({ left: Math.max(0, target), behavior: "smooth" });
  });
  return strip;
}

function historyList(rows) {
  if (!rows.length) return null;
  const sorted = [...rows].sort((x, y) => (y.mtime || 0) - (x.mtime || 0));
  return el("ul", { class: "history" }, sorted.slice(0, 8).map((row) => el("li", { class: row.state },
    el("span", { class: "dot", "aria-hidden": "true" }),
    el("span", {},
      el("button", { class: "text linkish", type: "button", onclick: () => selectGate(row.request_id), style: "all:unset;cursor:pointer" },
        `${gateWords(row.kind)}: ${row.state === "done" ? "signed" : row.state}`),
      el("small", {}, fmtGateTime(row.mtime))))));
}

function renderEntityViewer(s, e) {
  const frames = e.frames.map((f) => {
    const role = FRAME_ROLES.find(([r]) => r === f.role);
    return { path: f.object_rel, label: role ? role[1] : titleCase(f.role), state: "done", alt: `${e.name}, ${role ? role[1] : f.role}` };
  });
  if (selectedFrame >= frames.length) selectedFrame = 0;
  const nextStep = e.slots.look !== "done"
    ? "No look is locked yet. Once you promote a look in WriterOS and it's locked here, the headshot comes next."
    : e.slots.headshot !== "done"
      ? "The look is locked. The headshot comes next."
      : "The headshot is done. The character sheet comes next.";
  const blank = el("figure", { class: "screen blank" },
    el("div", {}, el("b", {}, "Nothing on film yet"), el("p", {}, nextStep)));

  const lookText = e.lookSpec && (e.lookSpec.prompt_safe_description || e.lookSpec.description);
  const source = e.look && e.look.source_ref && typeof e.look.source_ref === "object"
    ? "From WriterOS"
    : e.look ? "From a Story-drive ticket" : null;

  const head = el("header", { class: "viewer-head" },
    el("div", {},
      el("h2", { id: "viewer-heading" }, e.name),
      el("div", { class: "what" }, e.kind === "location" ? "Place" : "Character")),
    el("div", { class: "tapes" },
      ["look", "headshot", "sheet"].map((k) => tape(e.slots[k], SLOT_LABEL[k]))));

  const needs = e.pending.length ? el("section", { class: "notice", style: "margin-top:24px" },
    el("span", {},
      el("b", {}, `${e.name} needs you. `),
      e.pending.map((row, i) => [i ? " " : "", el("button", {
        class: "quiet-btn", type: "button", style: "margin:0 0 0 6px", onclick: () => selectGate(row.request_id),
      }, gateWords(row.kind))]))) : null;

  return el("div", { class: "viewer-inner" },
    head,
    renderScreen(s, frames[selectedFrame], blank),
    renderFrameStrip(s, frames, e.name),
    needs,
    el("div", { class: "notes" },
      el("section", {},
        el("h3", {}, "The look"),
        lookText ? el("p", {}, lookText) : el("p", {}, "No look written yet."),
        source ? el("p", { class: "source" }, source) : null),
      el("section", {},
        el("h3", {}, "What happened"),
        historyList(e.history) || el("p", {}, "Nothing signed yet."))));
}

function gateFrames(packet) {
  const frames = [];
  const push = (visual, label, state) => {
    if (!visual) return;
    frames.push({
      path: visual.path,
      label,
      state,
      missing: visual.error || (!visual.path ? "This picture could not be found." : null),
      alt: label,
    });
  };
  if (!packet || typeof packet !== "object") return frames;
  for (const c of packet.candidates || []) {
    if (c && c.visual) push(c.visual, c.number != null ? `Candidate ${c.number}` : humanize(c.visual.label || "candidate"), "waiting");
  }
  for (const v of packet.visuals || []) if (v) push(v, titleCase(v.label || "picture"), "");
  if (packet.visual) push(packet.visual, titleCase(packet.visual.label || "picture"), "");
  let n = 0;
  for (const row of packet.field_rows || []) {
    if (!row || !row.visual) continue;
    n += 1;
    push(row.visual, `Attempt ${n}`, "declined");
  }
  return frames;
}

function gateActions(s, summary, stale, packetError) {
  const lease = s.gates.run_lease || {};
  const matchingOpen = openGateSessions.find((session) => session.request_id === selectedGateId);
  const otherOpen = openGateSessions.find((session) => session.request_id !== selectedGateId);
  const socketOpen = gateSession.socket
    && [WebSocket.CONNECTING, WebSocket.OPEN].includes(gateSession.socket.readyState);
  const actionablePacket = !stale && !packetError;
  const actionable = actionablePacket && !lease.held && !otherOpen && !socketOpen;
  const reconnectable = Boolean(matchingOpen && !socketOpen && actionablePacket && !otherOpen);

  let button;
  if (reconnectable) {
    button = el("button", { type: "button", class: "gate-primary", onclick: () => startSigning(selectedGateId) }, "Reconnect");
  } else {
    button = el("button", {
      type: "button",
      class: "gate-primary",
      disabled: actionable ? null : "",
      onclick: () => startSigning(selectedGateId),
    }, socketOpen && gateSession.requestId === selectedGateId ? "Signing now" : matchingOpen ? "Can't reconnect" : "Start signing");
  }
  let blocker = null;
  if (otherOpen) blocker = "You're already signing something else. Finish that first.";
  else if (lease.held && !matchingOpen) blocker = `A production run is working on this film right now${lease.started ? ` (since ${fmtGateTime(lease.started)})` : ""}. Signing opens when it stops.`;
  else if (stale) blocker = "This is already decided. What you see is the record.";
  else if (packetError) blocker = "Signing is off until every picture in this request can be shown.";
  else if (reconnectable) blocker = "A signing session for this is still open. Reconnect to pick up where you left off.";
  else if (socketOpen) blocker = "Answer the questions in the signing panel on the right.";

  return el("section", { class: "sign-bay", "aria-label": "Sign" },
    el("p", {}, stale
      ? "Nothing to sign here."
      : "Signing opens the signing program in the panel on the right. It asks you one question at a time: approve or decline, which candidate, and why. Nothing is decided until you answer."),
    stale ? null : button,
    blocker ? el("p", { class: "blocker", role: "status" }, blocker) : null);
}

function renderGateViewer(s) {
  const summary = gateRowById(s, selectedGateId);
  const wrap = (...children) => el("section", {
    class: "viewer-inner gate-detail",
    id: "gate-detail",
    tabindex: "-1",
    "aria-labelledby": "gate-detail-heading",
  }, ...children);
  if (gateDetailState === "loading") {
    return wrap(el("h2", { id: "gate-detail-heading", class: "sr-only" }, "Loading"),
      el("figure", { class: "screen blank", role: "status", "aria-busy": "true" }, el("div", {}, el("b", {}, "Loading the pictures…"))));
  }
  if (gateDetailState === "error") {
    return wrap(el("h2", { id: "gate-detail-heading", class: "sr-only" }, "Could not load"),
      el("div", { class: "alert", role: "alert" }, el("b", {}, "Couldn't load this request"), el("span", {}, gateDetailError)));
  }
  if (!gateDetail) {
    return wrap(el("h2", { id: "gate-detail-heading", class: "sr-only" }, "Empty"), el("p", { class: "gate-empty" }, "Nothing came back for this request."));
  }
  const header = summary || gateDetail.request || {};
  const stale = !summary || summary.state !== "pending" || gateDetail.state !== "pending";
  const packetError = Boolean(gateDetail.error || (gateDetail.packet && gateDetail.packet.packet_error));
  let frames = gateFrames(gateDetail.packet);
  const key = entityKeyOf(header);
  const entity = key ? buildRoster(s).find((e) => e.key === key) : null;
  const who = key ? (entity && entity.name) || titleCase(key.split(":")[1]) : null;
  // Decided requests keep no pictures of their own; show what is on film now.
  if (!frames.length && stale && entity) {
    const roles = { headshot: ["hero"], sheet: ["turnaround", "expressions"] }[SLOT_OF_KIND[header.kind]] || [];
    frames = entity.frames.filter((f) => roles.includes(f.role)).map((f) => {
      const role = FRAME_ROLES.find(([r]) => r === f.role);
      return { path: f.object_rel, label: `On film now: ${role ? role[1] : titleCase(f.role)}`, state: "done", alt: `${entity.name}, ${role ? role[1] : f.role}` };
    });
  }
  if (selectedFrame >= frames.length) selectedFrame = 0;
  const stateTape = header.state === "pending" || (!summary && gateDetail.state === "pending")
    ? tape("waiting", "Needs you")
    : header.state === "done" ? tape("done", "Signed")
    : header.state === "declined" ? tape("declined", "Declined")
    : tape("", humanize(header.state || "unknown"));
  const title = who ? `${gateWords(header.kind)}: ${who}` : gateWords(header.kind);
  // Swap quoted machine ids ('sebastians-home') for the names Ben knows.
  const roster = buildRoster(s);
  const named = plain(header.summary).replace(/'([a-z0-9][a-z0-9-]*)'/g, (_, id) =>
    (roster.find((e) => e.id === id) || {}).name || titleCase(id));
  const firstSentence = (named.match(/^.*?[.!?](?=\s|$)/) || [named])[0];

  const facts = [
    ["what", gateWords(header.kind)], ["kind", header.kind], ["stage", header.stage], ["scope", header.scope],
    ["entity", header.entity_id], ["snapshot", fmtGateTime(gateDetail.snapshot_at)],
  ];
  const command = summary && summary.next_command;
  const more = el("details", { class: "more" },
    el("summary", {}, "Everything in this request"),
    gateDetail.error ? el("div", { class: "gate-packet-error", role: "alert" }, el("b", {}, "Lookup error"), el("span", {}, gateDetail.error)) : null,
    header.summary ? textEvidence("the full request", header.summary) : null,
    el("dl", { class: "gate-meta-strip gate-detail-facts" }, facts.map(([label, value]) =>
      el("div", { class: "gate-meta-chip" }, el("dt", {}, label), el("dd", {}, value || "—")))),
    gateDetail.packet
      ? renderPacketEvidence(s, gateDetail.packet, { visuals: false })
      : el("div", {},
        textEvidence("request", gateDetail.request || {}),
        gateDetail.approval_receipt_id ? textEvidence("approval receipt id", gateDetail.approval_receipt_id) : null,
        gateDetail.declined_note ? textEvidence("declined note", gateDetail.declined_note) : null,
        gateDetail.unverified_ledger_row ? textEvidence(gateDetail.ledger_label || "unverified ledger row", gateDetail.unverified_ledger_row) : null),
    command ? el("div", { class: "gate-command" },
      el("p", { class: "source" }, "Prefer your own Terminal? This command does the same signing."),
      el("code", {}, command),
      el("button", { type: "button", onclick: () => copyCommand(command) }, "Copy command"),
      el("p", { "aria-live": "polite", "data-gate-copy-status": "" }, copyStatus)) : null);

  const blank = el("figure", { class: `screen blank${stale ? " band" : ""}` },
    el("div", {}, el("b", {}, "No pictures in this request"),
      el("p", {}, stale ? "Decided requests keep their record, not their pictures. The details are below." : "Read the details below before you sign.")));

  // Gate evidence pictures keep their stable hooks for the visual checks.
  const evidence = frames.length ? el("div", { class: "sr-only" },
    frames.map((f, i) => el("figure", { class: "gate-evidence-card" },
      f.path && !f.missing ? el("img", { class: "gate-evidence-image", src: thumbURL(s.project_id, f.path, 320), alt: "", loading: "lazy" }) : null,
      el("figcaption", {}, el("b", {}, gateDetail.packet && gateDetail.packet.candidates && gateDetail.packet.candidates[i] && gateDetail.packet.candidates[i].number != null ? `[${gateDetail.packet.candidates[i].number}]` : f.label))))) : null;

  return wrap(
    el("header", { class: "viewer-head" },
      el("div", {},
        el("h2", { id: "gate-detail-heading", class: title.length > 34 ? "long" : "", tabindex: "-1" }, title),
        firstSentence && firstSentence !== title ? el("p", { class: "request-summary" }, shortText(firstSentence, 200)) : null),
      el("div", { class: "tapes" }, stateTape)),
    packetError ? el("div", { class: "alert", role: "alert" }, el("b", {}, "Some evidence is missing or changed"),
      el("span", {}, gateDetail.packet && gateDetail.packet.error ? gateDetail.packet.error : "Signing stays off until it's complete.")) : null,
    renderScreen(s, frames[selectedFrame], blank),
    renderFrameStrip(s, frames, "Request"),
    evidence,
    gateActions(s, summary, stale, packetError),
    more);
}

function logLine(row, roster) {
  const key = entityKeyOf(row);
  const who = key ? ((roster.find((e) => e.key === key) || {}).name || titleCase(key.split(":")[1])) : null;
  return who ? `${who}: ${gateWords(row.kind).toLowerCase()}` : gateWords(row.kind);
}

function renderLog(s, roster) {
  const needsBox = el("div", {});   // "Needs you", above the conversation
  const history = el("div", {});    // the log, decisions and machine room, below it
  if (hasAuthoredGates(s)) {
    const pending = pendingGates(s);
    const needs = el("section", { class: "log-section", id: "gates", "aria-labelledby": "needs-heading" },
      el("h2", { id: "needs-heading" }, "Needs you", pending.length ? el("span", { class: "count" }, String(pending.length)) : null));
    if (!pending.length) {
      needs.append(el("p", { class: "log-quiet" }, "Nothing is waiting on you."));
    }
    for (const row of pending) {
      const key = entityKeyOf(row);
      const who = key ? (roster.find((e) => e.key === key) || {}).name : null;
      needs.append(el("button", {
        class: "need",
        type: "button",
        "aria-current": selectedGateId === row.request_id ? "true" : null,
        "aria-label": `Inspect ${row.kind} gate ${row.request_id}`,
        onclick: () => { selectedEntity = key; selectGate(row.request_id); },
      },
        el("span", { class: "what" }, gateWords(row.kind)),
        el("span", { class: "text" }, plain(row.summary) || gateWords(row.kind)),
        who ? el("span", { class: "who" }, who) : null));
    }
    if (s.gates.error) needs.append(el("div", { class: "alert", role: "alert" }, el("b", {}, "Couldn't read the approvals"), el("span", {}, s.gates.error)));
    needsBox.append(needs);

    const done = (s.gates.requests || []).filter((row) => row.state !== "pending")
      .sort((x, y) => (y.mtime || 0) - (x.mtime || 0));
    if (done.length) {
      const shown = logShowAll ? done : done.slice(0, 8);
      const list = el("ol", { class: "entries" }, shown.map((row) => el("li", { class: `entry ${row.state}` },
        el("span", { class: "mark", "aria-hidden": "true" }),
        el("div", {},
          el("button", {
            class: "text",
            type: "button",
            "aria-label": `Inspect ${row.kind} gate ${row.request_id}`,
            onclick: () => { selectedEntity = entityKeyOf(row); selectGate(row.request_id); },
          }, logLine(row, roster)),
          el("small", {}, `${row.state === "done" ? "Signed" : row.state === "declined" ? "Declined" : "Dropped"} · ${fmtGateTime(row.mtime)}`)))));
      const section = el("section", { class: "log-section", "aria-labelledby": "log-heading" },
        el("h2", { id: "log-heading" }, "The log", el("span", { class: "count" }, String(s.gates.total_requests || done.length))),
        list);
      if (done.length > 8) {
        section.append(el("button", {
          class: "quiet-btn show-all", type: "button",
          onclick: () => { logShowAll = !logShowAll; render(); },
        }, logShowAll ? "Show fewer" : `Show all ${done.length}`));
      }
      history.append(section);
    }
  } else {
    const awaiting = s.stages.find((x) => x.status === "awaiting_human");
    needsBox.append(el("section", { class: "log-section", "aria-labelledby": "needs-heading" },
      el("h2", { id: "needs-heading" }, "Needs you"),
      awaiting
        ? el("p", { class: "log-quiet", style: "color:var(--ink)" }, `${titleCase(awaiting.name)} is ready for your review. It's open in the viewer.`)
        : el("p", { class: "log-quiet" }, "Nothing is waiting on you.")));
  }
  const decisions = renderDecisions(s);
  if (decisions) history.append(decisions);
  const activity = renderActivity(s);
  if (activity) history.append(activity);
  return { needs: needsBox, history };
}

// ---------------------------------------------------------------------------
// replay — scrub a completed run from its timestamps
// ---------------------------------------------------------------------------

// Python writers emit tz-aware UTC isoformat, but treat tz-naive strings as
// UTC too — mixing local-parsed and UTC-parsed timestamps would skew replay
// ordering by the user's UTC offset.
const ts = (iso) => {
  if (!iso) return null;
  let s = String(iso);
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += "Z";
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : null;
};

function replayBounds(s) {
  const moments = [];
  for (const st of s.stages) {
    for (const h of st.history_entries || []) {
      const t = ts(h.timestamp);
      if (t) moments.push(t);
    }
  }
  for (const ev of s.events || []) {
    const t = ts(ev.ts);
    if (t) moments.push(t);
  }
  if (moments.length < 2) return null;
  return { t0: Math.min(...moments), t1: Math.max(...moments) };
}

function stateAt(s, T) {
  const view = structuredClone(s);
  for (const st of view.stages) {
    const past = (st.history_entries || []).filter((h) => ts(h.timestamp) != null && ts(h.timestamp) <= T);
    if (!past.length) {
      st.status = "pending"; st.review = null; st.timestamp = null;
      st.gate_skipped = false; st.partial_progress = null;
    } else {
      const cur = past[past.length - 1];
      st.status = cur.status || "pending";
      st.timestamp = cur.timestamp;
    }
  }
  view.events = (view.events || []).filter((ev) => ts(ev.ts) != null && ts(ev.ts) <= T);

  // Storyboard: visuals appear as their scene finishes (events) or when the
  // assets stage has completed as of T (legacy runs without events).
  if (view.storyboard) {
    const assetsStage = view.stages.find((x) => x.name === "assets");
    const assetsDone = assetsStage && assetsStage.status === "completed";
    const finished = new Set();
    const startedNow = new Map();
    for (const ev of view.events) {
      if (!ev.scene_id) continue;
      if (ev.event === "finish") { finished.add(ev.scene_id); startedNow.delete(ev.scene_id); }
      else if (ev.event === "start") startedNow.set(ev.scene_id, ev);
      else if (ev.event === "error") startedNow.delete(ev.scene_id);
    }
    const scenePlanStage = view.stages.find((x) => x.name === "scene_plan");
    const scenePlanDone = scenePlanStage && ["completed", "awaiting_human"].includes(scenePlanStage.status);
    if (!scenePlanDone) {
      view.storyboard = null;
    } else {
      for (const card of view.storyboard.scenes) {
        const visible = assetsDone || finished.has(card.id);
        if (!visible) { card.visual = null; card.takes = []; card.audio = []; }
        card.generating = startedNow.has(card.id);
        card.generating_tool = (startedNow.get(card.id) || {}).tool;
      }
    }
  }
  // Final artifacts hide until their stage happened — for every project
  // shape, storyboard or not (a degraded run must not show the finished
  // movie before its stages ran).
  const scriptStage = view.stages.find((x) => x.name === "script");
  if (!(scriptStage && ["completed", "awaiting_human"].includes(scriptStage.status))) {
    delete view.artifacts.script;
  }
  const composeStage = view.stages.find((x) => x.name === "compose");
  if (!(composeStage && composeStage.status === "completed")) {
    view.media.renders = [];
  }
  return view;
}

function renderReplayBar(s) {
  const bounds = replayBounds(s);
  if (!bounds) return null;
  if (!replay) {
    // collapsed: just the entry button
    return el("div", { class: "replay-bar", style: "justify-content:flex-end" },
      
      el("button", { class: "rp-btn", type: "button", onclick: startReplay }, icon("play"), "Replay the run"));
  }
  const pos = (replay.t - replay.t0) / Math.max(1, replay.t1 - replay.t0);
  const timeLabel = el("span", { class: "rp-time" },
    new Date(replay.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
  const setT = (value) => {
    replay.t = replay.t0 + (Number(value) / 1000) * (replay.t1 - replay.t0);
    timeLabel.textContent = new Date(replay.t)
      .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };
  return el("div", { class: "replay-bar" },
    el("button", { class: "rp-btn", type: "button", "aria-label": replay.playing ? "Pause" : "Play", onclick: toggleReplayPlay }, icon(replay.playing ? "pause" : "play")),
    el("input", {
      type: "range", min: "0", max: "1000", value: String(Math.round(pos * 1000)),
      // A full render() would destroy this slider mid-drag: while dragging,
      // only pause + track the time label; re-render the board on release.
      onpointerdown: () => { replay.playing = false; },
      oninput: (e) => setT(e.target.value),
      onchange: (e) => { setT(e.target.value); render(); },
    }),
    timeLabel,
    el("button", { class: "rp-btn", type: "button", onclick: stopReplay }, "Back to live"),
  );
}

let replayTimer = null;

function startReplay() {
  const bounds = replayBounds(state);
  if (!bounds) return;
  replay = { ...bounds, t: bounds.t0, playing: true };
  document.body.classList.add("replaying");
  scheduleTick();
  render();
}

function stopReplay() {
  replay = null;
  clearTimeout(replayTimer);
  document.body.classList.remove("replaying");
  render();
}

function toggleReplayPlay() {
  if (!replay) return;
  replay.playing = !replay.playing;
  if (replay.playing) scheduleTick();
  render();
}

function scheduleTick() {
  // Single pending tick, ever — rapid pause/play must not stack chains.
  clearTimeout(replayTimer);
  replayTimer = setTimeout(tickReplay, 100);
}

function tickReplay() {
  if (!replay || !replay.playing) return;
  // A full run replays in ~20 seconds regardless of real duration
  // (10 renders/second — full re-render per tick, keep it modest).
  const step = (replay.t1 - replay.t0) / 200;
  replay.t = Math.min(replay.t1, replay.t + step);
  if (replay.t >= replay.t1) replay.playing = false;
  render();
  if (replay.playing) scheduleTick();
}

// ---------------------------------------------------------------------------
// page assembly
// ---------------------------------------------------------------------------

function render() {
  if (!state) return;
  const s = replay ? stateAt(state, replay.t) : state;
  const authoredGates = hasAuthoredGates(s);
  if (hasAuthoredGates(state) && gateSession.socket && gateSession.requestId) {
    const sessionRow = gateRowById(state, gateSession.requestId);
    if (!sessionRow || sessionRow.state !== "pending") {
      gateSession.inputReady = false;
      setTerminalStatus(
        "Already decided",
        "This changed while signing was open. What's on screen is kept; typing is off.",
        "warn",
      );
    }
  }
  document.title = `${s.title} · Front Lot`;
  document.body.classList.toggle("first", firstPaint);
  firstPaint = false;

  // The signing bay shows only while signing is open or resumable.
  const signingOpen = Boolean(gateSession.socket) || openGateSessions.length > 0 || terminalShell.dataset.used === "1";
  terminalShell.hidden = !(authoredGates && signingOpen);

  const roster = authoredGates ? buildRoster(s) : [];
  const withBin = roster.length > 0;
  if (withBin && !selectedGateId && !roster.some((e) => e.key === selectedEntity)) {
    const pending = pendingGates(s)[0];
    const first = roster.find((e) => e.frames.length) || roster[0];
    selectedEntity = (pending && entityKeyOf(pending)) || first.key;
  }
  tableEl.classList.toggle("no-bin", !withBin);

  app.innerHTML = "";
  app.append(renderBar(s));
  if (withBin) app.append(renderBin(s, roster));

  const viewer = el("main", { class: "viewer", id: "viewer", tabindex: "-1", "aria-label": "Viewer" });
  const drawer = renderDrawer(s);

  if (authoredGates && (selectedGateId || withBin)) {
    let inner;
    if (selectedGateId) inner = renderGateViewer(s);
    else inner = renderEntityViewer(s, roster.find((e) => e.key === selectedEntity) || roster[0]);
    if (drawer) inner.prepend(drawer);
    viewer.append(inner);
  } else {
    const inner = el("div", { class: "viewer-inner" });
    const replayBar = renderReplayBar(state);
    if (replayBar) inner.append(replayBar);
    if (drawer) inner.append(drawer);
    for (const section of [renderAwaitingNotice(s), renderNoState(s), renderApprovalReview(s),
      renderScriptCard(s), renderStoryboard(s), renderFoundMedia(s), renderRenders(s)]) {
      if (section) inner.append(section);
    }
    if (inner.childNodes.length === 0) {
      inner.append(el("figure", { class: "screen blank" },
        el("div", {}, el("b", {}, "Nothing on film yet"), el("p", {}, "When this production makes something, it appears here."))));
    }
    viewer.append(inner);
  }
  app.append(viewer);

  const { needs, history } = renderLog(s, roster);
  logFeed.innerHTML = "";
  logFeed.append(needs);
  document.getElementById("history-body").replaceChildren(history);
  if (!terminalShell.hidden) scheduleTerminalFit(false);
}

// Left and right arrows step through the frame strip.
document.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  const target = event.target;
  if (target && (target.closest(".xterm") || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
  const frames = document.querySelectorAll(".strip .frame");
  if (frames.length < 2) return;
  selectedFrame = (selectedFrame + (event.key === "ArrowRight" ? 1 : -1) + frames.length) % frames.length;
  render();
});

// Defensive normalization (F-02): the server contract guarantees these
// fields, but a sparse/legacy payload must degrade, never crash the board.
function normalize(s) {
  s.pipeline = s.pipeline || { pipeline_type: "unknown", stages: [], known: false };
  s.stages = Array.isArray(s.stages) ? s.stages : [];
  for (const stage of s.stages) {
    stage.produces = Array.isArray(stage.produces) ? stage.produces : [];
  }
  s.artifacts = s.artifacts || {};
  s.media = s.media || {};
  s.media.renders = Array.isArray(s.media.renders) ? s.media.renders : [];
  s.media.snapshots = Array.isArray(s.media.snapshots) ? s.media.snapshots : [];
  s.media.music = Array.isArray(s.media.music) ? s.media.music : [];
  s.events = Array.isArray(s.events) ? s.events : [];
  if (s.storyboard && Array.isArray(s.storyboard.scenes)) {
    for (const c of s.storyboard.scenes) {
      c.takes = Array.isArray(c.takes) ? c.takes : [];
      c.audio = Array.isArray(c.audio) ? c.audio : [];
      c.required_assets = Array.isArray(c.required_assets) ? c.required_assets : [];
    }
  } else {
    s.storyboard = null;
  }
  return s;
}

async function refresh() {
  const generation = ++refreshGeneration;
  if (refreshController) refreshController.abort();
  const controller = new AbortController();
  refreshController = controller;
  try {
    const [nextState, sessions] = await Promise.all([
      getJSON(`/api/project/${encodeURIComponent(projectId)}/state`, { signal: controller.signal }),
      getJSON(`/api/project/${encodeURIComponent(projectId)}/gate-sessions`, { signal: controller.signal })
        .catch((error) => {
          if (error && error.name === "AbortError") throw error;
          return [];
        }),
    ]);
    if (generation !== refreshGeneration || controller.signal.aborted) return;
    state = normalize(nextState);
    openGateSessions = Array.isArray(sessions) ? sessions : [];
    render();
    if (selectedGateId && gateRowById(state, selectedGateId)) {
      await selectGate(selectedGateId, { background: true });
    }
    if (hasAuthoredGates(state) && !selectedGateId && openGateSessions.length) {
      selectGate(openGateSessions[0].request_id);
    }
  } catch (error) {
    if (error && error.name === "AbortError") return;
    throw error;
  } finally {
    if (refreshController === controller) refreshController = null;
  }
}

if (!new URLSearchParams(location.search).has("static")) {
  mountSession({
    projectId,
    feedEl: document.getElementById("session-feed"),
    terminalEl: document.getElementById("session-terminal"),
    composerEl: document.getElementById("composer"),
    stateEl: document.getElementById("session-state"),
    onRunFinished: (entity) => {
      document.dispatchEvent(new CustomEvent("frontlot:run-finished", { detail: entity }));
      if (!state || !hasAuthoredGates(state)) return;
      const roster = buildRoster(state);
      const key = ["character", "location"].map((k) => `${k}:${entity}`).find((k) => roster.some((e) => e.key === k));
      if (key) selectEntity(key);
    },
  });
} else {
  document.getElementById("session").hidden = true;   // static views never start Claude
}

refresh().catch((err) => {
  app.innerHTML = "";
  tableEl.classList.add("no-bin", "no-log");
  document.getElementById("log").hidden = true;
  app.append(el("div", { class: "viewer" }, el("div", { class: "empty", style: "margin-top:80px" },
    el("div", { class: "big" }, "PROJECT NOT FOUND"),
    el("p", {}, "This film isn't in Front Lot. "), el("a", { href: "/" }, "Back to your films"))));
});
// ?static=1 disables the live feed (screenshots, static exports).
if (!new URLSearchParams(location.search).has("static")) {
  subscribe(`/api/project/${encodeURIComponent(projectId)}/events`, () => refresh().catch(console.error));
}
