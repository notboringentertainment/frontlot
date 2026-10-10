"""Deterministic visual eval for Backlot.

Stages the fictional Backlot projects, captures canonical browser screenshots,
optionally compares them to goldens, and can run a small Playwright interaction
smoke against the staged board.

Examples:
    python scripts/backlot_visual_eval.py
    python scripts/backlot_visual_eval.py --bless
    python scripts/backlot_visual_eval.py --interactions
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops
from PIL import ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.lib.look_lock_helpers import CHAR  # noqa: E402

STAGE_DIR = REPO_ROOT / ".backlot" / "screenshot-stage"
GOLDENS_DIR = REPO_ROOT / "internal" / "evals" / "goldens"
CAPTURE_ROOT = REPO_ROOT / "internal" / "evals" / "captures"
PORT = 4791
GATE_PROJECT_ID = "project-golden-gate"
GATE_REQUEST_ID = "headshot-char-golden-1"
GATE_SHOT_NAME = "headshot-gate-terminal"

# The only masked bytes are the server-authored snapshot timestamp.  The
# rectangle is computed from that exact DOM node after the fixed clip is
# chosen, rather than hiding a broad part of the evidence packet.
GATE_MASK_REASON = "volatile gate-detail snapshot timestamp"

# A browser-local protocol peer keeps the interaction hermetic: the real UI
# constructs the WebSocket, performs the capability handshake, receives a
# packet_refreshed frame, mounts real xterm, and follows the production render
# path.  No connection reaches the real signing broker and no input bytes are
# injected into any signer.
HERMETIC_WEBSOCKET_INIT = r"""
(() => {
  class HermeticGateSocket extends EventTarget {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    constructor(url) {
      super();
      this.url = String(url);
      this.readyState = HermeticGateSocket.CONNECTING;
      this.binaryType = "blob";
      this.protocol = "";
      this.extensions = "";
      this.bufferedAmount = 0;
      this.sent = [];
      this.protocolFrames = [];
      window.__backlotHermeticSockets = window.__backlotHermeticSockets || [];
      window.__backlotHermeticSockets.push(this);
      queueMicrotask(() => {
        if (this.readyState !== HermeticGateSocket.CONNECTING) return;
        this.readyState = HermeticGateSocket.OPEN;
        this.dispatchEvent(new Event("open"));
      });
    }

    async send(data) {
      if (this.readyState !== HermeticGateSocket.OPEN) throw new DOMException("socket is not open");
      this.sent.push(data);
      if (typeof data !== "string") return;
      let frame;
      try { frame = JSON.parse(data); } catch { return; }
      this.protocolFrames.push(frame);
      if (!frame.k || this._refreshed) return;
      this._refreshed = true;
      const match = this.url.match(/\/api\/project\/([^/]+)\/gate\/([^/]+)\/tty$/);
      if (!match) throw new Error(`unexpected hermetic tty URL: ${this.url}`);
      const detailURL = `/api/project/${match[1]}/gate/${match[2]}`;
      const response = await fetch(detailURL);
      if (!response.ok) throw new Error(`hermetic packet fetch failed: ${response.status}`);
      const packet = await response.json();
      const packetError = JSON.parse(JSON.stringify(packet));
      packetError.packet = {packet_error: true, error: "hermetic incomplete evidence"};
      this.dispatchEvent(new MessageEvent("message", {
        data: JSON.stringify({type: "packet_refreshed", packet: packetError}),
      }));
      window.__hermeticPacketErrorDelivered = true;
      await new Promise((resolve) => setTimeout(resolve, 180));
      this.dispatchEvent(new MessageEvent("message", {
        data: JSON.stringify({type: "input_ready"}),
      }));
      await new Promise((resolve) => setTimeout(resolve, 30));
      this.dispatchEvent(new MessageEvent("message", {
        data: JSON.stringify({type: "packet_refreshed", packet}),
      }));
      this.dispatchEvent(new MessageEvent("message", {
        data: JSON.stringify({type: "packet_refreshed", packet}),
      }));
      this.dispatchEvent(new MessageEvent("message", {
        data: JSON.stringify({type: "input_ready"}),
      }));
      const bytes = new TextEncoder().encode(
        "HERMETIC SIGNING SESSION\r\n" +
        "[1] candidate one    [2] candidate two    [3] candidate three\r\n" +
        "Evidence refreshed. Input channel open."
      );
      this.dispatchEvent(new MessageEvent("message", {data: bytes.buffer}));
    }

    close(code = 1000, reason = "hermetic cleanup") {
      if (this.readyState === HermeticGateSocket.CLOSED) return;
      this.readyState = HermeticGateSocket.CLOSED;
      this.dispatchEvent(new CloseEvent("close", {code, reason, wasClean: true}));
    }
  }
  window.WebSocket = HermeticGateSocket;
})();
"""

NODE_INTERACTION_SCRIPT = r"""
const fs = require("node:fs");
const { chromium } = require("playwright");

