import { test } from "node:test";
import assert from "node:assert/strict";
import { apply, initialModel } from "../../backlot/ui/session_model.js";

const hello = { epoch: "e1", sessionId: "s", source: "launch", cwd: "/w", claudeVersion: "2.1.294", history: [] };
const live = () => apply(initialModel(), { kind: "addon-hello", hello }, 1);

test("assistant row appends readable text", () => {
  const m = apply(live(), { kind: "row", epoch: "e1", uuid: "u", door: "d", type: "assistant", role: "assistant",
    origin: { kind: "model", model: "x" }, blocks: [{ type: "text", text: "Two looks are ready." }] }, 2);
  assert.equal(m.feed.at(-1).kind, "prose");
  assert.equal(m.cursor, 2);
});

test("a permission wait flips to the terminal", () => {
  const m = apply(live(), { kind: "waiting-for-input", epoch: "e1", requestId: "w1", reason: "permission", detail: "Claude wants to use Bash" }, 2);
  assert.equal(m.view, "raw");
});

test("cards follow the request to its outcome", () => {
  let m = apply(live(), { kind: "spend-request", requestId: "r-1", summary: "Make 3", entity: "hero-a", estimate_usd: 0.36, paid: true, state: "waiting-for-ben" }, 2);
  m = apply(m, { kind: "spend-decided", requestId: "r-1", state: "approved" }, 3);
  m = apply(m, { kind: "run-started", requestId: "r-1", state: "running" }, 4);
  assert.equal(m.cards["r-1"].state, "running");
  m = apply(m, { kind: "run-finished", requestId: "r-1", summary: "Make 3", entity: "hero-a", paid: true, state: "done" }, 5);
  assert.equal(m.cards["r-1"].state, "done");
  assert.equal(m.feed.at(-1).kind, "run");
});

test("a snapshot rebuilds rows and cards", () => {
  const m = apply(initialModel(), { kind: "snapshot", state: "ready", hello, cursor: 40,
    rows: [{ kind: "row", epoch: "e1", uuid: "u", door: "d", type: "assistant", role: "assistant", origin: { kind: "model" }, blocks: [{ type: "text", text: "Back." }] }],
    cards: [{ requestId: "r-2", summary: "Make 1", state: "uncertain", paid: true }] }, null);
  assert.equal(m.cursor, 40);
  assert.equal(m.feed.at(-1).kind, "prose");
  assert.equal(m.cards["r-2"].state, "uncertain");
});

test("no add-on hello falls back to the terminal with a note", () => {
  const m = apply(initialModel(), { kind: "addon-missing" }, 1);
  assert.equal(m.view, "raw");
  assert.ok(m.notice);
});

test("an add-on reload keeps the cards", () => {
  let m = apply(live(), { kind: "spend-request", requestId: "r-1", summary: "Make 3", paid: true, state: "waiting-for-ben" }, 2);
  m = apply(m, { kind: "spend-decided", requestId: "r-1", state: "cancelled", reason: "addon-reload" }, 3);
  m = apply(m, { kind: "addon-hello", hello: { ...hello, epoch: "e2", source: "reload" } }, 4);
  assert.equal(m.cards["r-1"].state, "cancelled");
});

test("a client-only notice (seq null) shows the note and keeps the cursor", () => {
  const m = apply(apply(live(), { kind: "row", epoch: "e1", type: "assistant", role: "assistant", blocks: [{ type: "text", text: "x" }] }, 7),
    { kind: "notice", plain: "Another window took control." }, null);
  assert.equal(m.notice, "Another window took control.");
  assert.equal(m.cursor, 7);
});

test("Ben's own message from the Front Lot add-on shows in the feed", () => {
  // Real origin from the bloodless run: the add-on is frontlot-live, not Story-drive's story-drive-live.
  const m = apply(live(), { kind: "row", epoch: "e1", uuid: "u", door: "prompt", type: "user", role: "user",
    origin: { kind: "plugin", name: "frontlot-live", asUser: true }, blocks: [{ type: "text", text: "Make one take." }] }, 2);
  assert.equal(m.feed.at(-1).kind, "you");
  assert.equal(m.feed.at(-1).text, "Make one take.");
});
