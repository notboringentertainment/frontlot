# Plan: Backlot Gates — the visual side of authored-film gates, signed in an embedded terminal
_Locked via claudex-loop — by Claude + Ben, 2026-08-30. Revision 6 (rounds 1–5: 18 + 11 + 4 + 5 + 4 findings; all accepted; MAX_ROUNDS reached without a formal APPROVED — the round-5 items were all incorporated here; nothing is disputed — see log)._

## Goal

Every authored-film approval (headshot selection, sheet, QC override, look lock,
config, pipeline migration, and the rest of `APPROVAL_KINDS`) is judged today
from file paths printed in a terminal. Ben's ruling: visual output must be seen
and judged by a human, never trusted to AI vision reasoning, and the pipeline was
never meant to live in a terminal. This plan extends **Backlot** (the existing
local web board: `backlot/server.py`, `backlot/state.py`, `backlot/ui/`) so a
pending gate request is shown with its images and evidence, and the human signs
it by typing into a real `gate_approve.py` terminal embedded in the same page.
Token minting, receipts, `gate_approve.py` and the run scripts are unchanged.
Runs are not launched from the board; Claude Code is not embedded.

## Trust model (stated, not implied)

- Today: "only a human at a TTY can sign" is process discipline, not a privilege
  boundary (`gate_approve.py:20-22`, `lib/gates.py:26-38`); any process running
  as Ben could already drive `gate_approve` through a pty.
- With this plan: the board's terminal is a pty reached through a localhost
  websocket. A **per-server secret** protects it from drive-by browser access
  (any page you happen to have open, cross-site requests): the **server**
  generates a random 32-byte token at start and writes it to
  `~/.openmontage/backlot/<port>.token` (0600); `python -m backlot open` reads
  that file (works with an already-running detached server) and opens
  `http://127.0.0.1:<port>/#k=<token>` — a **fragment**, never a query string,
  so it does not reach history, referrers or logs; the page moves it to
  `sessionStorage` and scrubs the URL on load. The first websocket frame must
  carry it; no token, no pty. Origin must be `http://127.0.0.1:<port>` /
  `http://localhost:<port>`. Every page gets `Content-Security-Policy`
  (`default-src 'self'; img-src 'self'; connect-src 'self' ws://127.0.0.1:<port>
  ws://localhost:<port>; script-src 'self'`) and `Referrer-Policy: no-referrer`.
- **What the secret proves and does not prove.** It is capability / CSRF
  protection for the browser surface. It does **not** prove human presence: a
  process running as Ben can read the token file, exactly as it could already
  drive `gate_approve` in a pty today. The trust level is unchanged from the
  user's own Terminal.
- The `gate_approve.py` / `lib/gates.py` docstrings gain one sentence each:
  "Backlot's embedded terminal is a pty owned by `scripts/gate_sign.py`, a
  user process the board attaches to over a 0600 unix socket, guarded by a
  per-server secret against browser cross-origin access; it is the same
  process-discipline trust as the user's Terminal, not a fourth handler."