const baseURL = process.env.BACKLOT_BASE_URL;
const projectId = process.env.BACKLOT_PROJECT_ID;
const requestId = process.env.BACKLOT_REQUEST_ID;
const charId = process.env.BACKLOT_CHAR_ID;
const screenshotPath = process.env.BACKLOT_SCREENSHOT_PATH;
const resultPath = process.env.BACKLOT_RESULT_PATH;
const initScriptPath = process.env.BACKLOT_INIT_SCRIPT_PATH;
const costLogPath = process.env.BACKLOT_COST_LOG_PATH;
const maskReason = process.env.BACKLOT_MASK_REASON;

async function captureClip(page) {
  const detailBox = await page.locator("#gate-detail").boundingBox();
  const terminalBox = await page.locator("#gate-terminal-shell").boundingBox();
  const snapshotBox = await page.locator("#gate-detail dt", {hasText: "snapshot"})
    .locator("..").locator("dd").boundingBox();
  if (!detailBox || !terminalBox || !snapshotBox) {
    throw new Error("gate evidence, terminal, or snapshot timestamp has no capture bounds");
  }
  const x = Math.min(detailBox.x, terminalBox.x);
  const y = Math.min(detailBox.y, terminalBox.y);
  const right = Math.max(detailBox.x + detailBox.width, terminalBox.x + terminalBox.width);
  const bottom = Math.max(detailBox.y + detailBox.height, terminalBox.y + terminalBox.height);
  const clip = {x, y, width: right - x, height: bottom - y};
  const mask = [
    Math.max(0, Math.round(snapshotBox.x - x) - 2),
    Math.max(0, Math.round(snapshotBox.y - y) - 2),
    Math.min(Math.round(clip.width), Math.round(snapshotBox.x + snapshotBox.width - x) + 2),
    Math.min(Math.round(clip.height), Math.round(snapshotBox.y + snapshotBox.height - y) + 2),
  ];
  return {clip, masks: [mask]};
}

