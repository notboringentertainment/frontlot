# Front Lot: a Claude session in the log column

Date: 2026-10-07
Status: design approved by Ben in conversation, section by section; awaiting Codex review
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
3. **Spend cards**: inline, when Claude asks to run something paid: what, for whom, cost estimate (or "cost unknown"), yellow **Go**, quiet **Not now**.
4. **Composer** at the bottom, with **Stop** while Claude is working.
5. **Show terminal** switch: flips the column to the raw Claude terminal. Flips automatically when Claude needs something the column can't draw (permission prompts other than spend, its own question dialogs, slash commands, version too old, add-on silent), and back when resolved.
6. **History** drawer: the current "The log" (decided approvals) and "Production decisions" move here.
7. **Signing bay** stays docked at the foot of the column, unchanged.

Session states shown in plain words: Starting, Ready, Working, Waiting for you (spend card / terminal), Ended (with **Pick back up**), Unavailable (with the one fix: sign in to Claude Code, update Claude Code).

## 3. Architecture

### 3.1 Session keeper (one per film)

A detached broker process per film, modelled on the existing signing broker (`scripts/gate_sign.py --broker`, `backlot/tty.py`):

- New script `scripts/claude_session.py --broker --project <slug>`; spawned by the Front Lot server with `start_new_session=True`, stdio to DEVNULL.
- Owns a PTY running `claude` with cwd = repo root, login-shell PATH, `TERM=xterm-256color`.
- Unix socket + lock + sidecar under `~/.openmontage/backlot/claude/<slug>.{sock,lock,session.json}`, mode 0600. Reuses the existing framing (`HELLO/IN/OUT/RESIZE/STATUS/BYE`), the single-client rule, and the 64 KB replay buffer.
- Differences from the signing broker: does **not** take the run lease (run scripts take it themselves; holding it would deadlock them); no gate-request precondition; output relayed with full xterm passthrough (not `AnsiSanitizer`, which strips cursor/OSC and breaks Claude's TUI).
- Lifetime: survives page reloads, window close, and Front Lot server restarts. Ends only on **End session**, **New conversation**, or Claude exiting. On end it sends `STATUS/BYE`, unlinks socket and sidecar.
- Launch: `claude --plugin-dir <mod> --session-id <uuid> --settings <generated settings file> --append-system-prompt-file <brief> "<check-in prompt>"`; resume: `claude --plugin-dir <mod> --resume <uuid> --settings … --append-system-prompt-file …` with a short "picking back up" prompt.
- Session id per film stored in `~/.openmontage/backlot/claude/<slug>.json` (not in the film folder).
- Preflight before spawn: `claude --version` ≥ 2.1.288 (installed: 2.1.293), `claude auth status --json` signed in. Failures become the Unavailable state with the specific fix.

### 3.2 Add-on (Story-drive's live mod, reused)

- Vendor a copy of `~/Projects/story-drive/mod/story-drive-live/` into `backlot/claude_mod/` (renamed env vars `FRONTLOT_LIVE_SOCKET` / `FRONTLOT_LIVE_TOKEN`). Story-drive's copy is not modified.
- Protocol unchanged: HTTP over a per-session unix socket (0600), token header, routes `/hello`, `/report` (seq-contiguous batches, `{acceptedThrough}`), `/ping` (5 s), `/inbox` (25 s long-poll), `/inbox-ack`.
- Events used: `row`, `delta`, `turn`, `tool`, `waiting-for-input`, `input-done`, `session-end`, `queue-overflow`. `mark` is unused at first.
- Composer submit via `$.prompt.submit({text, asUser:true})` (acked `queued` then `submitted`); Stop via `$.turn.abort({turnId})`. Slash commands are refused and routed to the terminal view.
- **New in Front Lot's copy: spend decisions.** The `classic.PermissionRequest` handler, when the tool call matches a paid command (§4.1), reports a `spend-request` event (`requestId`, command summary, entity, cost estimate if known) and awaits a `spend-decision` from the inbox, then returns allow or deny for that one call. Any other permission request keeps Story-drive behaviour (report `waiting-for-input`, defer to the TUI → terminal view).

### 3.3 Front Lot server

New module `backlot/claude_live.py`:

- Hosts the add-on's endpoint (asyncio unix-socket HTTP server per session, token-checked, POST-only) on behalf of each film's session; socket path + token passed to the broker as env at spawn.
- Keeps an in-memory and on-disk transcript cache per film (`~/.openmontage/backlot/claude/<slug>.events.jsonl`, bounded) so a reload redraws instantly; on resume, history replays via the add-on's `$.session.messages()` path.
- Exposes to the page:
  - `GET /api/project/{p}/claude` → state (none / starting / ready / working / waiting / ended / unavailable + reason) and recent events.
  - `WS /api/project/{p}/claude/live` → event stream to the page; page → server messages `submit`, `stop`, `spend-decision`, `start`, `resume`, `new`, `end`. Same origin check and capability token handshake as the signing WebSocket.
  - `WS /api/project/{p}/claude/tty` → raw terminal relay to the broker (xterm passthrough), used by Show terminal.
- Writes every spend decision to `~/.openmontage/backlot/claude/<slug>.spend.jsonl`: time, command summary, entity, cost estimate shown, decision.

### 3.4 Page

- `backlot/ui/session.js` (new module): connects the live WebSocket, reduces events into a view model (port of Story-drive's pure reducer `src/live/model.ts` to plain JS), renders the conversation, spend cards, composer, Stop, Show terminal, states.
- Raw terminal reuses the existing xterm instance pattern (one page-lifetime Terminal per surface; render/SSE never replace it).
- Picture links: when a `tool` event finishes a run script for an entity, the page selects that entity in the bin so new frames appear in the viewer.
- Styling follows `backlot/DESIGN.md` (Cutting Room): yellow only on spend cards' Go and Needs you; tape colours for states; no monospace outside the terminal and expanded tool detail.

## 4. Money and signing

### 4.1 Generated settings (per session)

Front Lot writes a settings file passed with `--settings`:

- `permissions.ask` for paid commands, by Bash prefix: `scripts/headshot_run.py`, `scripts/sheet_run.py`, `scripts.supervised_shot … generate` and any other generator entry point that spends (list confirmed during build from `lib/shot_allowance.py` callers and run scripts); both `python …` and `.venv/bin/python …` and `-m` forms.
- `permissions.deny` for signing: `scripts/gate_sign.py`, `scripts/gate_approve.py`, `-m scripts.gate_*`, and direct writes under `.gate-requests/`.
- `permissions.allow` for free work so Claude is not interrupted: reading the repo and projects, `look_run.py` (no spend), `--dry-run` forms, status/inspect commands, `python -m backlot` commands.
- Everything else follows Claude Code's default permission behaviour (prompts appear in the terminal view).

Cost estimate on the card: taken from the run script's own estimate where it prints one (dry-run or allowance record); otherwise "cost unknown". No invented numbers.

### 4.2 Trust boundary (test before building)

Two layers keep signing with Ben:

1. Deny rules above.
2. `gate_approve.py` refuses non-TTY stdin (`require_tty`). **Build gate:** prove, with the real broker + real `claude` + the generated settings, that a Bash command run by the embedded Claude sees `stdin` as not-a-TTY and that `gate_approve.py` refuses. If Claude's Bash children do see a TTY, fix that (e.g. force stdin to `/dev/null` for tool commands, or an additional check in `gate_approve.py` that its controlling terminal is the signing broker's) before any other work proceeds.

### 4.3 Brief given to Claude

`--append-system-prompt-file` with: what Front Lot is; which film; the check-in task (WriterOS promotes, pending approvals, what is ready to make, in a few lines, then wait); that paid runs will be shown to Ben as spend cards and a "Not now" is a decision to respect; that signing is Ben's and Claude must never attempt it; plain-language rules (no hashes, IDs, machine lines in replies). No story content is placed in the brief.

## 5. Failure handling

| Situation | What Ben sees | Mechanism |
|---|---|---|
| Claude exits / crashes | "The session ended" + Pick back up | broker STATUS exited; resume with stored uuid |
| Add-on silent > 15 s, or never says hello within 8 s | Terminal view + note | Story-drive timeouts; `view = raw` |
| Claude Code too old / signed out | Unavailable + the one fix | preflight |
| Run lease held by a production run | Claude still talks; run scripts refuse to overlap and Claude says so | existing `hold_lease` |
| Stop pressed | Current turn aborted | `$.turn.abort`; paid work already started behaves exactly as today |
| Front Lot server restarts | Page reconnects; session still running | detached broker + sidecar discovery |
| Second browser tab | Second tab gets read-only view of the conversation; composer disabled | single-client rule on the live socket |

## 6. Build order and tests

1. **Trust-boundary test (§4.2).** Nothing else proceeds until it passes.
2. **Session keeper** with tests: spawn, attach, replay, single client, survive server restart, clean end.
3. **Add-on endpoint** in `claude_live.py` with tests: hello/report/inbox/ack, token and POST-only refusal, seq gaps, timeouts.
4. **Early real run** (Ben's "run it before reviewing it"): real Claude in the column on a film, free work only: check-in, reading the film, a `look_run --dry-run`. Fix what it shows before continuing.
5. **Page**: conversation, composer, Stop, Show terminal, states, History drawer.
6. **Spend card**: add-on `spend-request` / `spend-decision`, settings rules, spend log; then one real paid run Ben approves with Go (one item, per the one-at-a-time rule).
7. Existing suite stays green (263 passing; the one pre-existing failure is a missing Playwright browser).

## 7. Open questions for review

- Q1. Can a function-hook `classic.PermissionRequest` handler await an external answer and return allow/deny for that call in Claude Code 2.1.293? If not, spend cards fall back to: deny-by-rule + Claude asks in chat + Go sends an inbox message that adds a one-shot allow (needs design).
- Q2. Is `claude --bg` / `claude attach` a better session keeper than a custom broker? Current choice: custom broker, because it matches the proven signing pattern and gives the replay buffer and single-client semantics Front Lot already relies on.
- Q3. Exact list of paid entry points for §4.1.
