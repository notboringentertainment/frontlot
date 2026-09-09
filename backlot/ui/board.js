// Backlot project board — renders BoardState and stays live via SSE.

import {
  CAPABILITY_TOKEN_KEY, STAGE_ICONS, el, fmtAgo, fmtClock, fmtDuration, fmtMoney,
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

const THEME_KEY = "backlot.theme";
let currentTheme = localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
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

const TERMINAL_THEME = {
  background: "#09090b",
  foreground: "#ececef",
  cursor: "#f0a83c",
  cursorAccent: "#09090b",
  selectionBackground: "rgba(240, 168, 60, .32)",
  black: "#09090b",
  red: "#e5544b",
  green: "#4fc283",
  yellow: "#f0a83c",
  blue: "#6aa1ff",
  magenta: "#bd8cff",
  cyan: "#66cbd1",
  white: "#ececef",
  brightBlack: "#5f5f68",
  brightWhite: "#ffffff",
};

function makeGateSession() {
  const TerminalClass = window.Terminal;
  const FitClass = window.FitAddon && window.FitAddon.FitAddon;
  const terminal = TerminalClass ? new TerminalClass({
    convertEol: true,
    cursorBlink: true,
    cursorStyle: "block",
    fontFamily: "'JetBrains Mono', ui-monospace, monospace",
    fontSize: 14,
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
    if (input) input.setAttribute("aria-label", "Gate signing terminal input");
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

function applyTheme(theme) {
  currentTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = currentTheme;
  document.querySelector('meta[name="theme-color"]').content = currentTheme === "light" ? "#efe4c9" : "#0a0a0c";
  localStorage.setItem(THEME_KEY, currentTheme);
}

function renderThemeToggle() {
  const next = currentTheme === "light" ? "dark" : "light";
  return el("button", {
    class: "theme-toggle",
    type: "button",
    title: `Switch to ${next} theme`,
    "aria-label": `Switch to ${next} theme`,
    "aria-pressed": currentTheme === "light" ? "true" : "false",
    onclick: () => {
      applyTheme(next);
      render();
    },
  }, el("span", { class: "theme-toggle-icon", "aria-hidden": "true" }, currentTheme === "light" ? "☾" : "☀"));
}

applyTheme(currentTheme);

// ---------------------------------------------------------------------------
// header slate
// ---------------------------------------------------------------------------

function renderSlate(s) {
  const board = s.storyboard;
  const chips = [
    el("span", { class: "chip" }, `${s.pipeline.pipeline_type} pipeline`),
    board && board.total_duration_seconds
      ? el("span", { class: "chip" }, `${board.scenes.length} scenes · ${fmtDuration(board.total_duration_seconds)}`)
      : null,
    s.style_playbook ? el("span", { class: "chip" }, s.style_playbook) : null,
  ];

  const awaiting = s.stages.find((x) => x.status === "awaiting_human");
  const inProgress = s.stages.find((x) => x.status === "in_progress");
  const stalled = s.stages.find((x) => x.stalled);
  let liveEl;
  if (awaiting) {
    liveEl = el("span", { class: "live" }, el("span", { class: "dot" }), "◈ AWAITING YOU");
  } else if (stalled) {
    liveEl = el("span", { class: "live", style: "color:var(--red)" },
      el("span", { class: "dot", style: "background:var(--red);animation:none" }), "⚠ STALLED?");
  } else if (s.live || inProgress) {
    liveEl = el("span", { class: "live" }, el("span", { class: "dot" }), "LIVE");
  } else {
    liveEl = el("span", { class: "live idle" }, el("span", { class: "dot" }),
      `IDLE${s.last_activity ? " · " + fmtAgo(s.last_activity).toUpperCase() : ""}`);
  }

  const cost = el("div", { class: "cost" });
  if (s.cost) {
    const spent = s.cost.total_spent_usd ?? 0;
    const budget = spent + (s.cost.budget_remaining_usd ?? 0);
    const hasBudget = s.cost.budget_remaining_usd != null;
    const pct = hasBudget && budget > 0 ? Math.min(100, (spent / budget) * 100) : 0;
    cost.append(el("div", { class: "nums" }, el("b", {}, fmtMoney(spent)),
      hasBudget ? el("span", {}, ` / ${fmtMoney(budget)}`) : ""));
    if (hasBudget) {
      cost.append(el("div", { class: "bar" }, el("i", {
        class: pct > 90 ? "crit" : pct > 75 ? "warn" : "", style: `width:${pct}%`,
      })));
    }
    cost.append(el("div", { class: "label" }, "generation spend"));
  }

  return el("header", { class: "slate" },
    el("div", { class: "clapper" }),
    el("div", {},
      el("a", { class: "wordmark", href: "/", style: "text-decoration:none" }, "Backlot"),
      el("h1", {}, s.title),
    ),
    ...chips,
    hasAuthoredGates(s) ? gatesCTA("GATES") : null,
    el("div", { class: "spacer" }),
    renderThemeToggle(),
    liveEl,
    cost,
  );
}

// ---------------------------------------------------------------------------
// stage rail
// ---------------------------------------------------------------------------

function stageSub(st) {
  if (st.status === "awaiting_human") return "awaiting your approval";
  if (st.status === "in_progress" && st.stalled) {
    return `stalled? no activity for ${st.stalled_minutes}m\nask the agent for status`;
  }
  if (st.status === "in_progress" && st.partial_progress) {
    const done = st.partial_progress.completed_scene_ids;
    if (Array.isArray(done)) return `${done.length} scene${done.length === 1 ? "" : "s"} done`;
    return "in progress";
  }
  if (st.status === "in_progress") return "in progress";
  if (st.status === "failed") return st.error ? String(st.error).slice(0, 60) : "failed";
  if (st.timestamp) {
    const approved = st.gated && st.human_approved ? " · approved" : "";
    return fmtClock(st.timestamp) + approved;
  }
  return "";
}

function gatesCTA(label = "OPEN GATES") {
  return el("a", { class: "gates-cta", href: "#gates" }, label);
}

function renderRail(s) {
  const rail = el("nav", { class: "rail" });
  let pendingIndex = 1;
  for (const st of s.stages) {
    const cls = st.status === "completed" ? "done"
      : st.status === "in_progress" ? (st.stalled ? "active stalled" : "active")
      : st.status === "awaiting_human" ? "await"
      : st.status === "failed" ? "failed" : "";
    const icon = STAGE_ICONS[st.status] || String(pendingIndex);
    if (!STAGE_ICONS[st.status]) pendingIndex += 1;
    const node = el("div", {
      class: `stage ${cls}${selectedStage === st.name ? " selected" : ""}${st.undeclared ? " undeclared" : ""}`,
      title: st.undeclared ? `"${st.name}" ran but isn't declared by this pipeline's manifest` : null,
    },
      el("span", { class: "line" }),
      el("button", {
        class: "stage-select",
        type: "button",
        "aria-expanded": selectedStage === st.name ? "true" : "false",
        "aria-label": `Open ${st.name} stage details: ${st.status}`,
        onclick: () => toggleDrawer(st.name),
      },
        el("span", { class: "node" }, icon),
        el("span", { class: "name" }, st.name),
        el("span", { class: "sub", style: "white-space:pre-line" },
          st.undeclared ? `${stageSub(st)}\nunlisted`.trim() : stageSub(st))),
      st.status === "awaiting_human" && hasAuthoredGates(s) ? gatesCTA() : null,
    );
    rail.append(node);
  }
  return rail;
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
      summary ? el("span", { style: "font-size:calc(11.5px * var(--fs-scale));color:var(--text-2);margin-left:8px" }, summary) : null,
    ));
  }

  const names = artifactNamesForStage(st);
  let shown = false;
  for (const name of names) {
    const artifact = s.artifacts[name];
    if (!artifact) continue;
    shown = true;
    body.append(
      el("div", { class: "d-cat", style: "font-family:var(--mono);font-size:calc(9.5px * var(--fs-scale));color:var(--text-3);letter-spacing:.1em;text-transform:uppercase;margin:6px 0 4px" }, name),
      el("pre", {}, JSON.stringify(artifact, null, 2)),
    );
  }
  if (!shown) {
    body.append(el("div", { class: "hint" },
      st.status === "pending" ? "This stage hasn't run yet." : "No canonical artifact found on disk for this stage."));
  }

  return el("div", { class: "drawer" },
    el("div", { class: "drawer-head" },
      el("h3", {}, `${st.name} — ${st.status}`),
      st.gate_skipped ? el("span", { class: "gate-chip" }, "⚑ GATE SKIPPED") : null,
      st.versions > 1 ? el("span", { class: "ver-chip" }, `v${st.versions}`) : null,
      st.timestamp ? el("span", { class: "meta", style: "font-family:var(--mono);font-size:calc(10.5px * var(--fs-scale));color:var(--text-3)" }, st.timestamp) : null,
      el("span", { class: "close", onclick: () => toggleDrawer(st.name) }, "CLOSE ✕"),
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
    el("span", { class: "sp-expand" }, "⤢ EXPAND SCRIPT"),
  );
  return card;
}

function humanize(value) {
  return String(value || "artifact").replaceAll("_", " ");
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
        ? `The ${awaiting.name} checkpoint declares ${names.map(humanize).join(", ")}, but Backlot could not load it.`
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
// right rail: decisions, activity
// ---------------------------------------------------------------------------

function renderDecisions(s) {
  const log = s.artifacts.decision_log;
  const decisions = (log && log.decisions) || [];
  if (!decisions.length) return null;
  const body = el("div", { class: "panel-body" });
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
    body.append(el("div", { class: "decision" },
      el("div", { class: "d-cat" }, `${d.category || "decision"}${d.confidence ? ` · ${d.confidence}` : ""}`,
        revised ? el("span", { class: "d-revised" }, " · revised") : null),
      el("div", { class: "d-pick" }, `${d.subject || ""} `, el("span", { class: "arrow" }, "→"), ` ${selLabel}`),
      d.reason ? el("div", { class: "d-why" }, d.reason) : null,
      alts.length ? el("div", { class: "d-alt" }, "also considered: ",
        alts.slice(0, 3).map((o, i) => [i ? " · " : "", el("s", {}, o.label || o.option_id)]).flat()) : null,
    ));
  }
  return el("div", { class: "panel" },
    el("div", { class: "panel-head" }, el("h2", {}, "Decisions"), el("span", { class: "meta" }, "decision_log.json")),
    body);
}

function renderActivity(s) {
  const events = s.events || [];
  if (!events.length) return null;
  const body = el("div", { class: "panel-body" });
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
  for (const ev of rows.slice(-10).reverse()) {
    let statusEl;
    if (ev.event === "finish") {
      statusEl = el("span", { class: `status ${ev.success === false ? "err" : "ok"}` },
        `${ev.success === false ? "✕" : "✓"}${ev.duration_s != null ? ` ${ev.duration_s.toFixed ? ev.duration_s.toFixed(1) : ev.duration_s}s` : ""}${ev.cost_usd ? ` ${fmtMoney(ev.cost_usd)}` : ""}`);
    } else if (ev.event === "error") {
      statusEl = el("span", { class: "status err" }, "✕");
    } else {
      statusEl = el("span", { class: "status run" }, "● running");
    }
    body.append(el("div", { class: "act-row" },
      el("span", { class: "t" }, fmtClock(ev.ts)),
      el("span", { class: "tool" }, ev.tool || ""),
      el("span", { class: "target" }, ev.scene_id || ""),
      statusEl,
    ));
  }
  return el("div", { class: "panel" },
    el("div", { class: "panel-head" }, el("h2", {}, "Activity"), el("span", { class: "meta" }, "events.jsonl")),
    body);
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
    card.hero_moment ? el("span", { class: "hero" }, "★ HERO") : null,
    el("span", { class: "dur" }, fmtDuration(dur)),
  );
  wrap.append(slate);

  // visual slot
  let thumb;
  if (card.generating) {
    thumb = el("div", { class: "thumb generating" },
      el("div", { class: "shimmer" }),
      el("div", { class: "gen-label" },
        el("span", {}, "◉ GENERATING"),
        el("span", { class: "sub" }, card.generating_tool || "")));
  } else if (card.visual && card.visual.exists) {
    const v = card.visual;
    const badge = [v.model || v.source_tool, v.cost_usd != null ? fmtMoney(v.cost_usd) : null,
      v.quality_score != null ? `q ${v.quality_score}` : null].filter(Boolean).join(" · ");
    if (v.type === "video") {
      thumb = el("div", { class: "thumb approved" },
        el("video", { src: mediaURL(s.project_id, v.path), muted: "", preload: "metadata", playsinline: "" }),
        el("span", { class: "play" }, "▶"),
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
        el("span", { class: "bespoke-tag" }, "◆ BESPOKE"),
        el("div", { class: "spec-desc" }, card.description || ""),
        el("div", { class: "spec-shot" }, "hand-authored composition")));
  } else if (card.visual && !card.visual.exists) {
    thumb = el("div", { class: "thumb missing" },
      el("div", { class: "spec-in" },
        el("span", { class: "warn-ic" }, "⚑"),
        el("div", { class: "spec-desc" }, "asset in manifest, file missing"),
        el("div", { class: "spec-shot" }, card.visual.path || "")));
  } else if (card.type === "text_card") {
    thumb = el("div", { class: "thumb textcard" },
      el("div", { class: "tc-copy" }, (card.narration || card.description || "").slice(0, 48)));
  } else if (card.required_assets.length) {
    thumb = el("div", { class: "thumb missing" },
      el("div", { class: "spec-in" },
        el("span", { class: "warn-ic" }, "⚑"),
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
        .map((t) => el("span", { style: "font-family:var(--mono);font-size:calc(8.5px * var(--fs-scale));letter-spacing:.04em;color:#62626c;border:1px solid #212129;border-radius:3px;padding:1px 5px" }, String(t).replaceAll("_", " ")))));
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
    }, card.narration, long ? el("span", { class: "narr-more" }, "⤢") : null));
  } else if (card.shot_intent || card.description) {
    wrap.append(el("div", { class: "narr tc-note" }, (card.shot_intent || card.description || "").slice(0, 110)));
  }
  const narrAudio = card.audio.find((a) => a.exists && (a.type === "narration" || a.type === "audio"));
  if (narrAudio) {
    const wave = el("div", { class: "wave", style: "cursor:pointer", title: "Play narration" });
    waveBars(wave, card.id + narrAudio.path);
    wave.append(el("span", { class: "wv-time" }, narrAudio.duration_seconds ? fmtDuration(narrAudio.duration_seconds) : "♪"));
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
    el("span", { style: "font-size:calc(15px * var(--fs-scale))" }, "◌"),
    el("span", {},
      el("b", { style: "color:var(--text-2)" }, "No pipeline state. "),
      "This project has no checkpoints — Backlot is showing what it found on disk. ",
      "Runs that follow the checkpoint protocol get the full board."));
}