- The board's **state server never writes to a project directory and never
  calls code that can** (see "Read-only, enforced" below). Inside a project
  directory only two processes write: `gate_sign.py` (`.run-lease` only) and
  `gate_approve.py` (as the human's hands). The broker's socket, lock and logs
  live under `~/.openmontage/backlot/`, outside every project.

## Approach

### 1. State layer (`backlot/state.py`) — raw files only, no `construct()`

Add a `gates` section to `load_board_state()` for projects with `project.yaml`
(resolved through `lib.run_common.resolve_project_root`: slug grammar, no
symlinks). Legacy projects get `gates: null` and render as today.

**Read-only, enforced.** The state layer reads JSON/JSONL/PNG bytes directly. It
does **not** import `scripts.gate_approve`, does not call `construct()`,
`headshot_candidates()`, `verified_approvals()`, `find_approval()` or anything
in `lib.receipts` that may replay the WAL (`receipts.py:375, 414, 439, 507`
all write). A test hashes every file under a fixture project before and after a
full state build plus every detail fetch and asserts equality (Story-drive's
immutability test, ported).

`gates` carries **summaries only** (bounded, cheap; built on every refresh):
- pending / done / declined / abandoned request rows from
  `.gate-requests/{,done/,declined/,abandoned/}` — every path component from the
  project root down is checked with `lstat` and any symlink (directory or file)
  makes the whole `gates` section `error: "symlink in gate directory"`;
  regular files only, `request_id` validated by `validate_request_id` and required to
  equal the filename stem, `project_id` required to equal the slug, a request
  present in two directories is reported as `state: "conflict"` and not
  actionable. Row: `request_id, kind, stage, scope, entity_id, summary, state,
  mtime, approval_receipt_id?, declined_note?`.
- `next_command`: for each pending request, rendered from a new shared helper
  `lib.run_common.gate_invocation(root, request_id) -> {"cwd": REPO, "argv":
  [venv_python, "scripts/gate_sign.py", "--project", slug, "--request", rid]}`
  — **always the wrapper**, never `gate_approve.py` directly (the wrapper holds
  the run lease). `gate_command()` becomes `f"cd {shlex.quote(cwd)} &&
  {shlex.join(argv)}"`; the board's terminal launches the same argv (one source
  of truth; tests for both). The wrapper's own child command is a private helper
  inside `gate_sign.py`. **No** guessed run-script lines
  (orchestration rules live in the runners, `run_common.py:1`).
- `canon`: per entity, `hero` + present sheet roles from
  `lib.canon_view.plan_view(root)` tuples → `{entity, role, object_rel}`. Look
  is text only (active look hash + ticket ref from `checkpoint_look_lock.json`);
  `plan_view` has no look image (`canon_view.py:47`).
- `cost`: `cost_log.json` totals labelled "ledger" for authored-film projects;
  legacy keep the checkpoint `cost_snapshot`, labelled "snapshot".
- `run_lease`: `{held: bool, pid, started}` from `.run-lease` via
  `lib.run_lease` read helpers (`owner_is_dead` is read-only).

**Detail is lazy**: `GET /api/project/{id}/gate/{request_id}` builds one
**visual packet** on demand from raw files. Every kind in
`lib.receipts.APPROVAL_KINDS` has an entry in a renderer map; a test asserts the
map's keys equal `APPROVAL_KINDS` exactly. Packets are labelled
`snapshot_at` and carry `record_sha256: null` always — the digest the terminal
prints after the human's input is the only authoritative one (headshot needs a
selection, qc_override a typed reason before a record exists;
`gate_approve.py:1016, 722`).

