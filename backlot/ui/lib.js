// Shared helpers for the Backlot UI.

// The server opens Backlot with a short-lived capability in the URL fragment.
// Capture it once per tab and scrub the fragment before any navigation can
// retain it.  The value is intentionally never returned by this helper.
export const CAPABILITY_TOKEN_KEY = "backlot.capability-token";

export function captureCapabilityToken() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const token = params.get("k");
  if (token) {
    try {
      sessionStorage.setItem(CAPABILITY_TOKEN_KEY, token);
    } catch {
      // A privacy-restricted browser may reject sessionStorage. The terminal
      // will surface an authentication setup message if signing is requested.
    }
  }
  if (params.has("k")) {
    history.replaceState(history.state, "", `${location.pathname}${location.search}`);
  }
}

captureCapabilityToken();

export async function getJSON(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function fmtDuration(seconds) {
  const n = Number(seconds);
  if (seconds == null || !Number.isFinite(n)) return "";
  const s = Math.max(0, Math.round(n));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtMoney(v) {
  const n = Number(v);
  if (v == null || !Number.isFinite(n)) return "—";
  return `$${n.toFixed(2)}`;
}

export function fmtAgo(epochSeconds) {
  if (!epochSeconds) return "";
  const diff = Date.now() / 1000 - epochSeconds;
  if (diff < 90) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

export function fmtClock(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "";
  }
}

export function mediaURL(projectId, relPath) {
  return `/media/${encodeURIComponent(projectId)}/${relPath.split("/").map(encodeURIComponent).join("/")}`;
}

// Downscaled cached JPEG for images (full media only in players/lightbox).
export function thumbURL(projectId, relPath, w = 640) {
  return `/thumb/${encodeURIComponent(projectId)}/${relPath.split("/").map(encodeURIComponent).join("/")}?w=${w}`;
}

// Subscribe to a server-sent change feed; call onChange (debounced) per burst.
// Full-viewport-width banner shown while the live connection is down: a page
// with a dead event stream silently displays stale gates, which reads as the
// app being broken (2026-09-01). One banner per page, shared by every
// subscription on it.
const CONN_BANNER_ID = "bk-conn-banner";
function connBanner(show) {
  let el = document.getElementById(CONN_BANNER_ID);
  if (!show) { if (el) el.remove(); return; }
  if (el) return;
  el = document.createElement("div");
  el.id = CONN_BANNER_ID;
  el.textContent = "⚠ CONNECTION LOST — this page is showing old data. Reconnecting…";
  el.style.cssText = [
    "position:fixed", "top:0", "left:0", "right:0", "z-index:99999",
    "padding:10px 16px", "background:#8a1f1f", "color:#fff",
    "font:600 14px/1.4 system-ui,sans-serif", "text-align:center",
    "letter-spacing:.02em",
  ].join(";");
  document.body.append(el);
}

// The server heartbeats every 15s on every event stream. A restarting (or
// half-dead) server often leaves the old connection LOOKING open — no error
// ever fires in the browser, the page just goes quiet and stale (verified
// live 2026-09-01). So liveness is judged by received data, not socket state.
const SSE_DEAD_AFTER_MS = 40_000;  // ~2.5 missed heartbeats
const SSE_SUSPECT_AFTER_MS = 20_000;

export function subscribe(url, onChange) {
  let timer = null;        // debounce for change events
  let bannerTimer = null;  // grace period so a sub-second blip never flashes the banner
  let source = null;
  let everOpened = false;
  let retryDelay = 1000;
  let closed = false;
  let lastBeat = Date.now();

  const heal = () => {
    // Treat the current connection as dead: banner (after grace), rebuild.
    if (closed) return;
    try { source.close(); } catch { /* already closed */ }
    if (!bannerTimer) bannerTimer = setTimeout(() => connBanner(true), 1200);
    setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 10_000);
  };

  const connect = () => {
    if (closed) return;
    source = new EventSource(url);
    source.onopen = () => {
      retryDelay = 1000;
      lastBeat = Date.now();
      clearTimeout(bannerTimer); bannerTimer = null;
      connBanner(false);
      if (everOpened) {
        // Reconnected after a drop (server restart): events sent while we
        // were gone are lost, so re-pull the full state instead of trusting
        // what is on screen.
        clearTimeout(timer);
        timer = setTimeout(onChange, 100);
      }
      everOpened = true;
    };
    source.onmessage = (msg) => {
      lastBeat = Date.now();  // hello/heartbeat/change all count as life
      try {
        const data = JSON.parse(msg.data);
        if (data.type !== "change") return;
      } catch {
        return;
      }
      clearTimeout(timer);
      timer = setTimeout(onChange, 250);
    };
    source.onerror = () => {
      if (closed) return;
      if (!bannerTimer) bannerTimer = setTimeout(() => connBanner(true), 1200);
      if (source.readyState === EventSource.CLOSED) {
        // The browser gave up (it only auto-retries while CONNECTING);
        // rebuild the connection ourselves, forever, with capped backoff.
        heal();
      }
    };
  };

  // Dead-man switch: heartbeats arrive every 15s; prolonged silence means the
  // connection is a zombie even though the browser reports it open.
  const watchdog = setInterval(() => {
    if (!closed && Date.now() - lastBeat > SSE_DEAD_AFTER_MS) heal();
  }, 5_000);
  // Returning to the tab checks immediately — nobody should look at a page
  // that is quietly stale while the watchdog counts down.
  const onWake = () => {
    if (closed || document.hidden) return;
    if (Date.now() - lastBeat > SSE_SUSPECT_AFTER_MS) heal();
  };
  document.addEventListener("visibilitychange", onWake);
  window.addEventListener("focus", onWake);

  connect();
  return {
    close() {
      closed = true;
      clearInterval(watchdog);
      document.removeEventListener("visibilitychange", onWake);
      window.removeEventListener("focus", onWake);
      clearTimeout(timer); clearTimeout(bannerTimer);
      try { source.close(); } catch { /* already closed */ }
      connBanner(false);
    },
  };
}

// Deterministic pseudo-waveform bars (seeded by a string).
export function waveBars(container, seedStr, count = 26, maxH = 14) {
  let seed = 0;
  for (const c of seedStr || "wave") seed = (seed * 31 + c.charCodeAt(0)) % 2147483647;
  seed = seed || 7;
  container.innerHTML = "";
  for (let i = 0; i < count; i++) {
    seed = (seed * 16807) % 2147483647;
    const h = 3 + ((seed % 100) / 100) * maxH * (0.55 + 0.45 * Math.sin(i / 5));
    const bar = document.createElement("i");
    bar.style.height = `${Math.max(3, h)}px`;
    container.append(bar);
  }
}

export const STAGE_ICONS = {
  completed: "✓",
  in_progress: "◉",
  awaiting_human: "◈",
  failed: "✕",
};