function renderAwaitingNotice(s) {
  const awaiting = s.stages.find((x) => x.status === "awaiting_human");
  if (!awaiting) return null;
  return el("div", { class: "notice" },
    el("span", { style: "font-size:calc(16px * var(--fs-scale))" }, "◈"),
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

function renderGateRows(s) {
  const requests = Array.isArray(s.gates.requests) ? s.gates.requests : [];
  if (!requests.length) {
    return el("div", { class: "gate-empty" },
      el("b", {}, "No gate requests"),
      el("p", {}, "When authored-film evidence reaches a governance checkpoint, it will appear here."));
  }

  const body = el("tbody");
  for (const row of requests) {
    const conflict = row.state === "conflict";
    const archival = ["done", "declined", "abandoned"].includes(row.state);
    const selected = selectedGateId === row.request_id;
    const selector = conflict
      ? el("span", { class: "gate-request-id" }, row.request_id)
      : el("button", {
        class: "gate-row-select",
        type: "button",
        "aria-label": `Inspect ${row.kind} gate ${row.request_id}`,
        "aria-pressed": selected ? "true" : "false",
        onclick: () => selectGate(row.request_id),
      }, row.request_id);
    const readOnly = archival
      ? el("div", { class: "gate-row-archive" },
        row.approval_receipt_id ? `Receipt ${row.approval_receipt_id}` : null,
        row.declined_note ? `Note: ${row.declined_note}` : null,
        row.state === "abandoned" ? "No decision was recorded." : null)
      : null;
    body.append(el("tr", {
      class: `${selected ? "selected " : ""}${conflict ? "conflict" : ""}`.trim(),
    },
      el("td", {}, gateStateChip(row.state)),
      el("td", {}, selector),
      el("td", {},
        el("b", {}, humanize(row.kind)),
        el("span", {}, `${row.stage || "—"} · ${row.scope || "—"}`)),
      el("td", {},
        el("span", { class: "gate-summary" }, row.summary || "No summary supplied."),
        el("span", { class: "gate-entity" }, row.entity_id || "—"),
        readOnly),
      el("td", { class: "gate-mtime" }, fmtGateTime(row.mtime)),
    ));
  }

  return el("div", { class: "gate-table-scroll" },
    el("table", { class: "gate-table" },
      el("caption", { class: "sr-only" }, "Authored-film gate requests and their current states"),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col" }, "State"),
        el("th", { scope: "col" }, "Request"),
        el("th", { scope: "col" }, "Kind / stage"),
        el("th", { scope: "col" }, "Evidence scope"),
        el("th", { scope: "col" }, "Modified"))),
      body));
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
  const rendered = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return el("section", { class: "gate-text-evidence" },
    el("h4", {}, humanize(label)),
    el("pre", {}, rendered == null || rendered === "" ? "No value supplied." : rendered));
}