| kind | packet (raw sources) |
|---|---|
| `headshot` | candidates `[1..N]` in the order `gate_approve` numbers them: read `checkpoint_headshots.json` → packet `characters[entity].candidates[]` → each candidate's exact path as the packet records it (`asset_id` → `canon/visual/objects/<asset_id>.png`, or the staging path it names) and **the file's sha256 must equal the candidate's `asset_id`/`normalized_pixel_hash`** else that candidate is shown as `hash mismatch` and the packet is flagged `packet_error`; `generator_kind`, sha prefix; `rejected_candidates` count. Missing/mid-write checkpoint → `packet_error`. |
| `sheet` | **from the draft entry, not `preview_paths`**: `checkpoint_visual_bible.json` → `characters[entity]` with `sheet_revision == request revision` → `sheet[role].asset_id` → `canon/visual/objects/<asset_id>.png`, and the file's sha256 must equal `asset_id` (objects are content-addressed) else `packet_error`; per-role QC rows from `qc-receipts.jsonl` matched by the entry's `qc_receipts[role]` receipt id — raw rows, labelled "unverified". |
| `qc_override` | image resolved from the request's `qc_receipt_id` → the QC row's asset id → vault object, hash-checked; `item_ids`; note "a typed 10+ character reason is required in the terminal". |
| `reference_import` | the request's `normalized_pixel_hash` → the staged PNG under `.import-staging/`, hash-checked; `origin_class`. |
| `look_lock` | `source_ticket_ref`, `look_hash`, `supersedes`; the ticket path is resolved through the existing `lib.look_ingest.confine_ticket_path()` contract and rendered as text only if it stays inside the configured wayfinder root, else "ticket outside wayfinder root". |
| `config` | `project.yaml` text with the `summary`. |
| `pipeline_migration` | from/to versions from the request. |
| `hero`, `location`, `poster`, `storyboard_batch` | **visual, per-kind extractors**: each mirrors the authoritative refs its constructor in `gate_approve.py` signs (`hero`: the packet's hero `asset_id`; `location`: the bible location entry's role refs; `poster`: `poster.{key_art,title_card,poster_final}` asset ids; `storyboard_batch`: the batch's frame asset ids from the storyboard checkpoint) → vault object, sha256 == asset id else `hash mismatch`/`packet_error`. One fixture test per kind. The exact ref paths are read from each constructor during the build and recorded in the renderer map's docstring. |
| `headshot_grandfather`, `artifact_review` | text: summary, the request JSON (envelope hints excluded), `preview_paths` as file names only. `artifact_review` gains an image extractor only if its constructor cites image asset ids (checked during the build). |

**Done / declined / abandoned** detail never reconstructs current state
(`gate_approve.py:279` constructors require `awaiting_human`): it shows the
archived request, the marker fields (`approval_receipt_id`, `declined_note`),
and the matching raw row from `approvals.jsonl` by `receipt_id` (labelled
"unverified ledger row"; verification stays with enforcement).

### 2. Read routes (`backlot/server.py`) — no write routes

- `/api/project/{id}/state` embeds the `gates` summaries; SSE `change` covers
  them (the watcher already watches the whole project dir).
- `GET /api/project/{id}/gate/{request_id}` — lazy packet (above).
- `/media` and `/thumb` unchanged; `_safe_project_dir` switches to
  `resolve_project_root` semantics (reject symlinked projects, enforce slug).
- Server logs (lifecycle, pty open/close/exit reason, never keystrokes or
  output) go to `~/.openmontage/backlot/server.log`; `open` no longer discards
  stderr (`__main__.py:38`) — it redirects to that file.

### 3. Signing terminal (`backlot/tty.py`, new)

- `WS /api/project/{id}/gate/{request_id}/tty`. Handshake: first frame must be
  `{"k": <token>}`; else close `4401`. Then, **before any await that yields**,
  reserve the project under an `asyncio.Lock`-guarded registry; if a live
  session exists for the project → close `4409` ("a signing terminal is already
  open for this project"). Single-worker invariant: `serve` runs one uvicorn
  worker; documented and asserted at startup.
- Preconditions (all before spawn): request loaded from `.gate-requests/<id>.json`
  (pending only, regular file, no symlink component, `request_id` == stem,
  `project_id` == slug, unique state).
- **`scripts/gate_sign.py` owns the session, the server only attaches.** The
  wrapper is a small broker (~150 lines, stdlib only):
  1. `with run_lease.acquire(root, minutes)` — if the lease is live it prints
     "a run holds the project lease (pid N); wait for it to block or finish" and
     exits 5 (closes the lease TOCTOU: the signer holds the lease through
     display and commit, the server never touches `.run-lease`).
  2. `flock` on `~/.openmontage/backlot/sessions/<slug>.lock` for its lifetime
     — one signing session per project across every server process.
  3. When run **from a Terminal** (stdin is a TTY): it runs `gate_approve.py`
     as a child inheriting the terminal and waits for it **inside** the lease
     and flock contexts (never `exec`, so `Lease.__exit__` releases
     `.run-lease`); exits with the child's code.
  4. When run **by the board** (`--broker`): it creates the pty
     (`pty.openpty()`), starts `gate_approve.py` on the slave
     (`start_new_session=True`, argv only, cwd=REPO, login-shell PATH + TERM),
     and listens on a unix socket `~/.openmontage/backlot/sessions/<slug>.sock`
     (0600, dir 0700) and writes a sidecar `<slug>.session.json` (0600) with
     `{project, request_id, session_id, pid, started}` so a restarted server can
     **list** open sessions and their request ids without attaching; the
     sidecar is advisory — `HELLO` is the verified identity and must match it,
     else the panel shows "session identity mismatch" and refuses attach.
     **Stale sockets and sidecars**: under the project flock (step 2) the
     broker unlinks any existing socket file before binding — a socket without
     a lock holder is by definition dead. **IPC framing** (both directions):
     length-prefixed typed frames `u8 type | u32 len | payload` with hard
     limits (`IN` ≤ 4 KB, `OUT` ≤ 64 KB, JSON frames ≤ 4 KB; unknown type,
     oversized or truncated frame → `BYE reason=protocol` and close; tests
     feed fragmented and coalesced streams); types `HELLO`
     (broker→client, JSON `{project, request_id, session_id, pid,
     lease_held: bool, signer: "starting"|"running"|"exited", exit_code?}` —
     `lease_held` is independent of the signer state), `IN` (client→pty
     bytes), `OUT` (pty→client bytes, sanitised), `RESIZE` (JSON cols/rows,
     bounded), `STATUS` (broker→client on state change), `BYE`. The client
     must send its own `HELLO` `{project, request_id, token_ok: true}` first;
     a mismatched project/request is refused with `BYE reason=mismatch`.
     Exactly one client at a time; the broker keeps a 64 KB `OUT` replay
     buffer for reconnects. The broker exits when `gate_approve` exits,
     unlinks its socket, releases the lease and lock, and logs the exit code.
- The server's websocket handler is a **client** of that socket: on connect it
  tries to attach; if refused with "no socket" it spawns the broker
  (`Popen(argv + ["--broker"], start_new_session=True)`, detached from the
  server's process group) and retries the attach with backoff for up to 5 s
  (broker readiness); two websockets racing to start a broker both retry, one
  attaches, the other gets `4409`. **Server shutdown closes only the client
  connection**; the broker, the pty and the signer keep running, and a
  restarted server re-attaches. No master fd or lock lives in the server.
- **Visual/signing order (closes the display TOCTOU):** the terminal panel's
  input stays disabled until the broker's `HELLO` reports `lease_held`; the
  server then **re-fetches the packet** and pushes it to the page as
  `packet_refreshed` (so candidate numbers and hashes are read while the lease
  is held and no runner can rewrite the checkpoint); until that push completes
  the **server drops every `IN` frame** (not just the page disabling input).
  Regression test: mutate the checkpoint between first display and
  `lease_held`, assert the refreshed packet differs and that bytes sent before
  the refresh never reach the pty. If the refreshed packet differs from the displayed one the page says
  so and shows the new one.
- Framing: binary frames = raw bytes both ways; text frames = JSON control
  (`resize {cols 10..500, rows 3..300}` → `TIOCSWINSZ`). Output relayed with a
  bounded queue (backpressure: pause reading the master fd when the socket
  buffer exceeds 256 KB). A 64 KB replay buffer lets the page reconnect to a
  still-running session (same token, same request).
- Output sanitising is **incremental**: a small state machine (`backlot/ansi.py`)
  that carries partial escape sequences and partial UTF-8 code points across
  reads, keeps printable text, `\r`, `\n`, `\t`, `\b` and the SGR subset
  (`ESC [ … m`), and drops every other CSI/OSC/DCS sequence. Tests split hostile
  payloads at every byte boundary and assert identical output.
- **Nothing is ever typed into the signer by software and it is never
  signalled.** `q` is not safe at every prompt (at an optional-note prompt it
  becomes the note and the approval proceeds, `gate_approve.py:1461, 1490`), so
  there is no auto-cancel. A detached session stays reconnectable for as long
  as the signer lives; the Gates panel lists open sessions per project
  ("signing session open since 14:02 — reconnect") from the socket files. The
  human ends a session the same way as in Terminal: answer the prompt, or `q`
  at a decision prompt. Crash-safety is exactly today's Terminal behaviour;
  making every kind's request completion crash-recoverable stays out of scope.

### 4. UI (`backlot/ui/`) — framework-free, xterm.js vendored

- **Gates panel** on the project page: rows with state chips; selecting one
  fetches the lazy packet: summary, images at 640px (click → `/media` full),
  per-role QC rows, evidence fields, `snapshot_at`, and a **Start signing**
  button. Headshot candidates are numbered `[1]..[N]` exactly as the terminal
  prompts. Done/declined/abandoned rows are read-only with receipt id / note.
- **Terminal mount** lives in a stable DOM node **outside** the subtree the SSE
  refresh replaces (`board.js:533, 549, 713, 1054` clear and rebuild); the
  session object owns the xterm instance; a refresh re-attaches the existing
  terminal rather than recreating it. Test: SSE `change` during an active
  session leaves the terminal and its websocket intact.
- Busy states: lease held → button disabled with the reason; session open →
  "signing in progress" with the request id; stale packet (a refresh moved the
  request out of pending) → packet greyed, terminal stays.
- **Canon strip**, **Cost** tile, **Next command** line with copy button.
- Replace "reply in chat to approve" (`board.js:111, 887`) with the Gates CTA.

### 5. Tests

- state: fixture authored-film project (pending headshot with 3 candidates,
  pending sheet, done sheet with receipt row, declined override, abandoned
  sheet, conflict request, symlinked request file skipped); `gates: null` for
  legacy; renderer map keys == `APPROVAL_KINDS`; `packet_error` on a truncated
  checkpoint; canon rows == `plan_view`; cost labels; `next_command` ==
  `shlex.join` of the gate argv; **immutability hash test** across state build +
  every detail fetch.
- tty: missing/wrong token → 4401; bad origin refused; unknown / non-pending /
  cross-project request refused; live lease → 4423; second session → 4409;
  resize bounds; sanitiser drops OSC/CSI and keeps SGR; disconnect does not
  kill the child, reconnect replays from the broker buffer; server restart
  re-attaches to a live broker; second client refused by the broker; broker
  flock refuses a second session across processes; stale socket removed under
  the flock before bind; two clients racing to start a broker → one attaches,
  one 4409; HELLO mismatch refused; no input is ever written by
  the server or broker on disconnect/shutdown (asserted with a fake signer that
  fails on any unexpected byte); sanitiser state machine across split
  boundaries; bytes round-trip against a fake `gate_approve` that asserts
  `isatty()` and exits on `q`; broker exit unlinks socket, releases lease+lock.
- **One hermetic real-signer test**: a temporary project + `OPENMONTAGE_GATES_DIR`,
  a pending `config` request, the real `scripts/gate_sign.py` → `gate_approve.py`
  under `openpty`, driven with `y`, `\n` (empty note): asserts both TTY checks
  pass, a receipt lands in `approvals.jsonl`, the request moves to `done/` with
  `approval_receipt_id`, and the run lease is released afterwards.
- `gate_sign.py`: refuses (exit 5) when the lease is live; releases on exit;
  foreground (TTY) mode runs `gate_approve` as a child and waits inside the
  lease/flock contexts; `--broker` mode as above.
- `gate_command` quoting test; existing 6 backlot tests + contract test green;
  `make test` green (baseline 1875).
- Playwright golden: open a pending headshot gate → three candidate images and
  the terminal panel; SSE refresh during a session keeps the terminal.

### 6. Docs

`backlot/README.md` (disk sources, trust model, the terminal);
`AGENT_GUIDE.md:225` one line ("gates are signed in the board's terminal or in
your own; the command is the same either way"); the two docstring sentences from
the trust model; `.claude/commands/backlot.md` unchanged.

Order of work: 1 → 2 → 5(state) → 3 → 5(tty) → 4 → 5(golden) → 6.

## Key decisions & tradeoffs

- **D1 Embedded terminal, not an Approve button** (Ben). Codex's simpler
  alternative — read-only panel + validated "copy command" — is recorded; Ben
  chose embedding. The per-launch secret is what makes the pty *his* terminal.
- **D2 Browser tab, not Electron** (Ben). The pty lives in `gate_sign.py`; the
  Python server is only a websocket↔unix-socket relay.
- **D3 No run launching** (Ben). Copy-button handoff only.
- **D4 Claude Code not embedded** (Ben).
- **Raw files, never `construct()`** (revision 2). Removes the WAL-replay
  write path, the `awaiting_human` coupling for archived gates, the
  input-dependent digest problem, and the timeout-around-a-thread problem in one
  move; the cost is that the board's QC rows and ledger rows are unverified
  snapshots, labelled as such. Verification stays where it is: in the terminal
  and in enforcement.
- **Signer owns its session; the server is a client** (revision 4). A killed
  signer mid-commit is worse than a detached one; there is no timeout — a
  detached session is listed in the panel until the human answers it.
- **Refuse to sign under a live run lease** (revision 2). `receipt_lock` does
  not stop a runner rewriting checkpoints during a decision; the runners
  release the lease when they block for a gate, so the refusal costs nothing in
  the normal flow and closes the race in the abnormal one.

## Toolchain

Claude build track: `frontend-design` for the Gates panel / canon strip;
`web-design-guidelines` for the UI review pass. Not loaded: `tailwind-design-
system`, `vercel-react-best-practices` (no React/Tailwind/bundler). No generator
skills, no MCP.

## Assumptions (confirmed ledger 2026-08-30; corrections from round 1 applied)

1. Single user, single Mac, localhost. 2. Backlot is the base. 3. Backlot is
gate-blind today. 4. "Server never writes" — kept and now enforced by test.
5. Signing is TTY-only by process discipline; "Backlot handler" in PLAN.md §5
was never built. 6. A pty running `gate_approve` satisfies every check.
7. Visual needs per kind — now exhaustive over `APPROVAL_KINDS`. 8. No script
prints JSON; the board reads raw files (not `construct()`). 9. Two project
generations. 10. Runners release the lease when they block; signing under a
live lease is refused. 11. Story-drive shell not needed. 12. Skills as above.
13. Original Backlot design docs missing; code is the spec. 14. `construct()`
and receipt readers can write (WAL replay) — the reason for 8.

## Risks / open questions

- R1 Reading `checkpoint_headshots.json` mid-write → `packet_error`; the
  watcher debounce (existing) makes this rare; the page retries on next change.
- R2 xterm.js under Backlot's no-cache middleware / any CSP: confirm
  `/ui/vendor/` and websockets load; add a CSP if none exists (`connect-src`
  self+ws, `img-src` self).
- R3 Login-shell PATH merge is needed if the board is ever launched from Finder.
- R4 A human closing the tab mid-prompt leaves a live session holding the run
  lease until they reconnect and answer; the panel shows it prominently.
- R5 POSIX only (`openpty`); Backlot is macOS-first.

## Out of scope

In-page Approve button / fourth handler; launching runs; embedding Claude Code;
Electron/Tauri, notarization, distribution; any change to receipts, tokens,
`gate_approve.py` prompts or run scripts beyond the two docstring sentences,
the `gate_invocation` helper and the `gate_sign.py` lease wrapper; making
non-sheet request completion crash-recoverable; video/storyboard stages beyond
what Backlot renders.
