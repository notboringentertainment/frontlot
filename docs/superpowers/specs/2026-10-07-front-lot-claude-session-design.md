# Front Lot: a Claude session in the log column

Date: 2026-10-07
Status: design approved by Ben in conversation, section by section; revision 3 after Codex rounds 1-2 (see REVIEW-LOG)
Branch: `front-lot-redesign` (worktree `~/Projects/OpenMontage-worktrees/front-lot-redesign`)

## 1. Intent

Each of Ben's apps has one job: Story-drive drives the beat sheet, WriterOS defines what images should look like, Front Lot makes the images. Today Front Lot only *shows* the work: image sessions run in a separate Claude Code terminal and their results appear on the board. This build puts that Claude session inside Front Lot, in the log column of the new "Cutting Room" design, so Ben makes images without leaving the app.

Ben's confirmed answers:

- One ongoing conversation per film that picks up where it left off; a "New conversation" action starts fresh.
- Claude opens (and re-opens) with a short check-in: what is new and ready to make, then waits.
- Before anything paid, a spend card with **Go / Not now**. Nothing paid starts without Go. Signing stays in the separate signing program, answered only by Ben.
- Approach A: Story-drive's pattern (Claude runs hidden; an add-on reports events; Front Lot draws the conversation in its own design; raw terminal as fallback).

Out of scope: Front Lot's own app window (next build); any change to WriterOS or Story-drive; any change to signing (`gate_sign.py` / `gate_approve.py`) beyond tests that prove the trust boundary.

## 2. What Ben sees (log column)

Top to bottom:

1. **Needs you**: pending approvals, unchanged from the current redesign.
2. **Conversation**: Claude's replies as readable text in the Cutting Room style. Tool activity collapses to one quiet line per step in plain words; clicking a line expands the detail. When pictures land, they appear in the viewer and the conversation links to them.
3. **Spend cards**: inline, when Claude asks Front Lot to run something paid: what, for whom, cost estimate (or "cost unknown"), yellow **Go**, quiet **Not now**.
4. **Composer** at the bottom, with **Stop** while Claude is working.
5. **Show terminal** switch: flips the column to the raw Claude terminal. Flips automatically when Claude needs something the column can't draw (permission prompts other than spend, its own question dialogs, slash commands, version too old, add-on silent), and back when resolved.
6. **History** drawer: the current "The log" (decided approvals) and "Production decisions" move here.
7. **Signing bay** stays docked at the foot of the column, unchanged.

Session states shown in plain words: Starting, Ready, Working, Waiting for you (spend card / terminal), Ended (with **Pick back up**), Unavailable (with the one fix: sign in to Claude Code, update Claude Code).

## 3. Architecture

Revision 2 rests on one rule: **Claude talks and plans; Front Lot runs the pipeline.** The embedded Claude works inside Claude Code's OS sandbox with no access to signing material and no route to paid services. Every pipeline run script (free or paid) is executed by Front Lot on Claude's request; paid ones only after Ben's Go. This replaces the revision-1 idea of steering Claude's own Bash with permission prefixes, which Codex showed cannot enforce "nothing paid without Go" or "never signs".

### Threat model

The embedded Claude is treated as a **capable, cooperative agent that can make mistakes, misread instructions, or improvise around obstacles**, as agents do. The design must make it impossible for such an agent to sign approvals or start paid work without Ben's Go *through any ordinary or improvised path* (other script names, Python imports, direct API calls, editing code, editing inputs after a card is shown). It is not designed against a deliberately hostile program with OS exploit capability running as Ben; OpenMontage's own approval layer states the same scope (`lib/gates.py` trust note). Every restriction below names what it protects.

Baseline, stated honestly: Ben's current separate Claude terminal runs as Ben with full access to the signing key (`~/.openmontage/gates/key`, see `lib/gates.py` trust note) and provider keys. This design is strictly stronger than today's workflow, not merely equal.

### 3.1 Session broker (one per film)

A detached Python process per film, `scripts/claude_session.py --broker --project <slug>`, spawned by the Front Lot server (`start_new_session=True`, stdio DEVNULL). It owns **everything that must survive a Front Lot server restart**:

- the PTY running `claude` (raw terminal, 64 KB replay buffer, existing `HELLO/IN/OUT/RESIZE/STATUS/BYE` framing),
- the add-on's live endpoint (§3.2) and its state: epoch, last accepted seq, the inbox queue, pending spend requests,
- a bounded event journal `~/.openmontage/backlot/claude/<slug>.events.jsonl` with monotonically increasing broker sequence numbers.