(async () => {
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({
    viewport: {width: 1560, height: 1800},
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "reduce",
    deviceScaleFactor: 1,
  });
  await context.addInitScript({path: initScriptPath});
  const page = await context.newPage();
  let detailResponses = 0;
  page.on("response", (response) => {
    if (response.url().endsWith(`/api/project/${projectId}/gate/${requestId}`)) detailResponses += 1;
  });
  try {
    await page.goto(`${baseURL}/#k=hermetic-golden-token`, {waitUntil: "domcontentloaded"});
    const projectLink = page.locator(`a.lib-card[href^="/p/${projectId}"]`);
    await projectLink.waitFor({state: "visible"});
    await projectLink.click();
    await page.waitForURL(`**/p/${projectId}`);
    await page.locator("#gates").waitFor({state: "visible"});

    const row = page.getByRole("button", {name: `Inspect headshot gate ${requestId}`});
    await row.click();
    const detail = page.locator("#gate-detail");
    await detail.getByText(`Review three invented headshot candidates for ${charId}.`, {exact: true}).waitFor();
    const images = detail.locator(".gate-evidence-image");
    const imageCount = await images.count();
    if (imageCount !== 3) throw new Error(`expected exactly three candidate images, found ${imageCount}`);
    await page.waitForFunction(() => [...document.querySelectorAll("#gate-detail .gate-evidence-image")]
      .every((image) => image.complete && image.naturalWidth > 0));
    for (let index = 0; index < 3; index += 1) {
      if (!(await images.nth(index).isVisible())) throw new Error("one or more candidate images are not visible");
    }
    const labels = await detail.locator(".gate-evidence-card figcaption b").allTextContents();
    if (JSON.stringify(labels) !== JSON.stringify(["[1]", "[2]", "[3]"])) {
      throw new Error(`candidate labels/order drifted: ${JSON.stringify(labels)}`);
    }
    if ((await detail.getByText("[4]", {exact: true}).count()) || (await images.count()) !== 3) {
      throw new Error("a fourth candidate rendered");
    }

    await detail.getByRole("button", {name: "Start signing"}).click();
    await page.locator("#gate-terminal .xterm").waitFor({state: "visible"});
    await page.waitForFunction(() => window.__hermeticPacketErrorDelivered === true);
    await page.waitForTimeout(70);
    const packetErrorLocked = await page.evaluate(() => window.__backlotGateSession?.inputReady === false);
    if (!packetErrorLocked) throw new Error("packet_error refresh enabled terminal input");
    await page.waitForFunction(() => window.__backlotGateSession?.socket?.readyState === WebSocket.OPEN
      && window.__backlotGateSession?.inputReady === true);
    await page.locator("#gate-terminal").getByText("HERMETIC SIGNING SESSION", {exact: false}).waitFor();
    if (!(await page.locator("#gate-terminal-shell").isVisible())) throw new Error("terminal shell is not visible");

    await page.evaluate(() => {
      window.__goldenGateIdentity = {
        mount: document.getElementById("gate-terminal"),
        xtermElement: document.querySelector("#gate-terminal > .xterm"),
        session: window.__backlotGateSession,
        terminal: window.__backlotGateSession.terminal,
        socket: window.__backlotGateSession.socket,
      };
    });

    const stateURL = `/api/project/${projectId}/state`;
    const detailURL = `/api/project/${projectId}/gate/${requestId}`;
    const detailResponsesBeforeSse = detailResponses;
    const refreshResponse = page.waitForResponse((response) => response.url().endsWith(stateURL), {timeout: 15000});
    const detailRefreshResponse = page.waitForResponse((response) => response.url().endsWith(detailURL), {timeout: 15000});
    fs.writeFileSync(costLogPath, JSON.stringify({entries: [{status: "reserved", reserved_usd: 0.25}]}, null, 2));
    await Promise.all([refreshResponse, detailRefreshResponse]);
    await page.getByText("$0.25 reserved", {exact: true}).waitFor();

    const identity = await page.evaluate(() => {
      const before = window.__goldenGateIdentity;
      const session = window.__backlotGateSession;
      const socket = session.socket;
      return {
        mount_same: before.mount === document.getElementById("gate-terminal"),
        xterm_dom_same: before.xtermElement === document.querySelector("#gate-terminal > .xterm"),
        session_same: before.session === session,
        terminal_same: before.terminal === session.terminal,
        socket_same: before.socket === socket,
        socket_open: socket?.readyState === WebSocket.OPEN,
        input_ready: session.inputReady === true,
        auth_frames: socket?.protocolFrames?.filter((frame) => Boolean(frame.k)).length || 0,
        resize_frames: socket?.protocolFrames?.filter((frame) => frame.type === "resize").length || 0,
        input_byte_frames: socket?.sent?.filter((frame) => typeof frame !== "string").length || 0,
      };
    });
    identity.packet_error_locked = packetErrorLocked;
    identity.detail_refetched = detailResponses > detailResponsesBeforeSse;
    for (const name of ["mount_same", "xterm_dom_same", "session_same", "terminal_same", "socket_same", "socket_open", "input_ready", "packet_error_locked", "detail_refetched"]) {
      if (identity[name] !== true) throw new Error(`SSE replaced or closed the terminal session: ${JSON.stringify(identity)}`);
    }
    if (identity.auth_frames !== 1) throw new Error(`unexpected fake protocol handshake count: ${JSON.stringify(identity)}`);
    if (identity.resize_frames < 1) throw new Error(`no terminal resize protocol frame: ${JSON.stringify(identity)}`);
    if (identity.input_byte_frames !== 0) throw new Error(`the interaction injected terminal input bytes: ${JSON.stringify(identity)}`);

    const refreshedLabels = await detail.locator(".gate-evidence-card figcaption b").allTextContents();
    if (JSON.stringify(refreshedLabels) !== JSON.stringify(["[1]", "[2]", "[3]"]) || (await images.count()) !== 3) {
      throw new Error(`candidate packet did not survive SSE refresh: ${JSON.stringify(refreshedLabels)}`);
    }

    await page.evaluate(() => { window.__backlotGateSession.terminal.options.cursorBlink = false; });
    await page.addStyleTag({content: "*, *::before, *::after { animation: none !important; transition: none !important; }"});
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => document.getElementById("gate-detail").scrollIntoView({block: "start"}));
    await page.waitForTimeout(150);
    const {clip, masks} = await captureClip(page);
    await page.screenshot({path: screenshotPath, clip, animations: "disabled", caret: "hide"});
    fs.writeFileSync(resultPath, JSON.stringify({
      status: "passed",
      screenshot: screenshotPath,
      masks,
      mask_reason: maskReason,
      identity,
      sse_boundary: "real watchfiles project change -> server SSE change -> board refresh/render",
      protocol_boundary: "browser-local WebSocket peer; real UI/xterm/fetch/session behavior",
    }, null, 2));
  } finally {
    await context.close();
    await browser.close();
  }
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""

SHOTS = [
    ("library", "/?static=1", 1560, 500, 4200, [
        (1370, 20, 1510, 62),   # live/idle badge
        (90, 106, 422, 380),    # card border/status animation variance
        (440, 106, 772, 380),
        (790, 106, 1122, 380),
        (1140, 106, 1472, 380),
    ]),
    ("board-live", "/p/signal-in-the-static?static=1", 1560, 1150, 4200, []),
    ("script-gate", "/p/the-slow-orchard?static=1", 1560, 760, 3200, []),
    ("storyboard", "/p/the-last-lighthouse?static=1", 1560, 1500, 4200, []),
]


def compare_images(
    expected_path: Path,
    actual_path: Path,
    diff_path: Path,
    *,
    threshold: float = 0.015,
    masks: list[tuple[int, int, int, int]] | None = None,
) -> dict[str, Any]:
    """Compare screenshots by changed-pixel ratio and write a red diff image."""
    expected = Image.open(expected_path).convert("RGB")
    actual = Image.open(actual_path).convert("RGB")
    if expected.size != actual.size:
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        actual.save(diff_path)
        return {"passed": False, "changed_ratio": 1.0, "reason": f"size {expected.size} != {actual.size}"}

    masks = masks or []
    for box in masks:
        patch = expected.crop(box)
        actual.paste(patch, box)

    delta = ImageChops.difference(expected, actual)
    changed = 0
    pixels = delta.load()
    width, height = delta.size
    diff = Image.new("RGB", delta.size, (0, 0, 0))
    diff_px = diff.load()
    for y in range(height):
        for x in range(width):
            if max(pixels[x, y]) > 8:
                changed += 1
                diff_px[x, y] = (255, 40, 40)
            else:
                diff_px[x, y] = actual.getpixel((x, y))
    ratio = changed / float(width * height)
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff.save(diff_path)
    return {"passed": ratio <= threshold, "changed_ratio": round(ratio, 6), "threshold": threshold}


def run_stage() -> None:
    subprocess.run(
        [sys.executable, "scripts/backlot_screenshot_stage.py", "--stage-only"],
        cwd=REPO_ROOT,
        check=True,
        timeout=180,
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _headshot_candidate(path: Path, number: int) -> dict[str, Any]:
    """Write one deterministic, byte-real fictional headshot candidate."""
    palettes = [
        ((28, 35, 52), (237, 164, 74), (93, 194, 180)),
        ((36, 25, 48), (203, 119, 156), (121, 170, 236)),
        ((21, 43, 39), (226, 194, 103), (102, 153, 126)),
    ]
    background, face, accent = palettes[number - 1]
    image = Image.new("RGB", (640, 480), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 330, 640, 480), fill=tuple(max(0, channel - 8) for channel in background))
    draw.ellipse((188, 66, 452, 330), fill=face)
    draw.polygon(((150, 480), (215, 292), (320, 342), (425, 292), (490, 480)), fill=accent)
    draw.arc((222, 145, 418, 275), 18, 162, fill=(18, 20, 25), width=8)
    draw.ellipse((253, 180, 275, 198), fill=(18, 20, 25))
    draw.ellipse((365, 180, 387, 198), fill=(18, 20, 25))
    draw.text((24, 24), f"{CHAR} / TAKE {number}", fill=(245, 242, 235))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "asset_id": digest,
        "path": path.relative_to(path.parents[2]).as_posix(),
        "role": "hero",
        "provenance": {
            "generator_kind": ("model", "local", "imported")[number - 1],
            "generation_receipt_id": f"golden-generation-{number}",
        },
    }