function renderPacketEvidence(s, packet) {
  if (!packet || typeof packet !== "object") {
    return el("div", { class: "gate-empty" }, "This request has no evidence packet.");
  }
  const output = el("div", { class: "gate-packet" });
  if (packet.packet_error || packet.error) {
    output.append(el("div", { class: "gate-packet-error", role: "alert" },
      el("b", {}, "EVIDENCE PACKET ERROR"),
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
  if (visuals.length) {
    output.append(el("div", { class: "gate-contact-sheet" }, visuals));
  }

  if (Array.isArray(packet.qc_rows)) {
    const qc = el("div", { class: "gate-qc-list" });
    for (const row of packet.qc_rows) {
      qc.append(el("section", { class: "gate-qc-row" },
        el("div", { class: "gate-qc-head" },
          el("b", {}, humanize(row.role || "sheet role")),
          el("span", {}, row.label || "unverified")),
        el("pre", {}, JSON.stringify(row.row, null, 2))));
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
  if (code === 4401) return ["AUTH REQUIRED", "The signing capability is missing or expired. Reopen Backlot from the CLI.", "error"];
  if (code === 4409 && /identity mismatch/i.test(reason || "")) return ["IDENTITY MISMATCH", "The live signer does not match this gate request.", "error"];
  if (code === 4409) return ["SIGNER BUSY", "A signing terminal is already attached to this project. Use the open-session reconnect action when it becomes available.", "warn"];
  if (code === 4423) return ["RUN LEASE HELD", "A live production run currently owns the project lease.", "warn"];
  if (code === 4422) return ["EVIDENCE UNAVAILABLE", "The signer is still alive, but input remains locked until the evidence packet is complete.", "error"];
  if (code === 4400) return ["REQUEST STALE", "The gate request changed before signing could begin.", "warn"];
  if (code === 4403) return ["ORIGIN REFUSED", "Backlot refused this page origin.", "error"];
  if (code === 4404) return ["REQUEST MISSING", "The project or gate request is no longer available.", "error"];
  if (code === 1011) return ["SIGNER UNAVAILABLE", reason || "The signing broker is unavailable.", "error"];
  if (code === 1000) return ["SESSION CLOSED", "The signing session closed.", ""];
  return ["CONNECTION CLOSED", reason || `Terminal connection closed (${code}).`, "warn"];
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
  if (!token) {
    setTerminalStatus("AUTH REQUIRED", "Reopen Backlot from the CLI to establish a signing capability.", "error");
    terminalShell.scrollIntoView({ behavior: "auto", block: "start" });
    return;
  }
  if (!gateSession.terminal) {
    setTerminalStatus("TERMINAL UNAVAILABLE", "The vendored terminal runtime did not load.", "error");
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
  setTerminalStatus("CONNECTING", `Opening signing session for ${requestId}…`, "warn");
  terminalShell.scrollIntoView({ behavior: "auto", block: "start" });

  socket.addEventListener("open", () => {
    if (gateSession.socket !== socket) return;
    socket.send(JSON.stringify({ k: token }));
    setTerminalStatus("VERIFYING LEASE", "Terminal input is locked while Backlot refreshes the evidence packet.", "warn");
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
        packetError ? "EVIDENCE UNAVAILABLE" : changed ? "EVIDENCE CHANGED" : "EVIDENCE REFRESHED",
        packetError
          ? "The signer remains detached and alive, but terminal input is locked until a complete evidence packet can be shown."
          : changed
          ? "Evidence changed under the signing lease. Review the refreshed packet above before typing."
          : "Evidence is refreshed under lease. Waiting for the relay to open the input channel.",
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
        gateSession.evidenceChanged ? "EVIDENCE CHANGED" : "INPUT READY",
        gateSession.evidenceChanged
          ? "Evidence changed under the signing lease. Review the refreshed packet above before typing."
          : "Evidence is refreshed under lease. Terminal input is now enabled.",
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
        message.signer === "exited" ? "SIGNER EXITED" : "SIGNING IN PROGRESS",
        message.exit_code == null ? `Signer status: ${message.signer || "active"}.` : `Signer exited with status ${message.exit_code}.`,
        message.exit_code ? "error" : "ready",
      );
      return;
    }
    if (message.type === "lifecycle") {
      setTerminalStatus("SIGNING IN PROGRESS", message.message || message.status || "Signer lifecycle updated.", "ready");
      return;
    }
    if (message.type === "bye") {
      gateSession.inputReady = false;
      setTerminalStatus("SESSION COMPLETE", message.reason ? `Signer closed: ${message.reason}.` : "Signer session complete.", "");
    }
  });
  socket.addEventListener("error", () => {
    if (gateSession.socket === socket) setTerminalStatus("CONNECTION ERROR", "The terminal connection could not be completed.", "error");
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

function renderCanonStrip(s) {
  const canon = Array.isArray(s.gates.canon) ? [...s.gates.canon] : [];
  canon.sort((a, b) => (a.entity || "").localeCompare(b.entity || "")
    || ({ hero: 0, turnaround: 1, expressions: 2 }[a.role] ?? 9)
      - ({ hero: 0, turnaround: 1, expressions: 2 }[b.role] ?? 9));
  const strip = el("div", { class: "canon-strip" });
  for (const entry of canon) {
    strip.append(el("a", {
      class: `canon-frame${entry.role === "hero" ? " hero" : ""}`,
      href: mediaURL(s.project_id, entry.object_rel),
      target: "_blank",
      rel: "noreferrer",
    },
      el("img", {
        src: thumbURL(s.project_id, entry.object_rel, entry.role === "hero" ? 640 : 320),
        width: entry.role === "hero" ? "640" : "320",
        height: entry.role === "hero" ? "480" : "240",
        loading: "lazy",
        alt: `${humanize(entry.role)} canon reference for ${entry.entity}`,
      }),
      el("span", {}, entry.entity, el("b", {}, humanize(entry.role)))));
  }
  if (!canon.length) strip.append(el("div", { class: "gate-empty" }, "No visual canon has been published yet."));
  const looks = Array.isArray(s.gates.looks) ? s.gates.looks : [];
  for (const look of looks) {
    const ticket = typeof look.source_ticket_ref === "object"
      ? look.source_ticket_ref.id || JSON.stringify(look.source_ticket_ref)
      : look.source_ticket_ref;
    strip.append(el("div", { class: "canon-look" },
      el("span", {}, `${look.entity_kind || "entity"} · ${look.entity || "—"}`),
      el("b", {}, `look ${String(look.look_hash || "—").slice(0, 12)}`),
      el("small", {}, `ticket ${ticket || "—"}`)));
  }
  return strip;
}

function renderGateDetail(s) {
  if (!selectedGateId) {
    return el("section", { class: "gate-detail empty-detail", id: "gate-detail", tabindex: "-1" },
      el("div", { class: "gate-empty" },
        el("b", {}, "Select a request to inspect its evidence"),
        el("p", {}, "Evidence is loaded only when requested and refreshed again under the signing lease.")));
  }
  const summary = gateRowById(s, selectedGateId);
  if (gateDetailState === "loading") {
    return el("section", {
      class: "gate-detail",
      id: "gate-detail",
      tabindex: "-1",
      role: "status",
      "aria-live": "polite",
      "aria-busy": "true",
    }, el("h3", { class: "gate-loading" }, "Loading evidence…"));
  }
  if (gateDetailState === "error") {
    return el("section", {
      class: "gate-detail",
      id: "gate-detail",
      tabindex: "-1",
      "aria-labelledby": "gate-detail-heading",
    },
      el("h3", { class: "sr-only", id: "gate-detail-heading", tabindex: "-1" }, "Evidence could not be loaded"),
      el("div", { class: "gate-packet-error", role: "alert" },
        el("b", {}, "EVIDENCE COULD NOT BE LOADED"), el("span", {}, gateDetailError)));
  }
  if (!gateDetail) {
    return el("section", { class: "gate-detail", id: "gate-detail", tabindex: "-1" },
      el("div", { class: "gate-empty", role: "status" }, "No detail packet returned."));
  }

  const stale = !summary || summary.state !== "pending" || gateDetail.state !== "pending";
  const packetError = Boolean(gateDetail.error || (gateDetail.packet && gateDetail.packet.packet_error));
  const lease = s.gates.run_lease || {};
  const matchingOpen = openGateSessions.find((session) => session.request_id === selectedGateId);
  const otherOpen = openGateSessions.find((session) => session.request_id !== selectedGateId);
  const socketOpen = gateSession.socket
    && [WebSocket.CONNECTING, WebSocket.OPEN].includes(gateSession.socket.readyState);
  const actionablePacket = !stale && !packetError;
  const actionable = actionablePacket && !lease.held && !otherOpen && !socketOpen;
  const reconnectable = Boolean(matchingOpen && !socketOpen && actionablePacket && !otherOpen);
  const headerSummary = summary || gateDetail.request || {};
  const facts = [
    ["kind", headerSummary.kind], ["stage", headerSummary.stage], ["scope", headerSummary.scope],
    ["entity", headerSummary.entity_id], ["snapshot", fmtGateTime(gateDetail.snapshot_at)],
  ];

  const actions = el("div", { class: "gate-actions" });
  if (reconnectable) {
    actions.append(
      el("span", { class: "gate-session-note" },
        `Signing in progress · request ${matchingOpen.request_id} · pid ${matchingOpen.pid} · started ${fmtGateTime(matchingOpen.started)}`),
      el("button", { type: "button", class: "gate-primary", onclick: () => startSigning(selectedGateId) }, "Reconnect terminal"));
  } else {
    actions.append(el("button", {
      type: "button",
      class: "gate-primary",
      disabled: actionable ? null : "",
      onclick: () => startSigning(selectedGateId),
    }, socketOpen && gateSession.requestId === selectedGateId
      ? "Signing in progress"
      : matchingOpen ? "Reconnect unavailable" : "Start signing"));
  }
  if (otherOpen) {
    actions.append(el("div", { class: "gate-blocker", role: "status" },
      `Signing disabled: request ${otherOpen.request_id} already has an open signer (pid ${otherOpen.pid}).`));
  } else if (lease.held && !matchingOpen) {
    actions.append(el("div", { class: "gate-blocker", role: "status" },
      `Signing disabled: live run lease held by pid ${lease.pid || "unknown"} since ${fmtGateTime(lease.started)}.`));
  } else if (stale) {
    actions.append(el("div", { class: "gate-blocker stale", role: "status" },
      "This request is no longer pending. The displayed packet is read-only; an existing terminal session is left intact."));
  } else if (packetError) {
    actions.append(el("div", { class: "gate-blocker", role: "status" }, "Signing is disabled until the evidence packet is valid."));
  } else if (socketOpen) {
    actions.append(el("div", { class: "gate-session-note", role: "status" },
      `Terminal session ${gateSession.requestId || "connecting"} is active and preserved across board refreshes.`));
  }

  const command = summary && summary.next_command;
  const commandBox = command ? el("div", { class: "gate-command" },
    el("div", {}, el("span", {}, "NEXT COMMAND"),
      el("button", { type: "button", onclick: () => copyCommand(command) }, "Copy command")),
    el("code", {}, command),
    el("p", { "aria-live": "polite", "data-gate-copy-status": "" }, copyStatus)) : null;

  return el("section", {
    class: `gate-detail${stale ? " stale" : ""}`,
    id: "gate-detail",
    tabindex: "-1",
    "aria-labelledby": "gate-detail-heading",
  },
    el("header", { class: "gate-detail-head" },
      el("div", {},
        el("span", { class: "gate-detail-kicker" }, `EVIDENCE PACKET · ${selectedGateId}`),
        el("h3", { id: "gate-detail-heading", tabindex: "-1" },
          headerSummary.summary || humanize(headerSummary.kind || "Gate request"))),
      gateStateChip(summary ? summary.state : gateDetail.state)),
    gateDetail.error ? el("div", { class: "gate-packet-error", role: "alert" },
      el("b", {}, "EVIDENCE LOOKUP ERROR"), el("span", {}, gateDetail.error)) : null,
    gateDetail.packet
      ? renderPacketEvidence(s, gateDetail.packet)
      : el("div", { class: "gate-archive-detail" },
        textEvidence("request", gateDetail.request || {}),
        gateDetail.approval_receipt_id ? textEvidence("approval receipt id", gateDetail.approval_receipt_id) : null,
        gateDetail.declined_note ? textEvidence("declined note", gateDetail.declined_note) : null,
        gateDetail.unverified_ledger_row
          ? textEvidence(gateDetail.ledger_label || "unverified ledger row", gateDetail.unverified_ledger_row) : null),
    el("dl", { class: "gate-detail-facts" }, facts.map(([label, value]) =>
      el("div", {}, el("dt", {}, label), el("dd", {}, value || "—")))),
    commandBox,
    actions);
}

function renderGatesWorkbench(s) {
  if (!hasAuthoredGates(s)) return null;
  const cost = s.gates.cost || {};
  const costUnknown = cost.total_spent_usd == null;
  const costError = typeof cost.error === "string" && cost.error;
  const shownRequests = (s.gates.requests || []).length;
  const totalRequests = Number.isInteger(s.gates.total_requests) ? s.gates.total_requests : shownRequests;
  return el("section", { class: "gates-workbench", id: "gates" },
    el("header", { class: "gates-head" },
      el("div", {},
        el("span", { class: "gates-kicker" }, "AUTHORED-FILM GOVERNANCE"),
        el("h2", {}, "Gates / Evidence desk"),
        el("p", {}, "Proof first. Signing happens in the live terminal against a packet refreshed under lease.")),
      el("div", { class: `gate-cost-tile${costError || costUnknown ? " unavailable" : ""}` },
        el("span", {}, `${cost.label || "ledger"} cost`),
        el("b", {}, costError ? "UNAVAILABLE" : costUnknown ? "UNKNOWN" : fmtMoney(cost.total_spent_usd)),
        costError
          ? el("small", { role: "alert" }, costError)
          : cost.total_reserved_usd != null ? el("small", {}, `${fmtMoney(cost.total_reserved_usd)} reserved`) : null)),
    s.gates.error ? el("div", { class: "gate-packet-error", role: "alert" },
      el("b", {}, "GATE INDEX ERROR"), el("span", {}, s.gates.error)) : null,
    el("div", { class: "canon-desk" },
      el("h3", { class: "section-title" }, "Canon strip", el("span", { class: "meta" }, "published visual truth / active looks")),
      renderCanonStrip(s)),
    el("div", { class: "gate-ledger" },
      el("h3", { class: "section-title" }, "Request ledger",
        el("span", { class: "meta" }, s.gates.truncated
          ? `${shownRequests} of ${totalRequests} requests · pending first`
          : `${shownRequests} request${shownRequests === 1 ? "" : "s"}`)),
      s.gates.truncated
        ? el("div", { class: "gate-packet-error", role: "status" },
          el("b", {}, "REQUEST LIST BOUNDED"),
          el("span", {}, `${totalRequests - shownRequests} request${totalRequests - shownRequests === 1 ? "" : "s"} omitted; pending requests are prioritized.`))
        : null,
      renderGateRows(s)),
    renderGateDetail(s));
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
      el("span", { class: "rp-time" }, "scrub the whole run"),
      el("span", { class: "rp-btn", onclick: startReplay }, "▶ REPLAY RUN"));
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
    el("span", { class: "rp-btn", onclick: toggleReplayPlay }, replay.playing ? "❚❚" : "▶"),
    el("input", {
      type: "range", min: "0", max: "1000", value: String(Math.round(pos * 1000)),
      // A full render() would destroy this slider mid-drag: while dragging,
      // only pause + track the time label; re-render the board on release.
      onpointerdown: () => { replay.playing = false; },
      oninput: (e) => setT(e.target.value),
      onchange: (e) => { setT(e.target.value); render(); },
    }),
    timeLabel,
    el("span", { class: "rp-btn", onclick: stopReplay }, "✕ LIVE"),
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
        "REQUEST NO LONGER PENDING",
        "Board state changed while the terminal was open. Output is preserved; terminal input is disabled.",
        "warn",
      );
    }
  }
  document.title = `Backlot — ${s.title}`;
  document.body.classList.toggle("first", firstPaint);
  firstPaint = false;
  terminalShell.hidden = !authoredGates;
  app.innerHTML = "";
  app.append(renderSlate(s));
  app.append(renderRail(s));
  const replayBar = renderReplayBar(state);
  if (replayBar) app.append(replayBar);
  const drawer = renderDrawer(s);
  if (drawer) app.append(drawer);
  const awaitingNotice = renderAwaitingNotice(s);
  if (awaitingNotice) app.append(awaitingNotice);
  const noState = renderNoState(s);
  if (noState) app.append(noState);

  const main = el("div", { class: "main-col" });
  const approvalReview = renderApprovalReview(s);
  if (approvalReview) main.append(approvalReview);
  const script = renderScriptCard(s);
  if (script) main.append(script);
  const aside = el("aside", {});
  const decisions = renderDecisions(s);
  const activity = renderActivity(s);
  if (decisions) aside.append(decisions);
  if (activity) aside.append(activity);

  // Media sections live INSIDE the main column so a tall decisions rail
  // never pushes them below the fold — the column flows beside the rail.
  const storyboard = renderStoryboard(s);
  const found = renderFoundMedia(s);
  const renders = renderRenders(s);

  if (approvalReview || script || decisions || activity) {
    for (const section of [storyboard, found, renders]) {
      if (section) main.append(section);
    }
    const hasAside = Boolean(decisions || activity);
    app.append(el("div", { class: `board${hasAside ? "" : " solo"}` }, main, hasAside ? aside : null));
  } else {
    for (const section of [storyboard, found, renders]) {
      if (section) app.append(section);
    }
  }
  if (authoredGates) {
    app.append(renderGatesWorkbench(s));
    scheduleTerminalFit(false);
  }
}

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

refresh().catch((err) => {
  app.innerHTML = "";
  app.append(el("div", { class: "empty", style: "margin-top:80px" },
    el("div", { class: "big" }, "PROJECT NOT FOUND"),
    el("div", {}, String(err))));
});
// ?static=1 disables the live feed (screenshots, static exports).
if (!new URLSearchParams(location.search).has("static")) {
  subscribe(`/api/project/${encodeURIComponent(projectId)}/events`, () => refresh().catch(console.error));
}
