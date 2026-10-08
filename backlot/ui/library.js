import { el, fmtAgo, getJSON, subscribe, thumbURL } from "/ui/lib.js";

const grid = document.getElementById("grid");
function miniRail(states) {
  const rail = el("div", { class: "mini-rail" });
  for (const s of states) {
    const cls = s.status === "completed" ? "d"
      : s.status === "in_progress" ? "a"
      : s.status === "awaiting_human" ? "w" : "";
    rail.append(el("i", { class: cls, title: `${s.name}: ${s.status}` }));
  }
  return rail;
}

function card(p) {
  const poster = el("div", { class: "lib-poster" });
  if (p.poster) {
    poster.append(el("img", {
      src: thumbURL(p.project_id, p.poster, 640),
      width: "640",
      height: "360",
      loading: "lazy",
      alt: "",
    }));
  } else {
    poster.append(el("span", { class: "lp-txt" }, "Nothing on film yet"));
  }
  if (p.awaiting_human) {
    poster.append(el("span", { class: "lp-live needs" }, "Needs you"));
  } else if (p.live && p.active_stage) {
    poster.append(el("span", { class: "lp-live" }, `Working · ${String(p.active_stage).replaceAll("_", " ")}`));
  }

  const meta = el("div", { class: "lb-meta" },
    p.scene_count ? el("span", {}, `${p.scene_count} scenes`) : null,
    p.render_count ? el("span", {}, `${p.render_count} cut${p.render_count === 1 ? "" : "s"}`) : null,
    el("span", { class: "when" }, fmtAgo(p.last_activity)),
  );

  const staticSuffix = new URLSearchParams(location.search).has("static") ? "?static=1" : "";
  return el("a", { class: `lib-card${p.live ? " live-card" : ""}`, href: `/p/${p.project_id}${staticSuffix}` },
    poster,
    el("div", { class: "lib-body" },
      el("h3", {}, p.title || p.project_id),
      meta,
      p.stage_states.length ? miniRail(p.stage_states) : null,
    ),
  );
}

function leadState(p) {
  const states = p.stage_states || [];
  const done = states.filter((s) => s.status === "completed").length;
  const label = p.awaiting_human ? "Needs you" : p.live ? "Working" : "Up next";
  const cls = p.awaiting_human ? "waiting" : p.live ? "running" : "";
  const active = p.active_stage ? String(p.active_stage).replaceAll("_", " ") : null;
  const line = p.awaiting_human
    ? "Something is waiting for your approval."
    : active ? `Working on ${active}.` : "Pick up where you left off.";
  return el("div", { class: "lead-state" },
    el("span", { class: `tape ${cls}`.trim() }, label),
    el("p", {}, line),
    states.length ? el("p", {}, `${done} of ${states.length} stages done`) : null);
}

async function render() {
  const projects = await getJSON("/api/projects");
  document.getElementById("count").textContent = `${projects.length} film${projects.length === 1 ? "" : "s"}`;
  const liveCount = projects.filter((p) => p.live).length;
  const badge = document.getElementById("liveBadge");
  badge.classList.toggle("running", liveCount > 0);
  document.getElementById("liveText").textContent = liveCount ? `${liveCount} working` : "Idle";
  grid.innerHTML = "";
  document.getElementById("empty").hidden = Boolean(projects.length);
  // One film leads: the one that needs you, else the most recently touched.
  const lead = projects.find((p) => p.awaiting_human)
    || [...projects].sort((a, b) => (b.last_activity || 0) - (a.last_activity || 0))[0];
  const ordered = lead ? [lead, ...projects.filter((p) => p !== lead)] : projects;
  ordered.forEach((p) => {
    const node = card(p);
    if (p === lead && projects.length > 1) {
      node.classList.add("featured");
      node.querySelector(".lib-body h3").after(leadState(p));
    }
    grid.append(node);
  });
}

render().catch(console.error);
if (!new URLSearchParams(location.search).has("static")) {
  subscribe("/api/library/events", () => render().catch(console.error));
}