def stage_gate_project() -> Path:
    """Stage the hermetic authored-film headshot gate used by the golden."""
    project = STAGE_DIR / GATE_PROJECT_ID
    if project.exists():
        shutil.rmtree(project)
    (project / "assets" / "images").mkdir(parents=True, exist_ok=True)
    _write_json(project / "project.json", {
        "project_id": GATE_PROJECT_ID,
        "title": "PROJECT — Golden Gate Study",
        "pipeline_type": "authored-film",
        "created_at": "2026-08-30T00:00:00Z",
    })
    project.joinpath("project.yaml").write_text(
        "version: '1.2'\npipeline: authored-film\n",
        encoding="utf-8",
    )
    candidates = [
        _headshot_candidate(project / "assets" / "images" / f"candidate-{number}.png", number)
        for number in range(1, 4)
    ]
    checkpoint = {
        "version": "1.0",
        "project_id": GATE_PROJECT_ID,
        "pipeline_type": "authored-film",
        "stage": "headshots",
        "status": "awaiting_human",
        "timestamp": "2026-08-30T00:00:00Z",
        "artifacts": {"headshot_packet": {
            "version": "1.0",
            "state": "pending",
            "characters": [{
                "entity_kind": "character",
                "entity_id": CHAR,
                "candidates": candidates,
                "rejection_notes": [],
            }],
        }},
    }
    _write_json(project / "checkpoint_headshots.json", checkpoint)
    checkpoint_digest = hashlib.sha256((project / "checkpoint_headshots.json").read_bytes()).hexdigest()
    request_path = project / ".gate-requests" / f"{GATE_REQUEST_ID}.json"
    _write_json(request_path, {
        "request_id": GATE_REQUEST_ID,
        "project_id": GATE_PROJECT_ID,
        "kind": "headshot",
        "stage": "headshots",
        "scope": f"character:{CHAR}",
        "entity_id": CHAR,
        "summary": f"Review three invented headshot candidates for {CHAR}.",
        "artifact": None,
        "approval_record": None,
        "source_checkpoint_digest": checkpoint_digest,
        "preview_paths": [],
    })
    os.utime(request_path, (1_700_000_000, 1_700_000_000))
    _write_json(project / "cost_log.json", {"entries": [
        {"status": "reserved", "reserved_usd": 0.0},
    ]})
    return project