The Front Lot server attaches to the broker over the broker's 0600 unix socket and subscribes with `resume_from=<broker seq>`; after a server restart it re-attaches and receives everything after the last seq it delivered. If the requested seq is older than the journal retains, the broker answers with an authoritative state snapshot (session state, open requests, recent conversation) plus a new cursor. The add-on's `/hello` epoch is unaffected because its endpoint lives in the broker, not the server.

Launch details:

- Executable pinned: resolve `claude` once (saved path, `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`), record its absolute path and version in the sidecar; preflight `--version` ≥ 2.1.288 and `auth status --json`.
- Environment built from an allowlist (HOME, USER, LANG, login-shell PATH, TERM=xterm-256color, the two FRONTLOT_LIVE_* vars), never `os.environ.copy()`: no `CLAUDECODE` / `CLAUDE_CODE_*` (Story-drive spike: these break transcripts and resume), no provider API keys.
- Command: `<claude> --plugin-dir backlot/claude_mod --strict-mcp-config --mcp-config <empty-but-for-mod> --settings <generated> --append-system-prompt-file <brief> --session-id <uuid> "<check-in prompt>"`; resume uses `--resume <uuid>` with a short pick-back-up prompt. `--strict-mcp-config` keeps Ben's other MCP servers (several can spend, e.g. image generators) out of this session.
- cwd: repo root.

Process ownership and shutdown:

- `claude` runs in its own process group led by the broker's child. **End** / **New conversation**: SIGHUP, wait 5 s, SIGTERM the group, wait 5 s, SIGKILL the group; reap the exact child pid; then unlink socket and sidecar. A replacement session for the film is refused until the previous broker has exited and its sidecar is gone.
- **Identity before publication**: the session uuid, broker pid, Claude pid and process-group id are written to `~/.openmontage/backlot/claude/<slug>.json` under the film's lock *before* the sidecar is published. Before any resume or replacement, the server reconciles under the same lock: a recorded process group still alive with no broker is terminated with the same escalation, stale sidecars are removed only after that group is confirmed gone.
- Paid jobs are **not** children of Claude or the broker (§4.2); ending a conversation never kills a paid job. End/New while a paid job runs is allowed; the job continues, its record stays, and the next conversation's check-in reports it.

### 3.2 Add-on (Story-drive's live mod, vendored)