def start_server() -> subprocess.Popen:
    env = dict(os.environ)
    env["OPENMONTAGE_PROJECTS_DIR"] = str(STAGE_DIR)
    # Session discovery is hermetic too; never consult a user's live broker.
    env["OPENMONTAGE_GATES_DIR"] = str(STAGE_DIR / ".golden-gates")
    server = subprocess.Popen(
        [sys.executable, "-m", "backlot", "serve", "--port", str(PORT)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1):
                return server
        except Exception:
            time.sleep(0.3)
    server.terminate()
    raise RuntimeError("Backlot server did not become healthy")


def capture_screenshot(url: str, output: Path, width: int, height: int, wait_ms: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "npx",
            "playwright",
            "screenshot",
            "--viewport-size",
            f"{width},{height}",
            "--wait-for-timeout",
            str(wait_ms),
            url,
            str(output),
        ],
        cwd=REPO_ROOT,
        check=True,
        timeout=120,
        shell=(os.name == "nt"),
    )


def capture_shots(capture_dir: Path) -> list[dict[str, Any]]:
    results = []
    for name, path, width, height, wait_ms, _masks in SHOTS:
        out = capture_dir / f"{name}.png"
        capture_screenshot(f"http://127.0.0.1:{PORT}{path}", out, width, height, wait_ms)
        results.append({"name": name, "path": out})
    return results


def compare_or_bless(capture_dir: Path, *, bless: bool, threshold: float) -> list[dict[str, Any]]:
    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    report = []
    for name, _path, _width, _height, _wait_ms, masks in SHOTS:
        actual = capture_dir / f"{name}.png"
        golden = GOLDENS_DIR / f"{name}.png"
        if bless:
            shutil.copyfile(actual, golden)
            report.append({"name": name, "status": "blessed", "golden": str(golden)})
            continue
        if not golden.exists():
            report.append({
                "name": name, "status": "missing", "passed": False, "golden": str(golden),
            })
            continue
        diff = capture_dir / "diffs" / f"{name}.png"
        result = compare_images(golden, actual, diff, threshold=threshold, masks=masks)
        result.update({"name": name, "diff": str(diff)})
        report.append(result)
    return report