- Copy `~/Projects/story-drive/mod/story-drive-live/` into `backlot/claude_mod/`; env vars renamed `FRONTLOT_LIVE_SOCKET` / `FRONTLOT_LIVE_TOKEN`; Story-drive untouched.
- Unchanged: `/hello`, `/report` (seq-contiguous, `{acceptedThrough}`), `/ping`, `/inbox` long-poll, `/inbox-ack`; events `row`, `delta`, `turn`, `tool`, `waiting-for-input`, `input-done`, `session-end`, `queue-overflow`; submit via `$.prompt.submit`, stop via `$.turn.abort`; slash commands routed to the terminal view.
- **New MCP tool `frontlot_run`** registered by the add-on (Story-drive's `mark` tool shows the registration pattern). Input: `{script, args[], entity?, why}`. The tool makes a direct, idempotent `POST /run` to the broker (not through the display event queue, which may drop events) and returns only after the broker has durably written the request, with `{requestId, status}`; if the broker does not acknowledge within 5 s the tool returns `status: "not-received"` and Claude is told plainly:
  - free script → `status: "running"`; the outcome arrives later as a submitted message,
  - paid script → `status: "waiting-for-ben"`; Claude is told Ben will see a spend card and must wait,
  - not on the allowlist → `status: "refused"` with the reason.
  No hook ever blocks waiting for Ben, so the 10-second function-hook budget (Codex round 1, #4) does not apply.
- Outcomes return to Claude as a submitted user-role message from Front Lot (`"[Front Lot] Run r-12 finished: 3 headshot candidates for Ivy are on the board."` / `"Ben said Not now to r-12."`), queued through the existing inbox so delivery and acks follow Story-drive's proven path.
- The add-on's `classic.PermissionRequest` handler keeps Story-drive behaviour (report a wait, let the TUI handle it → terminal view). It is not used for money.

### 3.3 Front Lot server

New module `backlot/claude_live.py`:

- Spawns/attaches brokers; relays broker events to pages; no live-channel state of its own beyond delivery cursors.
- Page endpoints (same origin check + capability token handshake as the signing WebSocket):
  - `GET /api/project/{p}/claude` → session state + recent events.
  - `WS /api/project/{p}/claude/live` → events; page actions `submit`, `stop`, `spend-decision`, `start`, `resume`, `new`, `end`, `take-control`.
  - `WS /api/project/{p}/claude/tty` → raw terminal (xterm passthrough, not `AnsiSanitizer`).
- **Controller lease** (Codex #8): one controlling page per film across both sockets. Every mutating action and every keystroke from a non-controller is rejected server-side. Another tab sees the conversation read-only with a "Take control" button that transfers the lease (the previous controller is told and becomes read-only). A lease with no live socket expires after 30 s.

### 3.4 Page

- `backlot/ui/session.js`: live socket, a plain-JS port of Story-drive's pure reducer (`src/live/model.ts`), conversation rendering, spend cards, composer, Stop, Show terminal, states, read-only mode.
- Raw terminal: one page-lifetime xterm, never replaced by render/SSE (existing pattern).
- When a run finishes for an entity, the bin selects it so new frames land in the viewer.
- Styling per `backlot/DESIGN.md`.

## 4. Signing and money

### 4.1 Claude's sandbox (enforced, not advisory)

Front Lot writes a per-session settings file (`--settings`) that:

- enables Claude Code's sandbox for Bash with `allowUnsandboxedCommands: false` (no escape hatch);
- **denies reading**: `~/.openmontage/**` (signing key, ledgers, WALs, consumed tokens, signer sockets under `backlot/sessions`, the session brokers' sockets), the repo's `.env*`, credential files (`tools/google_credentials.py` sources, `~/.config/gcloud`, any provider key files found during build);
- **denies writing** everywhere except a per-film work area (`projects/<film>/frontlot-work/`) and the session's scratch dir. In particular no writes to the trusted runtime (`scripts/`, `lib/`, `tools/`, `backlot/` incl. the vendored add-on, `pipeline_defs/`, `skills/`, `.venv/`, repo config), to `~/.openmontage/**`, or to any project record (`.gate-requests/`, `canon/`, checkpoints, ledgers, run settings). Protects: code Front Lot executes with Ben's privileges, and the records approvals rely on.
- **network**: localhost only (WriterOS reads on 5177, Front Lot); no provider domains;
- the same denies as permission rules for Claude's own Read/Edit/Write tools;
- MCP: only the add-on's `frontlot_run` (strict MCP config).
- **No inherited extensions**: launched with `--setting-sources` excluding user and project settings, so Ben's own hooks and plugins (which run outside the Bash sandbox) are not loaded; only the generated settings and the vendored add-on run. The add-on is Front Lot's own code in the write-protected runtime.
- **Fail closed**: the broker refuses to start the session unless a startup self-check confirms the sandbox is active (probe P1 defines the check).

What the limit protects (Ben's "don't cripple agents" rule): signing authority and money. Claude can still read every film file, the canon packet, look specs, and pipeline docs, plan, write its replies, and get any pipeline step run through `frontlot_run`.

### 4.2 Run executor (Front Lot runs the pipeline)

- Allowlist of scripts and argument shapes, each tagged **free** or **paid**, built from the repo during step 1 of the build: e.g. `look_run.py` (free), any `--dry-run` form (free), status/inspect commands (free), `headshot_run.py` candidates (paid), `sheet_run.py` (paid), `supervised_shot generate` (paid), QC/judge steps that call paid models (paid). Unknown → refused.
- **Film bound by Front Lot**: the executor inserts `--project <film>` itself and rejects any project argument from Claude. Every path argument is resolved and checked against the operation's permitted roots (the film's folder, the work area); anything else is refused. Protects: one film's approval spending on another, and the executor reading what Claude itself cannot.
- **Frozen inputs**: when a request is created, the executor resolves every input the operation reads (argv plus declared input files such as a shot's settings JSON) and records their sha256. The card describes those inputs. On Go, the executor re-hashes them; any change refuses the run and asks Ben again. Protects: a card approving one thing while another runs.
- Free requests run at once. Paid requests become a **spend card**. The card's estimate comes from the script's own dry-run/estimate where available; otherwise "cost unknown".
- Execution: a detached wrapper `scripts/frontlot_run.py` runs the exact argv recorded in the request (outside the sandbox, as Ben, with the normal environment and keys), with the repo's existing run lease taken by the run script itself. The wrapper writes a run record and outcome file, so outcomes survive server and broker restarts.
- Jobs are never children of Claude; Stop does not kill them; their own existing stop paths apply (e.g. `supervised_shot stop`).

### 4.3 Spend request state machine (Codex #5, #7, #11)

States: `waiting-for-ben` → (`approved` → `running` → `done` | `failed` | `uncertain`) | `declined` | `cancelled` | `expired`.

- Bound to `{film, session uuid, broker epoch, requestId, sha256 of the exact argv}`; the card shows what that argv will do in plain words.
- Exactly one decision accepted, from the controller, and only while `waiting-for-ben`; duplicates and stale decisions are rejected and logged.
- **Stop**, a new add-on epoch (plugin reload), New conversation, End, or broker exit → every request not yet started becomes `cancelled`; already running jobs continue. Requests expire after 30 minutes unanswered.
- **Exactly once**: on Go the executor creates `runs/<requestId>.claim` with `O_CREAT|O_EXCL` (a second claimant fails), writes `launching`, spawns the wrapper, then records the wrapper pid and `running`. Reconciliation after any crash: claim without pid → `uncertain (may not have started)`; pid gone without outcome → `uncertain`. Uncertain is shown to Ben plainly, never retried automatically, never shown as success.
- Spend log `~/.openmontage/backlot/claude/<slug>.spend.jsonl`: every transition with time, request identity, argv hash, plain summary, cost shown, decision, outcome.

### 4.4 Brief given to Claude

`--append-system-prompt-file`: what Front Lot is; which film; the check-in task; that pipeline steps go through `frontlot_run`; that paid steps wait for Ben's Go and a Not now is a decision; that signing is Ben's; plain-language replies (no hashes, IDs, machine lines). No story content in the brief.

## 5. Failure handling

| Situation | What Ben sees | Mechanism |
|---|---|---|
| Claude exits / crashes | "The session ended" + Pick back up | broker STATUS; resume with stored uuid; open requests cancelled |
| Add-on silent > 15 s / no hello in 8 s | Terminal view + note | Story-drive timeouts |
| Claude Code too old / signed out | Unavailable + the one fix | preflight |
| Front Lot server restarts | Page reconnects, nothing lost | broker owns live state; server resumes from broker seq |
| Broker dies | "The session ended"; paid jobs unaffected | jobs are detached with their own records |
| Run lease held by a production run | Executor reports "busy" to Claude in plain words | existing `hold_lease` refusal |
| Stop | Current turn aborted; running jobs continue | `$.turn.abort`; jobs detached |
| Two tabs | Second is read-only with Take control | controller lease |
| Go and Not now race / double click | First valid decision wins; the other is rejected | state machine |

## 6. Build order and tests

1. **Probes (free, no product code):**
   - P1 sandbox: with real `claude` 2.1.293 + generated settings in a throwaway session, prove that Bash and Read cannot read `~/.openmontage/gates/key`, cannot connect to a signer socket under `~/.openmontage/backlot/sessions/`, cannot read `.env`, cannot reach a provider domain, and that `dangerouslyDisableSandbox` is refused. Any failure stops the build and returns to design.
   - P2 add-on: the vendored mod registers `frontlot_run`, the call returns immediately, and a later inbox `submit` delivers an outcome message into the conversation.
   - P3 resume + env: a session launched from a Claude Code shell with the allowlisted env writes a transcript and resumes.
   - P4 improvisation: inside the sandboxed session, attempts to edit an allowlisted script, edit a shot settings file after a card exists, target another film, run a paid provider call from Python, and start a second claim on an approved request all fail as specified.
   - P5 broker death: kill -9 the broker mid-session; the server reconciles the orphaned Claude group before allowing resume.
2. Run executor + allowlist + state machine, with tests (double decisions, stale epoch, cancellation, uncertain outcome, never runs twice).
3. Broker (PTY + live endpoint + journal), with tests (server restart re-attach from seq, single client, bounded shutdown, replacement refused until reaped).
4. **Early real run** (free work only): check-in, reading the film, a `look_run --dry-run` via `frontlot_run`.
5. Page: conversation, composer, Stop, Show terminal, states, controller lease, History drawer.
6. Spend card end to end, then one real paid run Ben approves with Go (one item).
7. Existing suite stays green (263 passing; one pre-existing failure from a missing Playwright browser).

## 7. Open questions

- Q1 (was: hook waits for Ben): resolved by `frontlot_run` returning immediately; no blocking hook.
- Q2 `claude --bg`/`attach` vs custom broker: custom broker, because the broker must also own the live endpoint and journal for restart survival (Codex #6).
- Q3 exact allowlist and free/paid tags: built and reviewed in build step 2, from the repo's run scripts and every caller of paid providers.
- Q4 whether the Claude Code sandbox on 2.1.293 supports every deny listed in §4.1 (read denies, unix-socket denies, network allowlist, no-escape setting): answered by probe P1 before anything else.