def run_interactions(capture_dir: Path) -> dict[str, Any]:
    """Exercise the real UI with the repository's established npx browser."""
    screenshot = capture_dir / f"{GATE_SHOT_NAME}.png"
    project = STAGE_DIR / GATE_PROJECT_ID
    if not project.is_dir():
        raise RuntimeError("golden gate project has not been staged")
    capture_dir.mkdir(parents=True, exist_ok=True)

    playwright_bin = subprocess.run(
        ["npx", "--yes", "--package=playwright", "which", "playwright"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
        shell=(os.name == "nt"),
    ).stdout.strip()
    node_modules = Path(playwright_bin).parent.parent
    if not (node_modules / "playwright").is_dir():
        raise RuntimeError(f"npx Playwright package root is unavailable at {node_modules}")

    with tempfile.TemporaryDirectory(prefix="backlot-golden-") as temp_name:
        temp_dir = Path(temp_name)
        runner = temp_dir / "interaction.js"
        init_script = temp_dir / "hermetic-websocket.js"
        result_path = temp_dir / "result.json"
        runner.write_text(NODE_INTERACTION_SCRIPT, encoding="utf-8")
        init_script.write_text(HERMETIC_WEBSOCKET_INIT, encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "NODE_PATH": str(node_modules),
            "BACKLOT_BASE_URL": f"http://127.0.0.1:{PORT}",
            "BACKLOT_PROJECT_ID": GATE_PROJECT_ID,
            "BACKLOT_REQUEST_ID": GATE_REQUEST_ID,
            "BACKLOT_CHAR_ID": CHAR,
            "BACKLOT_SCREENSHOT_PATH": str(screenshot.resolve()),
            "BACKLOT_RESULT_PATH": str(result_path),
            "BACKLOT_INIT_SCRIPT_PATH": str(init_script),
            "BACKLOT_COST_LOG_PATH": str(project / "cost_log.json"),
            "BACKLOT_MASK_REASON": GATE_MASK_REASON,
        })
        subprocess.run(
            ["node", str(runner)],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            timeout=120,
        )
        return json.loads(result_path.read_text(encoding="utf-8"))


def compare_or_bless_interaction(
    interaction: dict[str, Any], *, bless: bool, threshold: float,
) -> dict[str, Any]:
    actual = Path(interaction["screenshot"])
    golden = GOLDENS_DIR / f"{GATE_SHOT_NAME}.png"
    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    if bless:
        shutil.copyfile(actual, golden)
        return {"name": GATE_SHOT_NAME, "status": "blessed", "golden": str(golden)}
    if not golden.exists():
        return {
            "name": GATE_SHOT_NAME,
            "status": "missing",
            "passed": False,
            "golden": str(golden),
        }
    diff = actual.parent / "diffs" / f"{GATE_SHOT_NAME}.png"
    result = compare_images(
        golden,
        actual,
        diff,
        threshold=min(threshold, 0.005),
        masks=interaction["masks"],
    )
    result.update({"name": GATE_SHOT_NAME, "golden": str(golden), "diff": str(diff)})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bless", action="store_true", help="Write current captures as goldens")
    parser.add_argument("--no-stage", action="store_true", help="Reuse existing .backlot/screenshot-stage")
    parser.add_argument("--interactions", action="store_true", help="Run Playwright interaction smoke")
    parser.add_argument("--threshold", type=float, default=0.015)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.no_stage:
        run_stage()
    stage_gate_project()

    stamp = datetime.now().strftime("visual-%Y%m%d-%H%M%S")
    capture_dir = args.out_dir or (CAPTURE_ROOT / stamp)
    capture_dir.mkdir(parents=True, exist_ok=True)

    server = start_server()
    try:
        capture_shots(capture_dir)
        report = compare_or_bless(capture_dir, bless=args.bless, threshold=args.threshold)
        interaction_report = run_interactions(capture_dir) if args.interactions else None
        interaction_comparison = compare_or_bless_interaction(
            interaction_report,
            bless=args.bless,
            threshold=args.threshold,
        ) if interaction_report else None
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    passed = all(item.get("passed", item.get("status") == "blessed") for item in report)
    if interaction_comparison:
        passed = passed and interaction_comparison.get(
            "passed", interaction_comparison.get("status") == "blessed"
        )
    payload = {
        "capture_dir": str(capture_dir),
        "shots": report,
        "interactions": interaction_report,
        "interaction_golden": interaction_comparison,
    }
    report_path = capture_dir / "report.json"
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
