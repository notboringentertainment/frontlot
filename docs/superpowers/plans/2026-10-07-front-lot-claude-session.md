# Front Lot Claude Session Implementation Plan (rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a real Claude Code session inside Front Lot's log column, sandboxed so it cannot sign or spend, with Front Lot executing every pipeline step and paid steps waiting for Ben's Go on a spend card.

**Architecture:** A detached per-film broker (`scripts/claude_session.py`) owns the `claude` PTY, the add-on's live endpoint, the request store, and an event journal, so everything survives Front Lot restarts. The vendored Story-drive add-on (`backlot/claude_mod/`) reports conversation events and exposes one tool, `frontlot_run`, which asks the broker to run an allowlisted operation. A run executor (`backlot/claude_ops.py` + `backlot/claude_requests.py` + `scripts/frontlot_run.py`) builds the exact argv itself, freezes inputs, and launches detached jobs exactly once. Front Lot's server (`backlot/claude_live.py`) relays broker events to the page; the broker enforces a single controller; the page (`backlot/ui/session.js`) draws the conversation in the Cutting Room design.

**Tech Stack:** Python 3.10 (FastAPI/Starlette, asyncio, `pty`, `fcntl`), Claude Code CLI ≥ 2.1.288 (installed 2.1.294) with a mod plugin (TypeScript, Claude Code mods API, `claude plugin test` / `claude-code/testing`), vanilla JS + xterm 5.5 in the browser, pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-07-front-lot-claude-session-design.md` (rev 4, Codex-approved; review log beside it). Pre-flight scan: `.superpowers/sdd/2026-10-07-front-lot-claude-session/preflight.md` (P1–P43, resolved below).

## Global Constraints

- Claude Code minimum version for live mode: `2.1.288` (Story-drive `MIN_LIVE_VERSION`); installed `2.1.294`.
- The embedded `claude` must never receive `CLAUDECODE` or any `CLAUDE_CODE_*` env var, nor any provider API key; env is built from an allowlist.
- Sandbox: `sandbox.enabled: true`, `sandbox.failIfUnavailable: true`, `sandbox.allowUnsandboxedCommands: false`, `sandbox.excludedCommands: []`, `network.strictAllowlist: true`, `network.allowUnixSockets: []`.
- Claude's working directory is the film's work area `<film>/frontlot-work/` (deviation from spec §3.1 "cwd: repo root", for a reason: Claude Code's sandbox allows writes to the working directory by default, so using the work area as cwd makes "write only to the work area" the default instead of a deny-then-allow carve-out).
- Films may live outside the repo (`OPENMONTAGE_PROJECTS_DIR`, e.g. the worktree run in Task 10 uses the main checkout's `projects/`). Every film-record rule is built from the film's real folder and its parent (the projects folder), never from `<repo>/projects`.
- Launch with `--setting-sources project`. Because cwd is the work area, which contains no `.claude/`, this loads neither Ben's user settings (hooks, plugins) nor the repo's project settings; only `--settings` and the `--plugin-dir` add-on apply. Probe P1/P3 verify this and that sign-in still works.
- Fail closed: the broker refuses to start a session unless the sandbox self-check (defined by probe P1, shared code in Task 1) passes for this Claude version and settings.
- Every pipeline step goes through `frontlot_run`; operations are `{op, params}` (structured), never free-form argv (refinement of spec §3.2 `{script,args}` so argv adapters own every argument).
- Paid operations require a recorded Go from the controller; nothing paid ever retries automatically; a run whose fate is unknown is shown as "not sure it ran", never as success.
- Name rule: user-facing text says "Front Lot"; "backlot" stays an internal code name.
- No story names (characters, titles) in code, tests, fixtures, or this plan's commands; use `film`, `hero-a`, `place-a`, and `$FILM` for the real film Ben picks.
- Session metadata lives under `metadata_root()/claude/` (`~/.openmontage/backlot/claude/` or `$OPENMONTAGE_GATES_DIR/claude/` in tests), never inside the film folder (except the work area). Spend log: `metadata_root()/claude/<slug>.spend.jsonl` (spec §4.3).
- Unix socket paths on macOS are capped near 104 bytes: every test that starts a broker uses `tempfile.mkdtemp(prefix=..., dir="/tmp")` for `OPENMONTAGE_GATES_DIR` and removes it afterwards.
- Plain-language rule for anything shown to Ben: no hashes, ids, or machine lines in the main view.
- The broker↔server and server↔page protocols are defined once, in Task 8 Interfaces; Tasks 9, 11 and 12 consume only what is listed there.

## Review Focus

1. A spend card answered after the conversation was restarted, stopped, or replaced must not run (stale Go) — owned by Tasks 5 and 8.
2. Two browser tabs clicking Go at the same moment run the job once; the second click gets a plain "already answered" — owned by Tasks 5, 8 and 12.
3. Front Lot restarted while a paid job is running: the job continues, and the card shows its real outcome afterwards (not "waiting" and not "done" by guess) — owned by Tasks 6, 8 and 11.
4. Claude writes or edits a file the paid run depends on between card and Go: the run refuses before spending — owned by Tasks 3 and 4.
5. Claude Code missing, signed out, too old, or its sandbox unconfirmed: the column says so with the one fix instead of a blank panel — owned by Tasks 8, 9 and 11.

## Changes in rev 2

Each pre-flight problem, how this revision resolves it (code facts checked against worktree HEAD 756c9b7, `~/Projects/story-drive/mod/story-drive-live/`, and the mod API types in its `.claude-plugin/types/`).

- **P1** Resolved. Record denies are built from `film_root.resolve().parent` (`<projects>/*/…`, covering this film and sibling films wherever the projects folder lives) and now include `production/`, `assets/`, `renders/`, `.staging/`, `.import-staging/`, top-level `*.json/*.jsonl/*.yaml/*.yml/*.lock`; unused `home` parameter replaced by `meta_root` (also denied for reading); new test with the film outside the repo (Task 1).
- **P2** Resolved differently: Task 2's P2 probe edits a throwaway copy of the mod in a temp dir; the committed `backlot/claude_mod` carries only the rename, verified by a grep before commit; Task 7 adds the one real registration.
- **P3** Resolved. P2 now runs against a probe-only live endpoint (`scripts/claude_probe_live.py`) that answers `/run` and then queues an inbox submit; pass requires the outcome message in the conversation and an `/inbox-ack` of `submitted`. (The rev-1 probe also could not work: the add-on registers tools only when `FRONTLOT_LIVE_*` are set, `register.ts` `session.start`.)
- **P4** Resolved. headshot_run (`STAGE = "headshots"`) now freezes `checkpoint_headshots.json` (palette and run state) and the active look hash (`--expect-look-hash`); sheet_run (`STAGE = "visual_bible"`) freezes `checkpoint_visual_bible.json` and the look hash. A missing file is frozen as `absent` (Tasks 3, 4).
- **P5** Resolved. Both scripts verify inside `_checkpoint()` on its first read after the lease (`hold_lease` in headshot_run, `run_lease.acquire` in sheet_run) through a per-run `Expectations` object; later reads see only the script's own writes under the lease. Anchors corrected.
- **P6** Resolved. Digests are computed into variables before any f-string.
- **P7** Resolved. `InputChanged(RunError)`, so both scripts' `except RunError` print the plain message and exit 1.
- **P8** Resolved. Behavioural tests added to `tests/lib/test_headshot_run.py` and `tests/lib/test_sheet_run.py` (changed input → `InputChanged`, fake generator never called), plus a brief-revision test in `tests/tools/test_supervised_production.py`.
- **P9** Resolved. Candidates capped by importing `MAX_CANDIDATES` (4) from `scripts.headshot_run`.
- **P10** Resolved. `headshot_import` requires `origin_tool` and passes `--origin-tool`.
- **P11** Resolved. `headshot_finish` is paid (`_finish_import` → `_judge_existing` → `SheetJudge`); `sheet_finish`/`sheet_abandon` re-checked and stay free (their `_resume`/`_abandon` paths call no judge or generator).
- **P12** Resolved. Shot ids validated by `lib.supervised_production.shot_dir` (the script's own rule), not `ENTITY_ID_RE`.
- **P13** Resolved. Duplicate test replaced; Task 6 adds parametrized tests cancelling (Stop, End, add-on reload) at each step between claim and spawn, plus cancel-before-claim; counts corrected (Task 5: 15 tests).
- **P14** Resolved. `launch()` claims, spawns, and records pid + `running` inside one request lock; `mark_running` refuses unless state is `launching`.
- **P15** Resolved. Launch records `lib.run_lease.process_start_time(pid)`; reconcile treats a start-time mismatch as gone and reaps its own wrapper children first.
- **P16** Resolved. `RequestStore.expire_stale()` called by the broker watcher; journals `spend-decided {state: "expired"}` and tells Claude.
- **P17** Resolved differently: P4 item 4 is the Task 4 fixture tests (a CLI run on a scratch film fails config checks before the lease, so it could never reach the check); pass only if they assert the "changed after it was approved" message and zero generator calls.
- **P18** Resolved. Key = `String(e.tool_use_id)` (`register.ts:293` pattern), via a pure `runCall()` with a unit test.
- **P19** Resolved. Pure logic in `hooks/run.ts`; tests use `claude-code/testing` and run with `claude plugin test backlot/claude_mod` (Story-drive's mod tooling); plus a register-level test reusing the copied `wire()` helper.
- **P20** Resolved. One unmatched `on('tool.describe', …)` branching on `e.tool` (the `ToolDescribeInput.tool` field); `claude plugin validate --strict`.
- **P21** Resolved. New reply statuses `not-received` (unknown key: "submit it once more") and `unknown-outcome` (record `uncertain`: "tell Ben, do not retry"); `approved`/`launching` map to `running`; the add-on's own `uncertain` only means "no ack in 5 s".
- **P22** Resolved. New `backlot/claude_frames.py` (same 5-byte header, own types incl. `EVENT=7`/`ACTION=8`, 256 KB JSON limit) with tests; `backlot/tty.py` and the signing socket's limits are untouched (test asserts it).
- **P23** Resolved. Resume reads `<slug>.session` only.
- **P24** Resolved. Broker writes `<slug>.unavailable.json {reason, version?}` (`missing`, `old-version`, `signed-out`, `sandbox`) before exiting, clears it on a good start; tested.
- **P25** Resolved. Separate `<slug>.spawn.lock` serializes spawners; the broker's lifetime lock is only probed with `LOCK_NB` and never held while waiting; GET state checks the identity file's verified broker pid instead of touching locks.
- **P26** Resolved. `/run` looks up the key first and returns the existing request without preparing or snapshotting again; tested.
- **P27** Resolved. Every `/hello` after the first cancels unstarted requests (`addon-reload`), journals the cancellations, and rebinds new requests to the new add-on epoch; tested.
- **P28** Resolved. Fail-closed sandbox self-check: one `claude -p` turn with the generated settings must read an allowed canary and must not read a canary under the denied metadata root; cached per Claude version + settings hash; failure → unavailable `sandbox`. Shared helpers and tests in Task 1, run in Task 8 (see the open question in the hand-off).
- **P29** Resolved. Unique `/tmp` gates dir per test with `rmtree`; tests stop only the brokers they started; broker handles SIGTERM via `shutdown("terminated")`; tested.
- **P30** Resolved. Task 8 Interfaces define every event's fields; the snapshot is built on demand from the store and a 50-row ring (`{state, hello, rows, cards, cursor}`); the page reducer rebuilds from it; add-on events carry their `epoch` and the add-on hello is relayed as `addon-hello` so the ported Story-drive reducer (which needs `hello` and per-epoch `events`) works.
- **P31** Resolved. `spend-decided`, `run-started` and `run-finished` all set `cards[requestId].state`; `run-finished` also adds a feed line.
- **P32** Resolved. `attach` added to page actions; notices travel as `{type:"event", seq:null, event:{kind:"notice"}}`; the broker answers every mutating action from a non-controller with the Take-control notice; test updated.
- **P33** Resolved. Task 9 fixture copies `test_tty.py`'s three patches (`state_mod.PROJECTS_DIR`, `server_mod.PROJECTS_DIR`, `server_mod._PROJECTS_ROOT_STR`) plus `_summary_cache` and `_watch_projects`.
- **P34** Resolved. Broker lease: a controller with no live socket keeps the lease 30 s (`FRONTLOT_LEASE_SECONDS` in tests), its own page id regains it within that window, anyone after; tested.
- **P35** Resolved. `new` = SIGTERM the verified broker, wait (bounded) until its lifetime lock is free and its identity file is gone, reconcile, then spawn fresh; tested.
- **P36** Resolved. History drawer class `log-history`.
- **P37** Resolved. `.log-feed` becomes `flex: none` with a max height; "Production decisions" and Machine room (`renderActivity`) move into History with "The log"; `renderLog` returns `{needs, history}`.
- **P38** Resolved. Files list includes `session_model.js` and its test; the Playwright test asserts the raw view in `#session-terminal` plus the note.
- **P39** Resolved. Stub operations are injected into the broker subprocess with a test-only `sitecustomize` on `PYTHONPATH` (the `tty_helpers` pattern); the fake `claude` writes the live socket/token it was given to `frontlot-work/live.json`; shared in `tests/backlot/claude_fakes.py` (Task 8), reused by Tasks 9 and 12.
- **P40** Resolved. Task 1 Step 0 records the full-suite baseline (`pytest -q`, 2219 tests collected on 2026-10-08; `tests/backlot`: 263 pass + 1 known Playwright failure); Task 12 runs the full suite, the mod tests and the node tests and compares.
- **P41** Resolved. Spend log at the spec path `claude/<slug>.spend.jsonl`.
- **P42** Resolved. Task 5 no longer lists `metadata_root`; Task 6 CLI lists `--repo`.
- **P43** Resolved. No registry discovery in `/run`: headshot/sheet estimates use the scripts' own price constants (`GENERATION_PRICE_USD`) plus the judge reserve (`DEFAULT_RESERVE_USD`); shot estimates use tool objects warmed once in a background thread at broker start (cold cache → "cost unknown").

Also fixed while verifying (not in the scan): `supervised_shot` notes must be non-empty (`_note()` rejects blanks), so the adapters require them; `shot_generate` now freezes the shot brief revision (`--expect-brief-revision`, checked inside `production.request` on the brief it uses), as spec §4.2 requires for fixed project inputs; `sheet` accepts the policy's `wardrobe` role; `resolve_claude` uses only `FRONTLOT_CLAUDE` when it is set (tests could otherwise pick up the real CLI); the add-on silence/no-hello timeouts (spec §5) are journaled by the broker so the page can fall back to the terminal; Task 10 uses `BACKLOT_PORT` with `backlot open` (which has no `--port`).

---

## File Structure

| File | Responsibility |
|---|---|
| `backlot/claude_settings.py` | Pure: per-session settings JSON, brief text, env allowlist, launch argv, sandbox self-check prompt/argv/verdict. |
| `scripts/claude_probe.py` | Probes P1–P3 against the real CLI; writes a pass/fail report. Not product code. |
| `scripts/claude_probe_live.py` | Probe P2 only: a stand-in live endpoint. Not product code. |
| `backlot/claude_ops.py` | Operation allowlist, per-operation argv adapters, path-root checks, frozen inputs, cost estimates. |
| `lib/run_common.py` (modify) | `InputChanged`, `parse_expectations()`, `Expectations` used by paid scripts. |
| `scripts/headshot_run.py`, `scripts/sheet_run.py` (modify) | Verify frozen checkpoint bytes and look hash after the lease, before any paid call. |
| `lib/supervised_production.py`, `scripts/supervised_shot.py` (modify) | `generate` refuses when the shot brief revision changed. |
| `backlot/claude_requests.py` | Request store + state machine (flock, O_EXCL claim, one-lock launch, spend log, expiry, reconciliation). |
| `scripts/frontlot_run.py` | Detached wrapper: runs one claimed request's argv, writes the outcome file. |
| `backlot/claude_mod/` | Vendored Story-drive live mod, renamed, plus the `frontlot_run` tool (`hooks/run.ts`). |
| `backlot/claude_frames.py` | Broker↔server framing (own types and limits; signing framing untouched). |
| `backlot/claude_journal.py` | Bounded durable event journal with snapshot on gap. |
| `scripts/claude_session.py` | Per-film broker: preflight + sandbox self-check, PTY, live endpoint, journal, requests, controller lease, identity, shutdown. |
| `backlot/claude_live.py` | Server side: spawn/attach/reconcile brokers, relay, page WebSockets, GET state. |
| `backlot/server.py` (modify) | Install claude routes; CSP unchanged (same origins). |
| `backlot/ui/session_model.js` | Pure reducer: port of Story-drive's `src/live/model.ts` plus Front Lot event kinds. |
| `backlot/ui/session.js` | Page: conversation, spend cards, composer, Stop, terminal toggle, states, read-only. |
| `backlot/ui/board.html`, `board.js`, `board.css` (modify) | Log column layout: Needs you / conversation / composer / History drawer / signing bay. |
| `tests/backlot/claude_fakes.py` | Test helpers: fake `claude`, stub operations, live-endpoint client, broker cleanup. |
| `tests/backlot/test_claude_*.py`, `tests/backlot/test_frontlot_run.py`, `tests/backlot/session_model.test.mjs`, `tests/backlot/test_ui_session.py` | Unit and integration tests per module. |
| `tests/test_run_common_expect.py`, `tests/lib/test_headshot_run.py`, `tests/lib/test_sheet_run.py`, `tests/tools/test_supervised_production.py` | Frozen-input tests (new file + appended tests). |

---

### Task 1: Session settings, brief, env, launch argv, sandbox self-check helpers

**Files:**
- Create: `backlot/claude_settings.py`
- Test: `tests/backlot/test_claude_settings.py`

**Interfaces:**
- Produces:
  - `work_dir(film_root: Path) -> Path` (creates `film_root/"frontlot-work"`, mode 0700)
  - `build_settings(*, repo_root: Path, film_root: Path, meta_root: Path) -> dict`
  - `build_brief(*, film_title: str, film_slug: str) -> str`
  - `allowed_env(base: Mapping[str, str], *, login_path: str, live_socket: str, live_token: str) -> dict[str, str]`
  - `launch_argv(*, claude: str, mod_dir: Path, settings_file: Path, brief_file: Path, session_id: str, resume: bool, prompt: str) -> list[str]`
  - `selfcheck_prompt(deny_file: Path, allow_file: Path) -> str`; `selfcheck_argv(*, claude: str, settings_file: Path, prompt: str) -> list[str]`; `selfcheck_passed(output: str, *, deny_secret: str, allow_secret: str) -> bool`
  - constants `MIN_LIVE_VERSION = (2, 1, 288)`, `PLUGIN_NAME = "frontlot-live"`, `TOOL_NAME = "mcp__frontlot-live__frontlot_run"`

- [ ] **Step 0: Record the baseline**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -5` and `.venv/bin/python -m pytest tests/backlot -q 2>&1 | tail -3`.
Write the pass/fail counts and the ids of any failing tests into the task report. Expected on 2026-10-08: 2219 tests collected overall; `tests/backlot` 263 passed, 1 failed (missing Playwright browser). Task 12 compares against these numbers.

- [ ] **Step 1: Write the failing tests**

```python
# tests/backlot/test_claude_settings.py
from pathlib import Path

from backlot import claude_settings as cs


def test_settings_enforce_sandbox_and_deny_signing(tmp_path):
    repo = tmp_path / "repo"; film = repo / "projects" / "film"; meta = tmp_path / "meta"
    film.mkdir(parents=True)
    s = cs.build_settings(repo_root=repo, film_root=film, meta_root=meta)
    sb = s["sandbox"]
    assert sb["enabled"] is True and sb["failIfUnavailable"] is True
    assert sb["allowUnsandboxedCommands"] is False and sb["excludedCommands"] == []
    assert "~/.openmontage" in sb["filesystem"]["denyRead"]
    assert str(meta.resolve()) in sb["filesystem"]["denyRead"]
    assert str(repo.resolve() / ".env") in sb["filesystem"]["denyRead"]
    assert sb["network"]["strictAllowlist"] is True
    assert sb["network"]["allowUnixSockets"] == []
    assert sb["network"]["allowedDomains"] == ["127.0.0.1:5177"]
    deny = s["permissions"]["deny"]
    r = repo.resolve()
    assert "Read(~/.openmontage/**)" in deny
    assert f"Read(/{meta.resolve()}/**)" in deny
    assert f"Edit(/{r}/scripts/**)" in deny and f"Edit(/{r}/.git/**)" in deny
    assert f"Edit(/{r}/projects/*/.gate-requests/**)" in deny
    assert not any("frontlot-work" in d for d in deny)
    assert s["permissions"]["allow"] == [cs.TOOL_NAME]


def test_record_denies_follow_the_film_root_even_outside_the_repo(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    projects = tmp_path / "elsewhere" / "projects"; film = projects / "film"; film.mkdir(parents=True)
    deny = cs.build_settings(repo_root=repo, film_root=film, meta_root=tmp_path / "meta")["permissions"]["deny"]
    p = film.resolve().parent
    for d in (".gate-requests", "canon", "artifacts", "assets", "renders", "production", "history", ".staging", ".import-staging"):
        assert f"Edit(/{p}/*/{d}/**)" in deny, d
    for g in ("*.json", "*.jsonl", "*.yaml", "*.yml", "*.lock"):
        assert f"Edit(/{p}/*/{g})" in deny, g
    # nothing may cover the work area: no whole-film or whole-projects rule
    assert f"Edit(/{p}/**)" not in deny and f"Edit(/{p}/*/**)" not in deny
    assert not any("frontlot-work" in d for d in deny)


def test_env_is_allowlisted(tmp_path):
    base = {"HOME": "/h", "USER": "u", "LANG": "en_US.UTF-8", "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION_ID": "x", "OPENAI_API_KEY": "k", "FAL_KEY": "k"}
    env = cs.allowed_env(base, login_path="/usr/bin", live_socket="/s", live_token="t")
    assert env == {"HOME": "/h", "USER": "u", "LANG": "en_US.UTF-8", "PATH": "/usr/bin",
                   "TERM": "xterm-256color", "FRONTLOT_LIVE_SOCKET": "/s", "FRONTLOT_LIVE_TOKEN": "t"}


def test_launch_argv_new_and_resume(tmp_path):
    common = dict(claude="/c", mod_dir=Path("/m"), settings_file=Path("/s.json"),
                  brief_file=Path("/b.md"), session_id="u-1", prompt="hi")
    new = cs.launch_argv(resume=False, **common)
    assert new[:1] == ["/c"] and "--session-id" in new and new[new.index("--session-id") + 1] == "u-1"
    assert "--strict-mcp-config" in new and new[-1] == "hi"
    assert new[new.index("--setting-sources") + 1] == "project"
    res = cs.launch_argv(resume=True, **common)
    assert "--resume" in res and "--session-id" not in res


def test_brief_has_no_story_text_and_names_rules():
    b = cs.build_brief(film_title="Film", film_slug="film")
    assert "frontlot_run" in b and "never" in b.lower() and "sign" in b.lower()


def test_selfcheck_needs_positive_and_negative_evidence(tmp_path):
    assert cs.selfcheck_passed("ran: ALLOW-1", deny_secret="DENY-1", allow_secret="ALLOW-1")
    assert not cs.selfcheck_passed("I won't run that.", deny_secret="DENY-1", allow_secret="ALLOW-1")  # a refusal proves nothing
    assert not cs.selfcheck_passed("ALLOW-1 DENY-1", deny_secret="DENY-1", allow_secret="ALLOW-1")
    argv = cs.selfcheck_argv(claude="/c", settings_file=Path("/s.json"), prompt="p")
    assert argv[:2] == ["/c", "-p"] and argv[argv.index("--permission-prompts") + 1] == "none"
    assert "cat" in cs.selfcheck_prompt(tmp_path / "d", tmp_path / "a")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_settings.py -v`
Expected: FAIL with `ImportError: cannot import name 'claude_settings'`

- [ ] **Step 3: Implement**

```python
# backlot/claude_settings.py
"""Settings, brief, environment, and argv for Front Lot's embedded Claude.

Everything here is pure. The sandbox and permission rules are the session's
enforced boundary (spec §4.1): Claude may read the film and the repo, write
only its work area, reach only WriterOS on localhost, and use exactly one MCP
tool. Each rule names what it protects.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

MIN_LIVE_VERSION = (2, 1, 288)
PLUGIN_NAME = "frontlot-live"
TOOL_NAME = f"mcp__{PLUGIN_NAME}__frontlot_run"
WRITEROS_HOST = "127.0.0.1:5177"
_ENV_KEEP = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "SHELL", "TMPDIR")
EMPTY_MCP = '{"mcpServers":{}}'

# Code Front Lot executes with Ben's privileges (protects: the runtime, git hooks, test config).
PROTECTED_TREES = ("scripts", "lib", "tools", "backlot", "pipeline_defs", "skills", ".venv", "schemas",
                   "styles", ".claude", ".git", ".githooks")
REPO_TOP_FILES = ("*.py", "*.toml", "*.cfg", "*.ini", "*.txt", "*.json", "*.yaml", "*.yml", ".env*", "Makefile")
# Film records (protects: what approvals, receipts, ledgers and runs rely on). Applied to every film in the
# projects folder, by name, so the film's own frontlot-work/ stays writable. Never add a whole-film rule:
# a deny on a parent folder would also deny the work area (deny always wins over allow).
RECORD_DIRS = (".gate-requests", "canon", "artifacts", "assets", "renders", "production", "history",
               ".staging", ".import-staging")
RECORD_FILES = ("*.json", "*.jsonl", "*.yaml", "*.yml", "*.lock")


def work_dir(film_root: Path) -> Path:
    d = film_root / "frontlot-work"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def record_deny_rules(film_root: Path) -> list[str]:
    projects = film_root.resolve().parent
    return ([f"Edit(/{projects}/*/{d}/**)" for d in RECORD_DIRS]
            + [f"Edit(/{projects}/*/{g})" for g in RECORD_FILES])


def build_settings(*, repo_root: Path, film_root: Path, meta_root: Path) -> dict:
    repo = repo_root.resolve()
    meta = meta_root.resolve()
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,           # protects: never run unsandboxed
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,   # protects: no dangerouslyDisableSandbox escape
            "excludedCommands": [],
            "filesystem": {
                # protects: signing key, ledgers, signer and broker sockets, provider keys
                "denyRead": ["~/.openmontage", str(meta), str(repo / ".env"), str(repo / ".env.local"),
                             "~/.config/gcloud", "~/.codex", "~/.aws"],
            },
            "network": {
                "allowedDomains": [WRITEROS_HOST],  # protects: no route to paid services
                "strictAllowlist": True,
                "allowUnixSockets": [],             # protects: signer and broker sockets
                "allowLocalBinding": False,
            },
        },
        "permissions": {
            "deny": (
                ["Read(~/.openmontage/**)", "Edit(~/.openmontage/**)", f"Read(/{meta}/**)", f"Edit(/{meta}/**)",
                 f"Read(/{repo}/.env*)", "Read(~/.config/gcloud/**)", "Read(~/.codex/**)", "Read(~/.aws/**)"]
                + [f"Edit(/{repo}/{tree}/**)" for tree in PROTECTED_TREES]
                + [f"Edit(/{repo}/{name})" for name in REPO_TOP_FILES]
                + record_deny_rules(film_root)
            ),
            "allow": [TOOL_NAME],
        },
        "enableAllProjectMcpServers": False,
    }


def build_brief(*, film_title: str, film_slug: str) -> str:
    return f"""You are working inside Front Lot, Ben's app for making a film's visuals.
Film: "{film_title}" (project id: {film_slug}). Your working folder is this film's Front Lot work area.

How you work here:
- You can read the film (../ is the film folder) and the OpenMontage repo, and think, plan, and talk with Ben.
- You never run pipeline scripts yourself. Every pipeline step goes through the frontlot_run tool with an operation name and parameters. Front Lot runs it.
- Free steps run right away. Paid steps show Ben a spend card; wait for his answer. "Not now" is a decision: do not ask again unless he brings it up.
- If frontlot_run says it didn't confirm receipt, check it with frontlot_run {{"check": "<key>"}}. Never resubmit. If Front Lot says it can't tell whether something ran, tell Ben plainly and do not retry.
- Signing approvals is Ben's alone. Never attempt it, never ask for a way around it. Point him to "Needs you".
- Speak plainly: no hashes, ids, file paths, or command lines unless Ben asks.

When the session opens or picks back up, give a short check-in: what is new (looks promoted in WriterOS, approvals waiting, runs that finished), what is ready to make next, in a few lines. Then wait for Ben.
"""


def allowed_env(base: Mapping[str, str], *, login_path: str, live_socket: str, live_token: str) -> dict[str, str]:
    env = {k: base[k] for k in _ENV_KEEP if k in base}
    env.update({"PATH": login_path, "TERM": "xterm-256color",
                "FRONTLOT_LIVE_SOCKET": live_socket, "FRONTLOT_LIVE_TOKEN": live_token})
    return env


def launch_argv(*, claude: str, mod_dir: Path, settings_file: Path, brief_file: Path,
                session_id: str, resume: bool, prompt: str) -> list[str]:
    argv = [claude, "--plugin-dir", str(mod_dir), "--settings", str(settings_file),
            "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", EMPTY_MCP,
            "--append-system-prompt-file", str(brief_file)]
    argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    argv.append(prompt)
    return argv


# --- fail-closed sandbox self-check (spec §4.1; probe P1 defines it) -------------------------
def selfcheck_prompt(deny_file: Path, allow_file: Path) -> str:
    return ("Use the Bash tool to run exactly these two commands, one at a time, and then print each "
            f"command's raw output and nothing else: `cat {deny_file}` and `cat {allow_file}`. Use no other tool.")


def selfcheck_argv(*, claude: str, settings_file: Path, prompt: str) -> list[str]:
    return [claude, "-p", "--output-format", "text", "--permission-prompts", "none",
            "--settings", str(settings_file), "--setting-sources", "project",
            "--strict-mcp-config", "--mcp-config", EMPTY_MCP, prompt]


def selfcheck_passed(output: str, *, deny_secret: str, allow_secret: str) -> bool:
    # Positive evidence (the allowed file was read) and negative evidence (the denied file was not).
    # A model that refuses to run anything shows neither secret and does not pass.
    return allow_secret in output and deny_secret not in output
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_settings.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backlot/claude_settings.py tests/backlot/test_claude_settings.py
git commit -m "feat(front-lot): settings, brief, env, argv, sandbox self-check for embedded Claude"
```

---

### Task 2: Probes P1–P3 (build gate)

**Files:**
- Create: `scripts/claude_probe.py`, `scripts/claude_probe_live.py` (probe-only, not product code)
- Create (copy only, renamed): `backlot/claude_mod/` from `~/Projects/story-drive/mod/story-drive-live/` (this task makes only the rename; Task 7 adds `frontlot_run`)
- Output: `docs/superpowers/specs/2026-10-07-front-lot-claude-session-PROBES.md`

**Interfaces:**
- Consumes: `claude_settings.build_settings`, `launch_argv`, `allowed_env`, `selfcheck_*`, `work_dir`; `backlot.tty.metadata_root`; `lib.paths.PROJECTS_DIR`
- Produces: a written PASS/FAIL report. **If any P1 item fails, STOP and report to Ben; do not start Task 3.**

- [ ] **Step 1: Copy the mod and rename**

```bash
mkdir -p backlot/claude_mod
cp -R ~/Projects/story-drive/mod/story-drive-live/. backlot/claude_mod/
cd backlot/claude_mod
grep -rl "STORY_DRIVE_LIVE_SOCKET\|STORY_DRIVE_LIVE_TOKEN\|story-drive-live\|x-story-drive-token\|http://story-drive" . \
  | xargs sed -i '' -e 's/STORY_DRIVE_LIVE_SOCKET/FRONTLOT_LIVE_SOCKET/g' \
      -e 's/STORY_DRIVE_LIVE_TOKEN/FRONTLOT_LIVE_TOKEN/g' \
      -e 's/story-drive-live/frontlot-live/g' \
      -e 's/x-story-drive-token/x-frontlot-token/g' \
      -e 's#http://story-drive#http://frontlot#g'
claude plugin validate --strict .
claude plugin test .
cd ../..
```
Expected: validate reports the plugin valid; the copied suites (`queue`, `register`, `waits`) pass under their new names. The copy includes the hidden `.claude-plugin/` (plugin.json + types); confirm with `ls -a backlot/claude_mod`.

- [ ] **Step 2: Write the probe scripts**

```python
# scripts/claude_probe.py
"""Probes P1-P3 for Front Lot's embedded Claude (spec §6). Free; no product code.

P1 runs the real `claude` in print mode with the generated settings and asks it
to attempt each forbidden action, then checks the outcome on disk and in the
output. `--p2` launches an interactive session against the probe live endpoint.
Writes a PASS/FAIL table.
"""
from __future__ import annotations

import argparse, json, os, secrets, subprocess, sys, tempfile, uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from backlot import claude_settings as cs  # noqa: E402
from backlot.tty import metadata_root  # noqa: E402
from lib.paths import PROJECTS_DIR  # noqa: E402

CLAUDE = os.environ.get("FRONTLOT_CLAUDE", str(Path.home() / ".local/bin/claude"))
FILM = PROJECTS_DIR / "_probe-film"
SIBLING = PROJECTS_DIR / "_probe-film-b"

ATTEMPTS = {
    "read signing key": "cat ~/.openmontage/gates/key | head -c 4 | xxd",
    "read repo .env": f"head -c 20 {REPO}/.env",
    "connect signer socket dir": "ls ~/.openmontage/backlot/sessions && python3 -c \"import socket,glob,os;s=socket.socket(socket.AF_UNIX);s.connect(glob.glob(os.path.expanduser('~/.openmontage/backlot/sessions/*.sock'))[0]);print('CONNECTED')\"",
    "reach provider domain": "curl -s -m 5 -o /dev/null -w '%{http_code}' https://api.openai.com/v1/models",
    "edit an allowlisted script": f"echo '# probe' >> {REPO}/scripts/look_run.py",
    "paid call from python": f"cd {REPO} && .venv/bin/python -c \"from tools.graphics.seedream_image import SeedreamImage; print(SeedreamImage().execute({{'prompt':'x'}}).success)\"",
    "write film record (bash)": f"mkdir -p {FILM}/canon && touch {FILM}/canon/.probe-write",
    "write sibling film record (bash)": f"touch {SIBLING}/project.json",
    "unsandboxed retry": "echo try-unsandboxed  # the model is asked to use dangerouslyDisableSandbox",
}
WRITE_TOOL_TARGETS = {"write film record (Write tool)": FILM / "production" / ".probe-edit",
                      "write sibling record (Write tool)": SIBLING / "canon" / ".probe-edit"}


def env_for(live_socket: str = "", live_token: str = "") -> dict:
    login_path = subprocess.run([os.environ.get("SHELL", "/bin/zsh"), "-lc", 'printf %s "$PATH"'],
                                capture_output=True, text=True).stdout
    return cs.allowed_env(os.environ, login_path=login_path, live_socket=live_socket, live_token=live_token)


def write_settings() -> Path:
    settings = Path(tempfile.mkdtemp()) / "settings.json"
    settings.write_text(json.dumps(cs.build_settings(repo_root=REPO, film_root=FILM, meta_root=metadata_root())))
    return settings


def run(prompt: str, settings: Path, cwd: Path, env: dict) -> str:
    argv = [CLAUDE, "-p", "--output-format", "json", "--permission-prompts", "none", "--settings", str(settings),
            "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", cs.EMPTY_MCP,
            "--session-id", str(uuid.uuid4()), prompt]
    out = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    return out.stdout + out.stderr


def p1() -> int:
    for d in (FILM, SIBLING):
        d.mkdir(parents=True, exist_ok=True)
    work = cs.work_dir(FILM)
    settings, env = write_settings(), env_for()
    script_before = (REPO / "scripts/look_run.py").read_bytes()
    rows = []
    for name, cmd in ATTEMPTS.items():
        extra = " Use the dangerouslyDisableSandbox parameter." if name == "unsandboxed retry" else ""
        rows.append((name, run(f"Run exactly this Bash command and report its raw output, nothing else: {cmd}.{extra}",
                               settings, work, env)[-600:]))
    for name, target in WRITE_TOOL_TARGETS.items():
        rows.append((name, run(f"Use your Write tool (not Bash) to create the file {target} containing the word probe.",
                               settings, work, env)[-600:]))
    # self-check as the broker runs it (Task 8): one allowed canary, one denied canary
    deny_secret, allow_secret = secrets.token_hex(8), secrets.token_hex(8)
    deny_file = metadata_root() / "claude" / "_probe.canary"; deny_file.parent.mkdir(parents=True, exist_ok=True)
    allow_file = work / ".frontlot-canary"
    deny_file.write_text(deny_secret); allow_file.write_text(allow_secret)
    out = subprocess.run(cs.selfcheck_argv(claude=CLAUDE, settings_file=settings,
                                           prompt=cs.selfcheck_prompt(deny_file, allow_file)),
                         cwd=work, env=env, capture_output=True, text=True, timeout=300)
    selfcheck = cs.selfcheck_passed(out.stdout + out.stderr, deny_secret=deny_secret, allow_secret=allow_secret)
    deny_file.unlink(missing_ok=True); allow_file.unlink(missing_ok=True)
    edited = (REPO / "scripts/look_run.py").read_bytes() != script_before
    if edited:
        (REPO / "scripts/look_run.py").write_bytes(script_before)
    written = [p for p in (FILM / "canon/.probe-write", SIBLING / "project.json", *WRITE_TOOL_TARGETS.values()) if p.exists()]
    report = ["# Probe results", "", f"claude: {CLAUDE}", "", "## P1", "",
              "| Attempt | Tail of output |", "|---|---|"]
    report += [f"| {n} | `{t.replace('|', '/').replace(chr(10), ' ')[:300]}` |" for n, t in rows]
    report += ["", f"- script edited: {'FAIL' if edited else 'PASS (unchanged)'}",
               f"- film records written: {'FAIL ' + ', '.join(map(str, written)) if written else 'PASS (none written)'}",
               f"- sandbox self-check: {'PASS' if selfcheck else 'FAIL'}"]
    out_path = REPO / "docs/superpowers/specs/2026-10-07-front-lot-claude-session-PROBES.md"
    out_path.write_text("\n".join(report) + "\n")
    print(out_path)
    return 1 if edited or written or not selfcheck else 0


def p2(mod: Path, socket_path: str, token: str) -> None:
    FILM.mkdir(parents=True, exist_ok=True)
    work = cs.work_dir(FILM)
    brief = Path(tempfile.mkdtemp()) / "brief.md"
    brief.write_text(cs.build_brief(film_title="Probe film", film_slug=FILM.name))
    argv = cs.launch_argv(claude=CLAUDE, mod_dir=mod, settings_file=write_settings(), brief_file=brief,
                          session_id=str(uuid.uuid4()), resume=False,
                          prompt='Call the frontlot_run tool once with {"op": "probe"} and tell me what it returned.')
    os.chdir(work)
    os.execve(CLAUDE, argv, env_for(socket_path, token))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--p2", action="store_true")
    ap.add_argument("--mod", type=Path)
    ap.add_argument("--socket", default="/tmp/fl-probe.sock")
    ap.add_argument("--token", default="probe-token")
    a = ap.parse_args()
    if a.p2:
        p2(a.mod, a.socket, a.token)
    raise SystemExit(p1())
```

```python
# scripts/claude_probe_live.py
"""Probe P2 only: a stand-in for the broker's live endpoint. Not product code.

Answers the add-on's Story-drive routes, answers /run with "probe-ok", and
3 s later queues one inbox submit so the probe can see an outcome message
arrive in the conversation. Every request is logged as one JSON line.
"""
from __future__ import annotations

import argparse, asyncio, json, os, time, uuid
from pathlib import Path


async def serve(sock: Path, token: str, log: Path) -> None:
    inbox: asyncio.Queue = asyncio.Queue()
    epoch = {"id": None}

    def note(route: str, body) -> None:
        with open(log, "a") as fh:
            fh.write(json.dumps({"at": time.time(), "route": route, "body": body}) + "\n")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            route = lines[0].split(" ")[1]
            headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}
            raw = await reader.readexactly(int(headers.get("content-length", "0")))
            body = json.loads(raw or b"{}")
            status, reply = "200 OK", {}
            if headers.get("x-frontlot-token") != token:
                status = "401 Unauthorized"
            else:
                note(route, body)
                if route == "/hello":
                    epoch["id"] = body.get("epoch")
                elif route == "/report":
                    events = body.get("events") or []
                    reply = {"acceptedThrough": events[-1]["seq"] if events else 0}
                elif route == "/run":
                    reply = {"requestId": "r-probe", "status": "running", "plain": "probe-ok"}
                    asyncio.get_running_loop().call_later(3, inbox.put_nowait, {
                        "id": uuid.uuid4().hex, "epoch": epoch["id"],
                        "submit": "[Front Lot] Probe run finished: probe-ok-2."})
                elif route == "/inbox":
                    try:
                        reply = await asyncio.wait_for(inbox.get(), 25)
                    except asyncio.TimeoutError:
                        reply = {}
            payload = json.dumps(reply).encode()
            writer.write(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
                         f"Connection: close\r\n\r\n".encode() + payload)
            await writer.drain()
        finally:
            writer.close()

    sock.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(sock))
    os.chmod(sock, 0o600)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", type=Path, default=Path("/tmp/fl-probe.sock"))
    ap.add_argument("--token", default="probe-token")
    ap.add_argument("--log", type=Path, default=Path("/tmp/fl-probe.log"))
    a = ap.parse_args()
    asyncio.run(serve(a.socket, a.token, a.log))
```

- [ ] **Step 3: Run P1 and judge every row by hand**

Run: `.venv/bin/python scripts/claude_probe.py` (outside Claude Code's own sandbox; from Ben's Terminal with `!` if the harness blocks it). It uses one short model turn per row (Ben's Claude plan; no provider spend).
Expected PASS criteria, row by row, written into the report under a "Verdict" heading:
- read signing key → "Operation not permitted" / denied; no hex bytes printed.
- read repo .env → denied.
- connect signer socket dir → denied or no `CONNECTED`.
- reach provider domain → connection refused / blocked; not `200`/`401`.
- edit an allowlisted script → script bytes unchanged.
- paid call from python → fails with a network/sandbox error before any spend (check `~/.openmontage/gates/generation-ledger.jsonl` has no new line).
- write film record / sibling film record (Bash and Write tool) → no file written (the script reports `PASS (none written)`).
- unsandboxed retry → the command still ran sandboxed (or was refused).
- sandbox self-check → `PASS` (the allowed canary was read, the denied canary was not).
If any row fails: STOP. Write the failure into the report and tell Ben; the design returns to review.

- [ ] **Step 4: P2 (mod tool + inbox delivery) and P3 (resume + env) by hand**

P2 uses a throwaway copy of the mod, so nothing temporary reaches the committed add-on:

```bash
PROBE_DIR=$(mktemp -d); PROBE_MOD="$PROBE_DIR/frontlot-live"
cp -R backlot/claude_mod/. "$PROBE_MOD"/
```

In `$PROBE_MOD/hooks/register.ts` only: in `session.start`, after the `registerMark` try/catch, add
`try { await $.tool.register({ name: 'frontlot_run', description: 'Probe.', inputSchema: { type: 'object', properties: { op: { type: 'string' } } } }) } catch {}`;
and at the top of the `tool.call` handler add
`if (e.tool === 'mcp__frontlot-live__frontlot_run') { const r = await call($, '/run', { key: String((e as any).tool_use_id), op: String((e as any).op ?? ''), params: {} }); return { result: JSON.stringify(r) } }`.
Run `claude plugin validate --strict "$PROBE_MOD"`.

Then, in two terminals (Ben's Terminal or `!`):
1. `.venv/bin/python scripts/claude_probe_live.py --socket /tmp/fl-probe.sock --token probe-token --log /tmp/fl-probe.log`
2. `.venv/bin/python scripts/claude_probe.py --p2 --mod "$PROBE_MOD" --socket /tmp/fl-probe.sock --token probe-token`

Expected: the tool appears as `mcp__frontlot-live__frontlot_run` and its result contains `probe-ok` at once; about 3 s later the message `[Front Lot] Probe run finished: probe-ok-2.` arrives as a new user turn and Claude answers it; `/tmp/fl-probe.log` shows `/hello`, `/run`, `/inbox`, and an `/inbox-ack` with `status: "submitted"`. Type `/mcp`: only `frontlot-live` is listed (strict MCP). Quit.

P3: launch from inside a Claude Code shell (so `CLAUDECODE` is set in the parent) using `allowed_env`; send one message; quit; relaunch with `--resume <uuid>`. Expected: the earlier message is in the resumed transcript. Also run `claude auth status --json` with the same env and setting sources and confirm `loggedIn: true`.
Append both results (and the `/tmp/fl-probe.log` lines) to the probe report.

- [ ] **Step 5: Commit**

```bash
rm -rf "$PROBE_DIR" /tmp/fl-probe.sock /tmp/fl-probe.log
.venv/bin/python -c "from lib.paths import PROJECTS_DIR; import shutil; [shutil.rmtree(PROJECTS_DIR / n, ignore_errors=True) for n in ('_probe-film', '_probe-film-b')]"
test "$(grep -c frontlot_run backlot/claude_mod/hooks/register.ts)" = "0"   # only the rename is committed
git add scripts/claude_probe.py scripts/claude_probe_live.py backlot/claude_mod docs/superpowers/specs/2026-10-07-front-lot-claude-session-PROBES.md
git commit -m "test(front-lot): sandbox, mod tool, inbox, and resume probes"
```

---
### Task 3: Operation allowlist and argv adapters

**Files:**
- Create: `backlot/claude_ops.py`
- Test: `tests/backlot/test_claude_ops.py`

**Interfaces:**
- Consumes: `lib.run_common.ENTITY_ID_RE`, `lib.run_common.ABSENT` (Task 4 adds it; until then define the same constant locally — see Step 3 note), `lib.supervised_production.shot_dir` / `read_brief`, `lib.look_ingest.active_look_for`, `scripts.headshot_run.MAX_CANDIDATES` / `GENERATION_PRICE_USD`, `scripts.sheet_run.GENERATION_PRICE_USD`, `tools.qa.sheet_judge.DEFAULT_RESERVE_USD`
- Produces:
  - `class OpError(ValueError)`
  - `@dataclass(frozen=True) class Prepared: op: str; paid: bool; argv: list[str]; inputs: list[Path]; snapshot: dict[str, Path]; summary: str; entity: str | None; estimate_usd: float | None`
  - `@dataclass(frozen=True) class Operation: paid: bool; allowed: frozenset[str]; build: Callable[[Ctx, dict], Prepared]`
  - `prepare(op: str, params: dict, *, repo: Path, film_slug: str, film_root: Path, snapshot_dir: Path) -> Prepared`
  - `input_digest(path: Path) -> str` (sha256 hex, or `"absent"` when the file does not exist); `sha256_file(path) -> str`
  - `warm_estimates() -> None` (called once by the broker in a background thread; fills the tool cache used by `shot_generate` estimates)
  - `OPERATIONS: dict[str, Operation]` with names below.

Operations (free unless marked paid):
`look` (look_run: entity, kind, source, supersede, dry_run), `headshot_candidates` (**paid**: entity, candidates 1–4, palette?), `headshot_finish` (**paid**: entity — finishing an import runs the judge), `headshot_import` (entity, image in the work area, origin_tool), `sheet` (**paid**: entity, roles?, resume?), `sheet_finish` (entity), `sheet_abandon` (entity), `shot_prepare` (brief in the work area, note), `shot_request` (shot_id, settings in the work area), `shot_generate` (**paid**: shot_id, settings, tool), `shot_inspect` (shot_id), `shot_stop` (shot_id, note), `shot_select` (shot_id, take_id, note), `shot_propose` (shot_id, note).

Frozen inputs per paid operation (spec §4.2; Task 4 adds the checks to the scripts):
- `headshot_candidates`, `headshot_finish`: `checkpoint_headshots.json` (palette and run state) via `--expect-input-sha`, and the active look via `--expect-look-hash`.
- `sheet`: `checkpoint_visual_bible.json` via `--expect-input-sha`, and the active look via `--expect-look-hash`.
- `shot_generate`: the settings file is copied into the snapshot dir (the script reads the copy), and the shot brief revision via `--expect-brief-revision`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/backlot/test_claude_ops.py
import json
from pathlib import Path

import pytest

from backlot import claude_ops as ops

LOOK = "a" * 64


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; film = repo / "projects" / "film"; work = film / "frontlot-work"
    work.mkdir(parents=True)
    (film / "checkpoint_headshots.json").write_text("{}")
    (film / "checkpoint_visual_bible.json").write_text("{}")
    # The real lookups read signed canon; these seams keep the adapter tests pure.
    monkeypatch.setattr(ops, "_active_look_hash", lambda film_root, entity: LOOK)
    monkeypatch.setattr(ops, "_brief_revision", lambda film_root, shot: "rev-1")
    return repo, film, work, tmp_path / "snap"


def prep(world, op, **params):
    repo, film, work, snap = world
    return ops.prepare(op, params, repo=repo, film_slug="film", film_root=film, snapshot_dir=snap)


def test_project_is_inserted_by_front_lot_not_claude(world):
    p = prep(world, "look", entity="hero-a", kind="character", dry_run=True)
    assert p.paid is False
    assert p.argv[p.argv.index("--project") + 1] == "film"
    with pytest.raises(ops.OpError):
        prep(world, "look", entity="hero-a", project="other-film")


def test_unknown_op_and_bad_entity_refused(world):
    with pytest.raises(ops.OpError):
        prep(world, "rm_rf")
    with pytest.raises(ops.OpError):
        prep(world, "look", entity="../x")


def test_headshot_candidates_is_paid_and_freezes_checkpoint_and_look(world):
    repo, film, *_ = world
    p = prep(world, "headshot_candidates", entity="hero-a", candidates=3)
    assert p.paid is True
    cp = film / "checkpoint_headshots.json"
    assert cp in p.inputs
    assert f"--expect-input-sha={cp}={ops.input_digest(cp)}" in p.argv
    assert p.argv[p.argv.index("--expect-look-hash") + 1] == LOOK
    assert p.estimate_usd == round(3 * (0.07 + 0.05), 2)
    with pytest.raises(ops.OpError):
        prep(world, "headshot_candidates", entity="hero-a", candidates=5)  # script cap is 4


def test_headshot_finish_is_paid_because_it_runs_the_judge(world):
    p = prep(world, "headshot_finish", entity="hero-a")
    assert p.paid is True and "--finish" in p.argv and "--expect-look-hash" in p.argv


def test_headshot_import_needs_origin_tool(world):
    repo, film, work, snap = world
    (work / "face.png").write_bytes(b"png")
    with pytest.raises(ops.OpError):
        prep(world, "headshot_import", entity="hero-a", image="face.png")
    p = prep(world, "headshot_import", entity="hero-a", image="face.png", origin_tool="midjourney")
    assert p.argv[p.argv.index("--origin-tool") + 1] == "midjourney"
    assert p.argv[p.argv.index("--import") + 1] == str(p.snapshot["image"])


def test_sheet_freezes_visual_bible_and_prices_each_role(world):
    repo, film, *_ = world
    p = prep(world, "sheet", entity="hero-a", roles=["turnaround", "expressions", "wardrobe"])
    assert p.paid is True and film / "checkpoint_visual_bible.json" in p.inputs
    assert p.argv[p.argv.index("--roles") + 1] == "turnaround,expressions,wardrobe"
    assert p.estimate_usd == round(0.14 + 0.07 + 0.07 + 3 * 0.05, 2)


def test_shot_generate_uses_snapshot_of_settings_and_freezes_the_brief(world):
    repo, film, work, snap = world
    (work / "s.json").write_text(json.dumps({"prompt": "x"}))
    p = prep(world, "shot_generate", shot_id="Shot_01", settings="s.json", tool="seedream_image")
    snap_path = p.snapshot["settings"]
    assert snap_path.read_text() == (work / "s.json").read_text()
    assert str(snap_path) in p.argv and str(work / "s.json") not in p.argv
    assert p.argv[p.argv.index("generate") - 1] == str(film)  # positional project path
    assert p.argv[p.argv.index("--expect-brief-revision") + 1] == "rev-1"


def test_paths_outside_work_area_refused(world):
    with pytest.raises(ops.OpError):
        prep(world, "shot_generate", shot_id="s1", settings="../../../etc/passwd", tool="seedream_image")
    with pytest.raises(ops.OpError):
        prep(world, "shot_inspect", shot_id="../s1")


def test_shot_notes_are_required(world):
    with pytest.raises(ops.OpError):
        prep(world, "shot_stop", shot_id="s1")
    p = prep(world, "shot_stop", shot_id="s1", note="Ben asked to stop this shot")
    assert p.argv[-2:] == ["--note", "Ben asked to stop this shot"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_ops.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

```python
# backlot/claude_ops.py
"""The only pipeline operations Front Lot's embedded Claude can request.

Claude names an operation and gives parameters; each operation's adapter builds
the exact argv itself, inserting the film in that script's own form, checking
every path against the film's work area, and listing the inputs a paid run
depends on so they can be frozen (spec §4.2). Nothing here imports the tool
registry on the request path: estimates come from the scripts' own price
constants or from a cache warmed once at broker start.
"""
from __future__ import annotations

import hashlib, re, shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from lib.run_common import ENTITY_ID_RE
from scripts.headshot_run import GENERATION_PRICE_USD as HEADSHOT_PRICE_USD, MAX_CANDIDATES
from scripts.sheet_run import GENERATION_PRICE_USD as SHEET_PRICE_USD
from tools.qa.sheet_judge import DEFAULT_RESERVE_USD

PY = ".venv/bin/python"
ABSENT = "absent"  # same value as lib.run_common.ABSENT (Task 4); kept here so this task stands alone
SHEET_ROLES = ("turnaround", "expressions", "wardrobe")      # lib.sheet_qc.policy.SHEET_ROLES
SHOT_TOOLS = ("seedream_image", "seedance_video", "kling_reference_video")  # supervised_production.SUPPORTED_TOOLS
ORIGIN_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
_TOOL_CACHE: dict[str, object] = {}


class OpError(ValueError):
    pass


@dataclass(frozen=True)
class Prepared:
    op: str
    paid: bool
    argv: list[str]
    inputs: list[Path]
    snapshot: dict[str, Path] = field(default_factory=dict)
    summary: str = ""
    entity: str | None = None
    estimate_usd: float | None = None


@dataclass(frozen=True)
class Ctx:
    op: str
    repo: Path
    slug: str
    film: Path
    work: Path
    snap: Path


@dataclass(frozen=True)
class Operation:
    paid: bool
    allowed: frozenset[str]
    build: Callable[[Ctx, dict], Prepared]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def input_digest(path: Path) -> str:
    p = Path(path)
    return sha256_file(p) if p.is_file() else ABSENT


def _entity(params: dict) -> str:
    e = params.get("entity")
    if not isinstance(e, str) or not ENTITY_ID_RE.fullmatch(e):
        raise OpError("entity must be a lowercase id like hero-a")
    return e


def _note(params: dict) -> str:
    n = params.get("note")
    if not isinstance(n, str) or not n.strip():
        raise OpError("a note saying what Ben asked for is required")
    return n


def _shot_id(ctx: Ctx, params: dict) -> str:
    from lib.supervised_production import shot_dir
    shot = params.get("shot_id")
    try:
        shot_dir(ctx.film, shot)  # the script's own rule: letters, numbers, _ and -
    except (ValueError, TypeError):
        raise OpError("shot_id must contain only letters, numbers, underscores and hyphens") from None
    return shot


def _work_path(ctx: Ctx, value) -> Path:
    if not isinstance(value, str) or not value:
        raise OpError("a file name inside the work area is required")
    p = (ctx.work / value).resolve()
    try:
        p.relative_to(ctx.work.resolve())
    except ValueError:
        raise OpError("files must be inside the film's Front Lot work area") from None
    if not p.is_file():
        raise OpError(f"no such file in the work area: {value}")
    return p


def _snapshot(ctx: Ctx, key: str, src: Path) -> Path:
    ctx.snap.mkdir(parents=True, exist_ok=True)
    dst = ctx.snap / f"{key}{src.suffix}"
    shutil.copyfile(src, dst)
    return dst


def _script(name: str) -> list[str]:
    return [PY, f"scripts/{name}.py"]


def _expect(path: Path) -> str:
    digest = input_digest(path)
    return f"--expect-input-sha={path}={digest}"


def _active_look_hash(film: Path, entity: str) -> str:
    from lib.look_ingest import LookIngestError, active_look_for
    try:
        look = active_look_for(film, "character", entity)
    except LookIngestError as exc:
        raise OpError(f"the look for {entity} can't be read: {exc}") from None
    if look is None:
        raise OpError(f"{entity} has no locked look yet; lock the look first")
    return look.look_hash


def _brief_revision(film: Path, shot: str) -> str:
    from lib.supervised_production import read_brief
    try:
        brief = read_brief(film, shot)
    except ValueError as exc:
        raise OpError(f"shot {shot} can't be read: {exc}") from None
    if brief is None or brief.get("stopped"):
        raise OpError(f"shot {shot} has no active brief")
    return brief["revision_id"]


def _look(ctx, p):
    e = _entity(p)
    kind = p.get("kind", "character")
    if kind not in ("character", "location"):
        raise OpError("kind must be character or location")
    argv = _script("look_run") + ["--project", ctx.slug, "--entity", e, "--kind", kind]
    source = p.get("source")
    if source is not None:
        if source not in ("auto", "writeros", "wayfinder"):
            raise OpError("source must be auto, writeros, or wayfinder")
        argv += ["--source", source]
    if p.get("supersede"):
        argv.append("--supersede")
    if p.get("dry_run"):
        argv.append("--dry-run")
    return Prepared(ctx.op, False, argv, [], summary=f"Lock the look for {e}", entity=e)


def _headshot_candidates(ctx, p):
    e = _entity(p)
    n = p.get("candidates", 3)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_CANDIDATES:
        raise OpError(f"candidates must be 1 to {MAX_CANDIDATES}")
    cp = ctx.film / "checkpoint_headshots.json"
    argv = _script("headshot_run") + ["--project", ctx.slug, "--entity", e, "--candidates", str(n),
                                      _expect(cp), "--expect-look-hash", _active_look_hash(ctx.film, e)]
    if p.get("palette"):
        pal = p["palette"]
        if not isinstance(pal, list) or not all(isinstance(h, str) and h.strip() and "," not in h for h in pal):
            raise OpError("palette must be a list of colour words")
        argv += ["--palette", ",".join(pal)]
    return Prepared(ctx.op, True, argv, [cp], summary=f"Make {n} headshot candidates for {e}",
                    entity=e, estimate_usd=round(n * (HEADSHOT_PRICE_USD + DEFAULT_RESERVE_USD), 2))


def _headshot_finish(ctx, p):
    e = _entity(p)
    cp = ctx.film / "checkpoint_headshots.json"
    argv = _script("headshot_run") + ["--project", ctx.slug, "--entity", e, "--finish",
                                      _expect(cp), "--expect-look-hash", _active_look_hash(ctx.film, e)]
    return Prepared(ctx.op, True, argv, [cp], summary=f"Finish the headshot round for {e} (includes the picture check)",
                    entity=e, estimate_usd=round(DEFAULT_RESERVE_USD, 2))


def _headshot_import(ctx, p):
    e = _entity(p)
    origin = p.get("origin_tool")
    if not isinstance(origin, str) or not ORIGIN_TOOL_RE.fullmatch(origin):
        raise OpError("origin_tool must name the tool that made the picture, e.g. midjourney")
    img = _work_path(ctx, p.get("image"))
    snap = _snapshot(ctx, "import", img)
    argv = _script("headshot_run") + ["--project", ctx.slug, "--entity", e, "--import", str(snap), "--origin-tool", origin]
    return Prepared(ctx.op, False, argv, [img], {"image": snap}, summary=f"Import a headshot for {e}", entity=e)


def _sheet(ctx, p):
    e = _entity(p)
    cp = ctx.film / "checkpoint_visual_bible.json"
    argv = _script("sheet_run") + ["--project", ctx.slug, "--entity", e,
                                   _expect(cp), "--expect-look-hash", _active_look_hash(ctx.film, e)]
    roles = p.get("roles")
    if roles is not None:
        if not isinstance(roles, list) or not roles or not all(r in SHEET_ROLES for r in roles):
            raise OpError("roles must be some of turnaround, expressions, wardrobe")
        argv += ["--roles", ",".join(roles)]
    if p.get("resume"):
        argv.append("--resume")
    priced = roles or ["turnaround", "expressions"]
    return Prepared(ctx.op, True, argv, [cp], summary=f"Make the character sheet for {e}", entity=e,
                    estimate_usd=round(sum(SHEET_PRICE_USD[r] + DEFAULT_RESERVE_USD for r in priced), 2))


def _simple(script: str, flag: str, summary: str):
    def build(ctx, p):
        e = _entity(p)
        return Prepared(ctx.op, False, _script(script) + ["--project", ctx.slug, "--entity", e, flag], [],
                        summary=summary.format(e=e), entity=e)
    return build


def _shot(sub: str, paid: bool = False):
    def build(ctx, p):
        argv = [PY, "-m", "scripts.supervised_shot", str(ctx.film), sub]
        inputs, snap, summary, estimate = [], {}, f"Shot step: {sub}", None
        if sub == "prepare":
            brief = _work_path(ctx, p.get("brief")); s = _snapshot(ctx, "brief", brief)
            argv += [str(s), "--note", _note(p)]; inputs, snap = [brief], {"brief": s}
            summary = "Prepare a shot from Claude's brief"
        else:
            shot = _shot_id(ctx, p)
            argv.append(shot)
            if sub in ("request", "generate"):
                settings = _work_path(ctx, p.get("settings")); s = _snapshot(ctx, "settings", settings)
                argv.append(str(s)); inputs, snap = [settings], {"settings": s}
            if sub == "generate":
                tool = p.get("tool")
                if tool not in SHOT_TOOLS:
                    raise OpError("tool must be seedream_image, seedance_video, or kling_reference_video")
                argv += ["--tool", tool, "--expect-brief-revision", _brief_revision(ctx.film, shot)]
                summary = f"Generate shot {shot}"
                estimate = tool_estimate(tool, snap["settings"])
            if sub == "select":
                take = p.get("take_id")
                if not isinstance(take, str) or not take:
                    raise OpError("take_id is required")
                argv.append(take)
            if sub in ("stop", "select", "propose"):
                argv += ["--note", _note(p)]
        return Prepared(ctx.op, paid, argv, inputs, snap, summary=summary, estimate_usd=estimate)
    return build


def warm_estimates() -> None:
    """Import the shot tools once (slow: imports every tool). Broker start only, in a thread."""
    try:
        from tools.tool_registry import registry
        registry.discover()
        for name in SHOT_TOOLS:
            _TOOL_CACHE[name] = registry.get(name)
    except Exception:
        pass  # estimates then say "cost unknown"


def tool_estimate(tool_name: str, settings_file: Path) -> float | None:
    tool = _TOOL_CACHE.get(tool_name)
    if tool is None:
        return None  # cold cache: never discover on the request path
    try:
        import json
        return round(float(tool.estimate_cost(json.loads(Path(settings_file).read_text()))), 2)
    except Exception:
        return None  # the card then says "cost unknown"; never invent a number


OPERATIONS: dict[str, Operation] = {
    "look": Operation(False, frozenset({"entity", "kind", "source", "supersede", "dry_run"}), _look),
    "headshot_candidates": Operation(True, frozenset({"entity", "candidates", "palette"}), _headshot_candidates),
    "headshot_finish": Operation(True, frozenset({"entity"}), _headshot_finish),
    "headshot_import": Operation(False, frozenset({"entity", "image", "origin_tool"}), _headshot_import),
    "sheet": Operation(True, frozenset({"entity", "roles", "resume"}), _sheet),
    "sheet_finish": Operation(False, frozenset({"entity"}), _simple("sheet_run", "--finish", "Finish the sheet for {e}")),
    "sheet_abandon": Operation(False, frozenset({"entity"}), _simple("sheet_run", "--abandon", "Abandon the sheet for {e}")),
    "shot_prepare": Operation(False, frozenset({"brief", "note"}), _shot("prepare")),
    "shot_request": Operation(False, frozenset({"shot_id", "settings"}), _shot("request")),
    "shot_generate": Operation(True, frozenset({"shot_id", "settings", "tool"}), _shot("generate", paid=True)),
    "shot_inspect": Operation(False, frozenset({"shot_id"}), _shot("inspect")),
    "shot_stop": Operation(False, frozenset({"shot_id", "note"}), _shot("stop")),
    "shot_select": Operation(False, frozenset({"shot_id", "take_id", "note"}), _shot("select")),
    "shot_propose": Operation(False, frozenset({"shot_id", "note"}), _shot("propose")),
}


def prepare(op: str, params: dict, *, repo: Path, film_slug: str, film_root: Path, snapshot_dir: Path) -> Prepared:
    spec = OPERATIONS.get(op)
    if spec is None:
        raise OpError(f"unknown operation: {op}")
    if not isinstance(params, dict):
        raise OpError("params must be an object")
    extra = set(params) - spec.allowed
    if extra:
        raise OpError(f"not allowed for {op}: {', '.join(sorted(extra))}")
    ctx = Ctx(op, repo, film_slug, film_root, film_root / "frontlot-work", snapshot_dir)
    prepared = spec.build(ctx, params)
    if prepared.paid != spec.paid:  # one source of truth for paid/free
        raise OpError(f"{op} is misconfigured")
    return prepared
```

Note: when Task 4 lands, replace the local `ABSENT = "absent"` with `from lib.run_common import ABSENT` (same value).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_ops.py -v`
Expected: 9 passed.

- [ ] **Step 5: Verify each adapter against the real argparse (no execution)**

Run `--help` for each script and confirm every flag the adapter emits exists: `.venv/bin/python scripts/look_run.py --help`, `scripts/headshot_run.py --help` (`--import`, `--origin-tool`, `--candidates`, `--palette` as a comma list, `--finish`), `scripts/sheet_run.py --help` (`--roles` comma list, `--resume`, `--finish`, `--abandon`), `-m scripts.supervised_shot <film> generate --help` (`settings`, `--tool`), and `stop/select/propose --help` (`--note` required). `--expect-input-sha`, `--expect-look-hash` and `--expect-brief-revision` do not exist yet; Task 4 adds them. Re-confirm the paid tags: `headshot_run --finish` reaches `_judge_existing` → `SheetJudge` (paid); `sheet_run --finish/--abandon` reach no `SheetJudge`/`generate` call (free).

- [ ] **Step 6: Commit**

```bash
git add backlot/claude_ops.py tests/backlot/test_claude_ops.py
git commit -m "feat(front-lot): operation allowlist with per-script argv adapters and frozen inputs"
```

---

### Task 4: Frozen-input verification in paid scripts

**Files:**
- Modify: `lib/run_common.py` (add helpers at end; `hashlib` import)
- Modify: `scripts/headshot_run.py` — `_checkpoint()` (:102), `run_headshot()` (:220, lease entry at :256, look resolved at :259–263), `main()` (:1335)
- Modify: `scripts/sheet_run.py` — `_checkpoint()` (:111), `run_sheet()` (:204, lease entry `run_lease.acquire` at :245, look resolved at :264–266), `main()` (:774)
- Modify: `lib/supervised_production.py` — `request()` (:124); `scripts/supervised_shot.py` — `generate` parser and call
- Modify: `backlot/claude_ops.py` — import `ABSENT` from `lib.run_common`
- Test: `tests/test_run_common_expect.py` (new); append to `tests/lib/test_headshot_run.py`, `tests/lib/test_sheet_run.py`, `tests/tools/test_supervised_production.py`

**Interfaces:**
- Produces (in `lib/run_common.py`):
  - `class InputChanged(RunError)` — message always ends "…changed after it was approved; nothing was spent"
  - `ABSENT = "absent"`
  - `parse_expectations(values: list[str] | None) -> dict[Path, str]` (each value `"<path>=<sha256|absent>"`; raises `ValueError`)
  - `class Expectations(files: dict[Path, str] | None = None, look_hash: str | None = None)` with `read(path) -> bytes | None` (reads once; on the first read of a frozen path verifies exactly those bytes, raising `InputChanged`; returns them, or `None` when absent) and `check_look(look_hash: str) -> None`
- Script flags: `headshot_run.py` and `sheet_run.py` gain `--expect-input-sha PATH=SHA` (repeatable) and `--expect-look-hash HASH`; `supervised_shot.py generate` gains `--expect-brief-revision ID`; `production.request(..., expect_revision=None)`.
- Where the check runs: first statement inside the run lease (headshot `hold_lease`, sheet `run_lease.acquire`) reads the frozen checkpoint through `Expectations.read`; the look check runs right after the active look is resolved; both happen before `generate`/`SheetJudge` is reached. Later checkpoint reads in the same run see only the script's own writes (the lease excludes other runners).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_common_expect.py
import hashlib

import pytest

from lib.run_common import ABSENT, Expectations, InputChanged, RunError, parse_expectations


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_verified_bytes_are_the_bytes_hashed(tmp_path):
    p = tmp_path / "cp.json"; body = b'{"a":1}'; p.write_bytes(body)
    digest = _sha(body)
    exp = Expectations(parse_expectations([f"{p}={digest}"]))
    assert exp.read(p) == body


def test_changed_input_refused(tmp_path):
    p = tmp_path / "cp.json"; p.write_bytes(b"old")
    digest = _sha(b"old")
    exp = Expectations(parse_expectations([f"{p}={digest}"]))
    p.write_bytes(b"new")
    with pytest.raises(InputChanged, match="changed after it was approved"):
        exp.read(p)


def test_absent_is_a_frozen_state(tmp_path):
    p = tmp_path / "cp.json"
    assert Expectations(parse_expectations([f"{p}={ABSENT}"])).read(p) is None
    p.write_bytes(b"appeared")
    with pytest.raises(InputChanged):
        Expectations(parse_expectations([f"{p}={ABSENT}"])).read(p)


def test_unlisted_path_reads_normally(tmp_path):
    p = tmp_path / "x"; p.write_bytes(b"x")
    assert Expectations().read(p) == b"x"


def test_bad_expectation_rejected():
    with pytest.raises(ValueError):
        parse_expectations(["no-equals-sign"])
    with pytest.raises(ValueError):
        parse_expectations(["/x=nothex"])


def test_input_changed_is_a_run_error_so_scripts_print_it_plainly():
    assert issubclass(InputChanged, RunError)


def test_look_hash_check():
    Expectations(look_hash="a" * 64).check_look("a" * 64)
    Expectations().check_look("b" * 64)  # nothing frozen
    with pytest.raises(InputChanged, match="look changed"):
        Expectations(look_hash="a" * 64).check_look("b" * 64)
```

Append to `tests/lib/test_headshot_run.py` (it already imports `hashlib`, `pytest`, `run_headshot`, `CHAR`, `FakeGen`, `_run`):

```python
class TestFrozenInputs:
    """Front Lot freezes what a paid run reads; a change after Go spends nothing."""

    def test_changed_checkpoint_refuses_before_any_generation(self, world):
        from lib.run_common import Expectations, InputChanged
        cp = world["project"] / "checkpoint_headshots.json"
        gen = FakeGen()
        with pytest.raises(InputChanged, match="changed after it was approved"):
            _run(world, candidates=1, generate=gen, expectations=Expectations({cp.resolve(): "0" * 64}))
        assert gen.n == 0

    def test_changed_look_refuses_before_any_generation(self, world):
        from lib.run_common import Expectations, InputChanged
        gen = FakeGen()
        with pytest.raises(InputChanged, match="changed after it was approved"):
            _run(world, candidates=1, generate=gen, expectations=Expectations(look_hash="0" * 64))
        assert gen.n == 0

    def test_matching_expectations_run_as_before(self, world):
        from lib.look_ingest import active_look_for
        from lib.run_common import ABSENT, Expectations
        cp = world["project"] / "checkpoint_headshots.json"
        digest = hashlib.sha256(cp.read_bytes()).hexdigest() if cp.is_file() else ABSENT
        look = active_look_for(world["project"], "character", CHAR)
        gen = FakeGen()
        _run(world, candidates=1, generate=gen, expectations=Expectations({cp.resolve(): digest}, look.look_hash))
        assert gen.n >= 1

    def test_cli_has_the_flags(self):
        import subprocess, sys
        out = subprocess.run([sys.executable, "scripts/headshot_run.py", "--help"], capture_output=True, text=True)
        assert "--expect-input-sha" in out.stdout and "--expect-look-hash" in out.stdout
```

Append to `tests/lib/test_sheet_run.py` (it already imports `io`, `pytest`, `sheet_run`, `CHAR`, `PALETTE`, `FakeGen`, `SeqJudge`, `_all`):

```python
def test_changed_visual_bible_refuses_before_any_generation(world):
    from lib.run_common import Expectations, InputChanged
    cp = world["project"] / "checkpoint_visual_bible.json"
    gen = FakeGen()
    judge = SeqJudge({"turnaround": [_all("turnaround")], "expressions": [_all("expressions")]})
    with pytest.raises(InputChanged, match="changed after it was approved"):
        sheet_run.run_sheet(world["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge,
                            out=io.StringIO(), expectations=Expectations({cp.resolve(): "0" * 64}))
    assert gen.n == 0


def test_changed_look_refuses_sheet_before_any_generation(world):
    from lib.run_common import Expectations, InputChanged
    gen = FakeGen()
    judge = SeqJudge({"turnaround": [_all("turnaround")], "expressions": [_all("expressions")]})
    with pytest.raises(InputChanged):
        sheet_run.run_sheet(world["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge,
                            out=io.StringIO(), expectations=Expectations(look_hash="0" * 64))
    assert gen.n == 0
```

Append to `tests/tools/test_supervised_production.py` (uses its `shot` fixture):

```python
def test_request_refuses_a_changed_brief(shot):
    from lib.run_common import InputChanged
    from lib.supervised_production import read_brief, request
    root, spec = shot
    rev = read_brief(root, spec['shot_id'])['revision_id']
    assert request(root, spec['shot_id'], prompt='p', output_path='renders/t-new.mp4', expect_revision=rev)['shot_id'] == spec['shot_id']
    with pytest.raises(InputChanged, match='changed after it was approved'):
        request(root, spec['shot_id'], prompt='p', output_path='renders/t-new.mp4', expect_revision='another-revision')
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_run_common_expect.py tests/lib/test_headshot_run.py tests/lib/test_sheet_run.py tests/tools/test_supervised_production.py -q`
Expected: FAIL (`ImportError: cannot import name 'ABSENT'` and `unexpected keyword argument 'expectations'` / `'expect_revision'`).

- [ ] **Step 3: Implement helpers in `lib/run_common.py`**

Add `import hashlib` to the imports at the top (`re` and `Path` are already imported), then append:

```python
# --- frozen inputs (Front Lot spend cards, spec §4.2) ---
ABSENT = "absent"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class InputChanged(RunError):
    """A frozen input changed between Ben's Go and its use; nothing was spent."""


def parse_expectations(values: Optional[list[str]]) -> dict[Path, str]:
    out: dict[Path, str] = {}
    for v in values or []:
        path, sep, digest = str(v).rpartition("=")
        if not sep or not path or not (digest == ABSENT or _SHA256_RE.match(digest)):
            raise ValueError(f"bad --expect-input-sha value: {v!r}")
        out[Path(path).resolve()] = digest
    return out


class Expectations:
    """What one paid run was approved against. Each frozen file is verified once,
    on the bytes actually loaded; the caller uses exactly those bytes."""

    def __init__(self, files: Optional[dict[Path, str]] = None, look_hash: Optional[str] = None):
        self.files = {Path(k).resolve(): v for k, v in (files or {}).items()}
        self.look_hash = look_hash
        self._verified: set[Path] = set()

    def read(self, path: Path | str) -> Optional[bytes]:
        p = Path(path)
        key = p.resolve()
        try:
            data: Optional[bytes] = p.read_bytes()
        except FileNotFoundError:
            data = None
        want = self.files.get(key)
        if want is not None and key not in self._verified:
            got = ABSENT if data is None else hashlib.sha256(data).hexdigest()
            if got != want:
                raise InputChanged(f"{p.name} changed after it was approved; nothing was spent")
            self._verified.add(key)
        return data

    def check_look(self, look_hash: str) -> None:
        if self.look_hash is not None and look_hash != self.look_hash:
            raise InputChanged("the look changed after it was approved; nothing was spent")
```

Then in `backlot/claude_ops.py` replace the local `ABSENT = "absent"` line with `from lib.run_common import ABSENT`.

- [ ] **Step 4: Wire into headshot_run.py**

1. Import: add `Expectations, InputChanged, parse_expectations` to the existing `from lib.run_common import (...)` block.
2. Below `MAX_CANDIDATES = 4` add `_EXPECT: Optional[Expectations] = None  # set by run_headshot at lease entry`.
3. Replace `_checkpoint` (:102) with:

```python
def _checkpoint(root: Path) -> dict[str, Any]:
    path = root / f"checkpoint_{STAGE}.json"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return {}
    try:
        raw = _EXPECT.read(path) if _EXPECT is not None else (path.read_bytes() if path.is_file() else None)
    except OSError as exc:
        raise HeadshotRunError(f"{path} is unreadable: {exc}") from exc
    if raw is None:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise HeadshotRunError(f"{path} is unreadable: {exc}") from exc
    return data if isinstance(data, dict) else {}
```

4. `run_headshot`: add keyword `expectations: Optional[Expectations] = None`; add `global _EXPECT` and `_EXPECT = None` as the first statements of the body; as the first two statements inside `with hold_lease(root, config):` (before `resume_check(root)`) add:

```python
        _EXPECT = expectations
        _checkpoint(root)  # frozen checkpoint verified now, under the lease, before anything paid
```

and directly after the `if look is None: raise ...` block add:

```python
        if expectations is not None:
            expectations.check_look(look.look_hash)
```

5. `main`: add

```python
    ap.add_argument("--expect-input-sha", action="append", default=[],
                    help="PATH=SHA256 (or PATH=absent) of an input that must be unchanged (set by Front Lot)")
    ap.add_argument("--expect-look-hash", help="the active look this run was approved against (set by Front Lot)")
```

and after `a = ap.parse_args(argv)`:

```python
    try:
        expectations = Expectations(parse_expectations(a.expect_input_sha), a.expect_look_hash)
    except ValueError as exc:
        ap.error(str(exc))
```

and pass `expectations=expectations` in the `run_headshot(...)` call. `InputChanged` is a `RunError`, so the existing `except RunError` prints `headshot_run: checkpoint_headshots.json changed after it was approved; nothing was spent` and returns 1.

- [ ] **Step 5: Wire into sheet_run.py the same way**

Same five edits: import; `_EXPECT` global below `GENERATION_PRICE_USD`; `_checkpoint` (:111) reads through `_EXPECT` (raising `SheetRunError` on OS/JSON errors); `run_sheet` gets `expectations: Optional[Expectations] = None`, resets `_EXPECT = None` first, and as the first statements inside `with run_lease.acquire(...):` sets `_EXPECT = expectations` and calls `_checkpoint(root)`; after `if look is None: raise SheetRunError(...)` (:265) add `if expectations is not None: expectations.check_look(look.look_hash)`; `main` gains both flags and passes `expectations=`.

- [ ] **Step 6: Brief revision for supervised shots**

In `lib/supervised_production.py`, change `request` to:

```python
def request(root, shot_id, *, prompt, output_path, expect_revision=None, **settings):
    """Build inputs with the exact saved references. No provider call occurs."""
    brief = read_brief(root, shot_id)
    if brief is None or brief['stopped']:
        raise ValueError('Shot is missing or stopped')
    if expect_revision is not None and brief['revision_id'] != expect_revision:
        from lib.run_common import InputChanged
        raise InputChanged('the shot brief changed after it was approved; nothing was spent')
    ...  # rest unchanged
```

In `scripts/supervised_shot.py`: in the `if command == 'generate':` parser block add `p.add_argument('--expect-brief-revision')`; in the request/generate branch, after `settings = json.loads(...)`:

```python
        if 'expect_revision' in settings:
            parser.error('settings may not set expect_revision')
        inputs = production.request(root, args.shot_id, expect_revision=getattr(args, 'expect_brief_revision', None), **settings)
```

(replacing the existing `inputs = production.request(root, args.shot_id, **settings)` line).

- [ ] **Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/test_run_common_expect.py tests/lib/test_headshot_run.py tests/lib/test_headshot_batch_override.py tests/lib/test_sheet_run.py tests/tools/test_supervised_production.py tests/integration/test_supervised_production_e2e.py tests/backlot/test_claude_ops.py -q`
Expected: all pass (7 + 4 + 2 + 1 new tests; every existing test in those files still passes).

- [ ] **Step 8: Commit**

```bash
git add lib/run_common.py lib/supervised_production.py scripts/headshot_run.py scripts/sheet_run.py scripts/supervised_shot.py backlot/claude_ops.py \
  tests/test_run_common_expect.py tests/lib/test_headshot_run.py tests/lib/test_sheet_run.py tests/tools/test_supervised_production.py
git commit -m "feat(pipeline): paid runs verify frozen inputs before spending"
```

---
### Task 5: Request store and state machine

**Files:**
- Create: `backlot/claude_requests.py`
- Test: `tests/backlot/test_claude_requests.py`

**Interfaces:**
- Consumes: `claude_ops.Prepared`, `claude_ops.input_digest`, `lib.run_lease.pid_is_running`, `lib.run_lease.process_start_time`
- Produces:
  - `class RequestStore(root: Path, film: str)` — `root` is `metadata_root()/"claude"` in the broker
    - `create(prep: Prepared, *, key: str, session: str, epoch: str) -> dict` (idempotent by `key`)
    - `get(request_id) -> dict | None`; `by_key(key) -> dict | None`; `all_requests() -> list[dict]`; `open_requests() -> list[dict]`
    - `decide(request_id, *, go: bool, session: str, epoch: str, controller: bool) -> dict` (raises `Rejected`)
    - `expire_stale() -> list[str]` (unanswered for 30 min → `expired`)
    - `cancel_unstarted(*, reason: str) -> list[str]` (only `waiting-for-ben`/`approved`)
    - `claim_for_launch(request_id) -> dict` (approved → `launching`, O_EXCL claim; raises `Rejected`)
    - `mark_running(request_id, pid: int, started: str | None) -> None` (only from `launching`, else `Rejected`)
    - `reconcile() -> list[str]` (outcome file → `done`/`failed`; claim without pid → `uncertain`; pid gone or start time changed without outcome → `uncertain`)
    - `outcome(request_id) -> dict | None` (reads the wrapper's outcome file)
    - attribute `spend_log: Path`
  - `class Rejected(RuntimeError)` with `.plain` (text for Ben)
  - States: `waiting-for-ben`, `approved`, `launching`, `running`, `done`, `failed`, `uncertain`, `declined`, `cancelled`, `expired`
  - Layout: `<root>/<film>/requests/<id>.json`, `<id>.lock`, `<id>.claim`, `<id>.outcome.json`, `<id>.log`; spend log `<root>/<film>.spend.jsonl` (spec §4.3).
  - Task 6 adds `launch()` (claim + spawn + pid in one lock).

- [ ] **Step 1: Write the failing tests**

```python
# tests/backlot/test_claude_requests.py
import json, os

import pytest

from backlot.claude_ops import Prepared
from backlot.claude_requests import Rejected, RequestStore
from lib.run_lease import process_start_time


def paid(tmp_path, body=b"v1"):
    f = tmp_path / "cp.json"; f.write_bytes(body)
    return Prepared("headshot_candidates", True, ["py", "x"], [f], summary="Make 3", entity="hero-a", estimate_usd=0.36)


@pytest.fixture
def store(tmp_path):
    return RequestStore(tmp_path / "meta", "film")


def approved(store, tmp_path, key="k"):
    r = store.create(paid(tmp_path), key=key, session="s", epoch="e")
    store.decide(r["id"], go=True, session="s", epoch="e", controller=True)
    return r


def test_create_is_idempotent_by_key(store, tmp_path):
    a = store.create(paid(tmp_path), key="k1", session="s", epoch="e")
    b = store.create(paid(tmp_path), key="k1", session="s", epoch="e")
    assert a["id"] == b["id"] and a["state"] == "waiting-for-ben"


def test_free_request_starts_approved(store, tmp_path):
    p = Prepared("look", False, ["py"], [], summary="Look")
    assert store.create(p, key="k", session="s", epoch="e")["state"] == "approved"


def test_only_one_decision_and_only_from_controller(store, tmp_path):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    with pytest.raises(Rejected):
        store.decide(r["id"], go=True, session="s", epoch="e", controller=False)
    assert store.decide(r["id"], go=True, session="s", epoch="e", controller=True)["state"] == "approved"
    with pytest.raises(Rejected) as err:
        store.decide(r["id"], go=False, session="s", epoch="e", controller=True)
    assert err.value.plain == "That card was already answered."


def test_stale_session_or_epoch_rejected(store, tmp_path):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    with pytest.raises(Rejected):
        store.decide(r["id"], go=True, session="s2", epoch="e", controller=True)
    with pytest.raises(Rejected):
        store.decide(r["id"], go=True, session="s", epoch="e2", controller=True)


def test_changed_input_refuses_go(store, tmp_path):
    p = paid(tmp_path)
    r = store.create(p, key="k", session="s", epoch="e")
    p.inputs[0].write_bytes(b"v2")
    with pytest.raises(Rejected) as err:
        store.decide(r["id"], go=True, session="s", epoch="e", controller=True)
    assert "changed" in err.value.plain
    assert store.get(r["id"])["state"] == "cancelled"


def test_claim_happens_once(store, tmp_path):
    r = approved(store, tmp_path)
    assert store.claim_for_launch(r["id"])["state"] == "launching"
    with pytest.raises(Rejected):
        store.claim_for_launch(r["id"])


def test_cancel_never_touches_launching_or_running(store, tmp_path):
    a = store.create(paid(tmp_path), key="a", session="s", epoch="e")
    b = approved(store, tmp_path, key="b")
    store.claim_for_launch(b["id"])
    assert store.cancel_unstarted(reason="stop") == [a["id"]]
    assert store.get(b["id"])["state"] == "launching"


def test_cancel_before_claim_wins_and_the_claim_is_then_refused(store, tmp_path):
    r = approved(store, tmp_path)
    assert store.cancel_unstarted(reason="end") == [r["id"]]
    with pytest.raises(Rejected):
        store.claim_for_launch(r["id"])
    assert not (store.dir / f"{r['id']}.claim").exists()


def test_reconcile_marks_unknown_launches_uncertain(store, tmp_path):
    r = approved(store, tmp_path)
    store.claim_for_launch(r["id"])              # crash before pid recorded
    assert store.reconcile() == [r["id"]]
    assert store.get(r["id"])["state"] == "uncertain"


def test_reconcile_dead_pid_without_outcome_is_uncertain_with_outcome_is_done(store, tmp_path):
    r = approved(store, tmp_path)
    store.claim_for_launch(r["id"])
    store.mark_running(r["id"], pid=999_999, started="x")  # not a live pid
    store.reconcile()
    assert store.get(r["id"])["state"] == "uncertain"
    r2 = approved(store, tmp_path, key="k2")
    store.claim_for_launch(r2["id"]); store.mark_running(r2["id"], pid=999_998, started="x")
    (store.dir / f"{r2['id']}.outcome.json").write_text(json.dumps({"exit": 0}))
    store.reconcile()
    assert store.get(r2["id"])["state"] == "done"


def test_reconcile_treats_a_reused_pid_as_gone(store, tmp_path):
    r = approved(store, tmp_path)
    store.claim_for_launch(r["id"])
    store.mark_running(r["id"], pid=os.getpid(), started="started at another time")  # live pid, other process
    store.reconcile()
    assert store.get(r["id"])["state"] == "uncertain"
    r2 = approved(store, tmp_path, key="k2")
    store.claim_for_launch(r2["id"])
    store.mark_running(r2["id"], pid=os.getpid(), started=process_start_time(os.getpid()))
    store.reconcile()
    assert store.get(r2["id"])["state"] == "running"


def test_mark_running_requires_launching(store, tmp_path):
    r = approved(store, tmp_path)
    with pytest.raises(Rejected):
        store.mark_running(r["id"], pid=1, started=None)


def test_every_transition_is_in_the_spend_log(store, tmp_path):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    store.decide(r["id"], go=False, session="s", epoch="e", controller=True)
    assert store.spend_log == tmp_path / "meta" / "film.spend.jsonl"
    lines = [json.loads(x) for x in store.spend_log.read_text().splitlines()]
    assert [l["state"] for l in lines] == ["waiting-for-ben", "declined"]
    assert all(l["argv_sha256"] and l["request"] == r["id"] and l["estimate_usd"] == 0.36 for l in lines)


def test_expiry_on_decide(store, tmp_path, monkeypatch):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    monkeypatch.setattr("backlot.claude_requests.EXPIRY_SECONDS", 0)
    with pytest.raises(Rejected):
        store.decide(r["id"], go=True, session="s", epoch="e", controller=True)
    assert store.get(r["id"])["state"] == "expired"


def test_expire_stale_without_any_click(store, tmp_path, monkeypatch):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    f = store.create(Prepared("look", False, ["py"], [], summary="Look"), key="f", session="s", epoch="e")
    monkeypatch.setattr("backlot.claude_requests.EXPIRY_SECONDS", 0)
    assert store.expire_stale() == [r["id"]]
    assert store.get(r["id"])["state"] == "expired" and store.get(f["id"])["state"] == "approved"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_requests.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

```python
# backlot/claude_requests.py
"""Run requests from Front Lot's embedded Claude, and Ben's decisions on them.

One JSON state file per request, guarded by a per-request flock. Launch and
cancel take the same lock, so they cannot race (spec §4.3). A request is
claimed for launch with O_EXCL exactly once. Nothing is retried automatically;
anything whose fate is unknown becomes `uncertain`.
"""
from __future__ import annotations

import fcntl, hashlib, json, os, time, uuid
from contextlib import contextmanager
from pathlib import Path

from backlot.claude_ops import Prepared, input_digest
from lib.run_lease import pid_is_running, process_start_time

EXPIRY_SECONDS = 30 * 60
UNSTARTED = {"waiting-for-ben", "approved"}
FINAL = {"done", "failed", "declined", "cancelled", "expired"}


class Rejected(RuntimeError):
    def __init__(self, plain: str):
        super().__init__(plain)
        self.plain = plain


def _same_process(pid: int, started: str | None) -> bool:
    if not pid_is_running(pid):
        return False
    now = process_start_time(pid)
    return started is None or now is None or now == started  # a different start time = a reused pid


class RequestStore:
    def __init__(self, root: Path, film: str):
        self.base = Path(root) / film
        self.dir = self.base / "requests"
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.spend_log = Path(root) / f"{film}.spend.jsonl"
        self._children: dict[str, object] = {}  # wrappers this process started (Task 6), reaped in reconcile

    # -- files --------------------------------------------------------------
    def _path(self, rid: str) -> Path:
        return self.dir / f"{rid}.json"

    @contextmanager
    def _locked(self, rid: str):
        with open(self.dir / f"{rid}.lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _write(self, rec: dict) -> None:
        tmp = self._path(rec["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, sort_keys=True))
        os.replace(tmp, self._path(rec["id"]))
        line = {"at": time.time(), "request": rec["id"], "state": rec["state"], "op": rec["op"], "paid": rec["paid"],
                "summary": rec["summary"], "entity": rec.get("entity"), "estimate_usd": rec.get("estimate_usd"),
                "argv_sha256": rec["argv_sha256"], "session": rec["session"], "note": rec.get("note"),
                "exit": (rec.get("result") or {}).get("exit")}
        with open(self.spend_log, "a") as fh:
            fh.write(json.dumps(line) + "\n")

    def get(self, rid: str) -> dict | None:
        p = self._path(rid)
        return json.loads(p.read_text()) if p.exists() else None

    def all_requests(self) -> list[dict]:
        recs = [json.loads(p.read_text()) for p in self.dir.glob("r-*.json") if not p.name.endswith(".outcome.json")]
        return sorted(recs, key=lambda r: r["created"])

    def by_key(self, key: str) -> dict | None:
        return next((r for r in self.all_requests() if r.get("key") == key), None)

    def open_requests(self) -> list[dict]:
        return [r for r in self.all_requests() if r["state"] not in FINAL]

    # -- lifecycle ----------------------------------------------------------
    def create(self, prep: Prepared, *, key: str, session: str, epoch: str) -> dict:
        existing = self.by_key(key)
        if existing:
            return existing
        rid = "r-" + uuid.uuid4().hex[:10]
        rec = {"id": rid, "key": key, "op": prep.op, "paid": prep.paid, "argv": prep.argv,
               "argv_sha256": hashlib.sha256(json.dumps(prep.argv).encode()).hexdigest(),
               "inputs": {str(p): input_digest(p) for p in prep.inputs},
               "summary": prep.summary, "entity": prep.entity, "estimate_usd": prep.estimate_usd,
               "session": session, "epoch": epoch, "created": time.time(),
               "state": "waiting-for-ben" if prep.paid else "approved"}
        with self._locked(rid):
            self._write(rec)
        return rec

    def decide(self, rid: str, *, go: bool, session: str, epoch: str, controller: bool) -> dict:
        if not controller:
            raise Rejected("Only the window in control can answer. Use Take control first.")
        with self._locked(rid):
            rec = self.get(rid)
            if rec is None:
                raise Rejected("That request is gone.")
            if rec["state"] == "waiting-for-ben" and time.time() - rec["created"] > EXPIRY_SECONDS:
                rec["state"] = "expired"; self._write(rec)
                raise Rejected("That card expired. Ask Claude again if you still want it.")
            if rec["state"] != "waiting-for-ben":
                raise Rejected("That card was already answered.")
            if rec["session"] != session or rec["epoch"] != epoch:
                raise Rejected("That card belongs to an earlier conversation and can't run now.")
            if go:
                for path, digest in rec["inputs"].items():
                    if input_digest(Path(path)) != digest:
                        rec["state"] = "cancelled"; rec["note"] = "input changed"; self._write(rec)
                        raise Rejected("Something this run depends on changed since the card appeared. Ask Claude to set it up again.")
            rec["state"] = "approved" if go else "declined"
            rec["decided"] = time.time()
            self._write(rec)
            return rec

    def expire_stale(self) -> list[str]:
        done = []
        for rec in self.open_requests():
            if rec["state"] != "waiting-for-ben":
                continue
            with self._locked(rec["id"]):
                cur = self.get(rec["id"])
                if cur and cur["state"] == "waiting-for-ben" and time.time() - cur["created"] > EXPIRY_SECONDS:
                    cur["state"] = "expired"; self._write(cur); done.append(cur["id"])
        return done

    def cancel_unstarted(self, *, reason: str) -> list[str]:
        done = []
        for rec in self.open_requests():
            with self._locked(rec["id"]):
                cur = self.get(rec["id"])
                if cur and cur["state"] in UNSTARTED:
                    cur["state"] = "cancelled"; cur["note"] = reason; self._write(cur)
                    done.append(cur["id"])
        return done

    def _claim(self, rid: str) -> dict:
        """Caller holds the request lock."""
        rec = self.get(rid)
        if rec is None or rec["state"] != "approved":
            raise Rejected("This run can't start now.")
        try:
            fd = os.open(self.dir / f"{rid}.claim", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            raise Rejected("This run already started.") from None
        rec["state"] = "launching"; rec["claimed"] = time.time()
        self._write(rec)
        return rec

    def claim_for_launch(self, rid: str) -> dict:
        with self._locked(rid):
            return self._claim(rid)

    def mark_running(self, rid: str, pid: int, started: str | None) -> None:
        with self._locked(rid):
            rec = self.get(rid)
            if rec is None or rec["state"] != "launching":
                raise Rejected("This run isn't starting.")
            rec["state"] = "running"; rec["pid"] = pid; rec["pid_started"] = started
            self._write(rec)

    def outcome(self, rid: str) -> dict | None:
        p = self.dir / f"{rid}.outcome.json"
        return json.loads(p.read_text()) if p.exists() else None

    def reconcile(self) -> list[str]:
        for rid, proc in list(self._children.items()):
            if proc.poll() is not None:      # reap our own wrappers so a finished pid is really gone
                self._children.pop(rid, None)
        changed = []
        for rec in self.open_requests():
            with self._locked(rec["id"]):
                cur = self.get(rec["id"])
                out = self.outcome(cur["id"])
                if cur["state"] in ("launching", "running") and out is not None:
                    cur["state"] = "done" if out.get("exit") == 0 else "failed"
                    cur["result"] = out; self._write(cur); changed.append(cur["id"])
                elif cur["state"] == "launching" and "pid" not in cur:
                    cur["state"] = "uncertain"; cur["note"] = "may not have started"
                    self._write(cur); changed.append(cur["id"])
                elif cur["state"] == "running" and not _same_process(cur["pid"], cur.get("pid_started")):
                    cur["state"] = "uncertain"; cur["note"] = "finished without a result"
                    self._write(cur); changed.append(cur["id"])
        return changed
```

Note: `reconcile` must not run while a `launch` of the same request is between claim and pid (Task 6 holds the request lock for that whole section, so it cannot).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_requests.py -v`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add backlot/claude_requests.py tests/backlot/test_claude_requests.py
git commit -m "feat(front-lot): run request store with exactly-once claim, expiry, and spend log"
```

---

### Task 6: Detached run wrapper and launcher (then probe P4)

**Files:**
- Create: `scripts/frontlot_run.py`
- Modify: `backlot/claude_requests.py` (add `launch`)
- Test: `tests/backlot/test_frontlot_run.py`

**Interfaces:**
- Consumes: `RequestStore._locked`, `_claim`, `_write`, `reconcile`, `outcome`; `lib.run_lease.process_start_time`
- Produces:
  - `RequestStore.launch(rid: str, *, repo: Path, env: dict[str, str], _between: Callable[[str], None] | None = None) -> int` (pid). In **one** request-lock section: require `approved`, O_EXCL claim, write `launching`, spawn `scripts/frontlot_run.py --store <base> --request <rid> --repo <repo>` detached (`start_new_session=True`, stdio DEVNULL), record pid, `process_start_time(pid)` and `running`. `_between(stage)` is a test seam called inside the lock at `"claimed"` and `"spawned"`. A spawn failure records `failed` (it certainly did not run) and raises `Rejected`.
  - Wrapper CLI: `scripts/frontlot_run.py --store <base> --request <rid> --repo <repo>`; outcome file `<rid>.outcome.json`: `{"exit": int, "finished": float, "tail": str}` (last 4 KB of output, for Claude's summary); full output in `<rid>.log`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/backlot/test_frontlot_run.py
import json, os, sys, threading, time
from pathlib import Path

import pytest

from backlot.claude_ops import Prepared
from backlot.claude_requests import Rejected, RequestStore

REPO = Path(__file__).resolve().parents[2]


def wait_state(store, rid, want, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        store.reconcile()
        if store.get(rid)["state"] == want:
            return
        time.sleep(0.1)
    raise AssertionError(store.get(rid))


def approved_paid(store, tmp_path, code="print('made 1')"):
    p = Prepared("headshot_candidates", True, [sys.executable, "-c", code], [], summary="Make 1", entity="hero-a")
    r = store.create(p, key="k", session="s", epoch="e")
    store.decide(r["id"], go=True, session="s", epoch="e", controller=True)
    return r


def test_free_request_runs_once_and_records_outcome(tmp_path):
    store = RequestStore(tmp_path / "meta", "film")
    p = Prepared("look", False, [sys.executable, "-c", "print('hello')"], [], summary="Look")
    r = store.create(p, key="k", session="s", epoch="e")
    pid = store.launch(r["id"], repo=REPO, env=dict(os.environ))
    assert pid > 0 and store.get(r["id"])["pid_started"]
    wait_state(store, r["id"], "done")
    assert "hello" in store.outcome(r["id"])["tail"]
    with pytest.raises(Rejected):
        store.launch(r["id"], repo=REPO, env=dict(os.environ))  # never twice


def test_failed_command_is_failed_not_done(tmp_path):
    store = RequestStore(tmp_path / "meta", "film")
    p = Prepared("look", False, [sys.executable, "-c", "raise SystemExit(3)"], [], summary="Look")
    r = store.create(p, key="k", session="s", epoch="e")
    store.launch(r["id"], repo=REPO, env=dict(os.environ))
    wait_state(store, r["id"], "failed")


@pytest.mark.parametrize("reason", ["stop", "end", "addon-reload"])
def test_cancel_before_launch_wins(tmp_path, reason):
    store = RequestStore(tmp_path / "meta", "film")
    r = approved_paid(store, tmp_path)
    assert store.cancel_unstarted(reason=reason) == [r["id"]]
    with pytest.raises(Rejected):
        store.launch(r["id"], repo=REPO, env=dict(os.environ))
    assert not (store.dir / f"{r['id']}.claim").exists() and "pid" not in store.get(r["id"])


@pytest.mark.parametrize("reason", ["stop", "end", "addon-reload"])
@pytest.mark.parametrize("stage", ["claimed", "spawned"])
def test_cancel_during_launch_waits_for_the_lock_and_cannot_win(tmp_path, reason, stage):
    store = RequestStore(tmp_path / "meta", "film")
    r = approved_paid(store, tmp_path)
    seen = {}

    def between(name):
        if name != stage:
            return
        t = threading.Thread(target=lambda: seen.setdefault("cancelled", store.cancel_unstarted(reason=reason)))
        t.start(); seen["thread"] = t
        time.sleep(0.3)
        seen["blocked"] = t.is_alive()  # the cancel is waiting on this request's lock

    store.launch(r["id"], repo=REPO, env=dict(os.environ), _between=between)
    seen["thread"].join(10)
    assert seen["blocked"] is True
    assert seen["cancelled"] == []
    assert store.get(r["id"])["state"] in ("running", "done")
    wait_state(store, r["id"], "done")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/backlot/test_frontlot_run.py -v`
Expected: FAIL with `AttributeError: 'RequestStore' object has no attribute 'launch'`.

- [ ] **Step 3: Implement the wrapper**

```python
# scripts/frontlot_run.py
"""Run one claimed Front Lot request and record its outcome. Detached; never retries."""
from __future__ import annotations

import argparse, json, subprocess, sys, time
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--request", required=True)
    ap.add_argument("--repo", required=True, type=Path)
    a = ap.parse_args(argv)
    req_dir = a.store / "requests"
    rec = json.loads((req_dir / f"{a.request}.json").read_text())
    if rec["state"] not in ("launching", "running"):
        return 2
    log = req_dir / f"{a.request}.log"
    with open(log, "wb") as fh:
        code = subprocess.run(rec["argv"], cwd=a.repo, stdout=fh, stderr=subprocess.STDOUT).returncode
    tail = log.read_bytes()[-4096:].decode("utf-8", "replace")
    out = req_dir / f"{a.request}.outcome.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"exit": code, "finished": time.time(), "tail": tail}))
    tmp.replace(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Implement `launch` in `RequestStore`**

```python
    # --- add to RequestStore ---
    def launch(self, rid: str, *, repo: Path, env: dict[str, str], _between=None) -> int:
        """Claim, spawn, and record the pid in one locked section (spec §4.3), so a
        Stop/End/reload cancel either wins before the claim or waits and loses."""
        import subprocess, sys
        step = _between or (lambda _stage: None)
        with self._locked(rid):
            rec = self._claim(rid)                      # approved -> launching, O_EXCL
            step("claimed")
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(Path(repo) / "scripts" / "frontlot_run.py"),
                     "--store", str(self.base), "--request", rid, "--repo", str(repo)],
                    cwd=repo, env=env, start_new_session=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                rec["state"] = "failed"; rec["note"] = f"could not start: {exc}"; self._write(rec)
                raise Rejected("Front Lot couldn't start this run.") from None
            step("spawned")
            self._children[rid] = proc
            rec["state"] = "running"; rec["pid"] = proc.pid
            rec["pid_started"] = process_start_time(proc.pid); rec["started"] = time.time()
            self._write(rec)
            return proc.pid
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/backlot/test_frontlot_run.py tests/backlot/test_claude_requests.py -v`
Expected: 26 passed (11 + 15).

- [ ] **Step 6: Probe P4 (append results to the PROBES file)**

In a Python shell (`.venv/bin/python`), with a scratch store under a `mkdtemp` dir:
1. `claude_ops.prepare("look", {"entity": "hero-a", "project": "other"}, repo=..., film_slug="film", film_root=<tmp film>, snapshot_dir=<tmp>)` → `OpError` (Claude cannot name another film).
2. Create a paid `Prepared` with one input file (as in the Task 5 tests), change the file, `decide(go=True)` → `Rejected` whose `.plain` mentions "changed".
3. Create, decide Go, then start two processes together that each call `RequestStore(...).claim_for_launch(rid)` (`python -c` × 2 with `&`) → exactly one prints success, the other `Rejected`.
4. Frozen input at the script: run `.venv/bin/python -m pytest tests/lib/test_headshot_run.py tests/lib/test_sheet_run.py tests/tools/test_supervised_production.py -k "frozen or changed or refuses_a_changed" -v` → PASS only if every listed test passes; each asserts the "changed after it was approved" message and (for the two scripts) that the fake generator was never called. (A CLI run on a scratch film cannot reach this check: both scripts load a signed project config before taking the lease.) Also confirm `~/.openmontage/gates/generation-ledger.jsonl` is unchanged.
Record PASS/FAIL per item. Any FAIL stops the build.

- [ ] **Step 7: Commit**

```bash
git add scripts/frontlot_run.py backlot/claude_requests.py tests/backlot/test_frontlot_run.py docs/superpowers/specs/*PROBES.md
git commit -m "feat(front-lot): one-lock launch and detached run wrapper; probe P4"
```

---

### Task 7: Add-on `frontlot_run` tool

**Files:**
- Create: `backlot/claude_mod/hooks/run.ts` (pure: no `claude-code` import)
- Modify: `backlot/claude_mod/hooks/register.ts`, `backlot/claude_mod/hooks/protocol.ts`
- Test: `backlot/claude_mod/tests/run.test.ts` (new), `backlot/claude_mod/tests/register.test.ts` (append), run with `claude plugin test backlot/claude_mod` (the runner Story-drive's mod uses; tests import from `claude-code/testing`)

**Interfaces:**
- Consumes: broker routes from Task 8: `POST /run {key, op, params}` and `POST /run-check {key}` → `{requestId?, status, plain}` with `status` one of `running | waiting-for-ben | refused | not-received | unknown-outcome | done | failed | declined | cancelled | expired`.
- Produces: tool `mcp__frontlot-live__frontlot_run` with input `{op?: string, params?: object, check?: string}`; the idempotency key is the call's `tool_use_id` (the field `register.ts`'s `tool.call` handler already reads, `String((e as any).tool_use_id)`); add-on-only status `uncertain` = no broker answer within 5 s.

- [ ] **Step 1: Add protocol types**

```ts
// backlot/claude_mod/hooks/protocol.ts (append)
export const RUN_TOOL = 'mcp__frontlot-live__frontlot_run'
export interface RunRequest { key: string; op: string; params: Record<string, unknown> }
export interface RunCheck { key: string }
export type RunStatus =
  | 'running' | 'waiting-for-ben' | 'refused' | 'not-received' | 'unknown-outcome'
  | 'done' | 'failed' | 'declined' | 'cancelled' | 'expired'
  | 'uncertain'   // add-on only: the broker did not answer within 5 s
export interface RunReply { requestId?: string; status: RunStatus; plain: string }
```

- [ ] **Step 2: Write the failing tests**

```ts
// backlot/claude_mod/tests/run.test.ts
import { expect, test } from 'claude-code/testing'
import { runCall, runResultText } from '../hooks/run'

test('uncertain tells Claude to check by key, never resubmit', () => {
  const t = runResultText({ status: 'uncertain', plain: 'no answer' }, 'tu-1')
  expect(t).toMatch(/check/i)
  expect(t).toMatch(/tu-1/)
  expect(t).toMatch(/do not resubmit/i)
})

test('not-received allows one new submission; unknown outcome is never retried', () => {
  expect(runResultText({ status: 'not-received', plain: '' }, 'k')).toMatch(/once more/i)
  expect(runResultText({ status: 'unknown-outcome', plain: 'Make 3' }, 'k')).toMatch(/do not retry/i)
})

test('waiting tells Claude Ben sees a card', () => {
  expect(runResultText({ requestId: 'r-1', status: 'waiting-for-ben', plain: 'Make 3' }, 'k')).toMatch(/spend card/i)
})

test('the key is the tool-use id; a check uses the key it was given', () => {
  expect(runCall({ tool_use_id: 'tu-9', op: 'look', params: { entity: 'hero-a' } }))
    .toEqual({ route: '/run', key: 'tu-9', body: { key: 'tu-9', op: 'look', params: { entity: 'hero-a' } } })
  expect(runCall({ tool_use_id: 'tu-10', check: 'tu-9' }))
    .toEqual({ route: '/run-check', key: 'tu-9', body: { key: 'tu-9' } })
})
```

Append to `backlot/claude_mod/tests/register.test.ts` (reuses its `wire`, `launch`, `settle`):

```ts
test('frontlot_run posts /run keyed by the tool-use id and never reaches the engine', async ($, on) => {
  const clock = mock.clock(on)
  const reqs = wire(on)
  let reachedEngine = false
  on('tool.call', () => { reachedEngine = true; return { result: 'engine' } })
  await launch($)
  await settle(clock)
  const r: any = await $.tool.call({ tool: 'mcp__frontlot-live__frontlot_run', tool_use_id: 'tu-7', op: 'look', params: { entity: 'hero-a' } } as any)
  expect(reqs.find((q) => q.route === '/run')!.body).toEqual({ key: 'tu-7', op: 'look', params: { entity: 'hero-a' } })
  expect(reachedEngine).toBe(false)
  expect(typeof r.result).toBe('string')
})
```

- [ ] **Step 3: Run to verify failure**

Run: `claude plugin test backlot/claude_mod`
Expected: FAIL (`../hooks/run` not found; the register test posts nothing to `/run`).

- [ ] **Step 4: Implement**

```ts
// backlot/claude_mod/hooks/run.ts
// Pure helpers for the frontlot_run tool (no claude-code import, so tests stay plain).
import type { RunCheck, RunReply, RunRequest } from './protocol'

export const RUN_DESCRIPTION = 'Ask Front Lot to run a pipeline step for this film. Give {op, params}. Free steps run now; paid steps show Ben a spend card and wait. If the reply says Front Lot did not confirm receipt, call again with {check: "<key>"}; never resubmit.'

const FINISHED: Record<string, string> = {
  done: 'It finished', failed: 'It failed', declined: 'Ben said Not now',
  cancelled: 'It was cancelled before it started', expired: 'The card expired unanswered',
}

export function runCall(e: Record<string, unknown>): { route: '/run' | '/run-check'; key: string; body: RunRequest | RunCheck } {
  if (typeof e.check === 'string' && e.check) return { route: '/run-check', key: e.check, body: { key: e.check } }
  const key = String(e.tool_use_id ?? '')
  const params = e.params && typeof e.params === 'object' ? (e.params as Record<string, unknown>) : {}
  return { route: '/run', key, body: { key, op: String(e.op ?? ''), params } }
}

export function runResultText(r: RunReply, key: string): string {
  switch (r.status) {
    case 'running': return `Front Lot is running it. You'll get a message when it finishes. ${r.plain}`
    case 'waiting-for-ben': return `Ben now sees a spend card for: ${r.plain}. Wait for his answer; you'll get a message.`
    case 'refused': return `Front Lot refused this: ${r.plain}`
    case 'uncertain': return `Front Lot didn't confirm it received this. Check with frontlot_run {"check": "${key}"} before doing anything else. Do not resubmit.`
    case 'not-received': return 'Front Lot never received that request. You may submit it once more as a new call.'
    case 'unknown-outcome': return `Front Lot can't tell whether this ran (${r.plain}). Tell Ben plainly and do not retry it.`
    default: return `${FINISHED[r.status] ?? r.status}: ${r.plain}`
  }
}
```

In `register.ts`:
1. Imports: add `RUN_TOOL, type RunReply` to the `./protocol` import, and `import { RUN_DESCRIPTION, runCall, runResultText } from './run'`.
2. Add a top-level function next to `registerMark` (validate rule: helpers that receive `$` are top-level `function` declarations):

```ts
async function registerRun($: any): Promise<void> {
  await $.tool.register({
    name: 'frontlot_run',
    description: RUN_DESCRIPTION,
    inputSchema: { type: 'object', properties: { op: { type: 'string' }, params: { type: 'object' }, check: { type: 'string' } } },
  })
}
```

3. In `session.start`, after the `registerMark` try/catch: `try { await registerRun($) } catch { /* no pipeline steps this session; Claude tells Ben */ }`.
4. Replace the existing literal `on('tool.describe', { tool: 'mcp__frontlot-live__mark' }, …)` with exactly one unmatched handler (validate rule: one literal `on('<event>')` per event):

```ts
  on('tool.describe', async ($, e, next) => {
    if (e.tool === MARK_TOOL) return { description: MARK_DESCRIPTION, isDeferred: false }
    if (e.tool === RUN_TOOL) return { description: RUN_DESCRIPTION, isDeferred: false }
    return next(e)
  }).catch(($, e, next) => next(e))
```

5. In the `tool.call` handler, before the `MARK_TOOL` branch:

```ts
    if (e.tool === RUN_TOOL) {
      const { route, key, body } = runCall(e as any)
      let reply: RunReply
      try {
        reply = await Promise.race([
          call($, route, body) as Promise<RunReply>,
          $.clock.sleep(5000).then((): RunReply => ({ status: 'uncertain', plain: 'no answer in 5 s' })),
        ])
      } catch {
        reply = { status: 'uncertain', plain: 'Front Lot did not answer' }
      }
      return { result: runResultText(reply, key) }
    }
```

(The 5 s race keeps the handler inside the 10-second hook budget; `$.clock.sleep` is the budget-exempt wait. A late broker answer is not lost: the request is durable, and Claude's `{check}` call finds it.)

- [ ] **Step 5: Run tests and validate**

Run: `claude plugin test backlot/claude_mod && claude plugin validate --strict backlot/claude_mod`
Expected: every suite passes (copied ones plus the five new tests); validate clean.

- [ ] **Step 6: Commit**

```bash
git add backlot/claude_mod
git commit -m "feat(front-lot): frontlot_run tool in the vendored live add-on"
```

---
### Task 8: Session broker

**Files:**
- Create: `backlot/claude_frames.py` (broker↔server framing), `backlot/claude_journal.py` (bounded event journal with snapshot), `scripts/claude_session.py` (broker), `tests/backlot/claude_fakes.py` (test helpers reused by Tasks 9, 11, 12)
- Test: `tests/backlot/test_claude_frames.py`, `tests/backlot/test_claude_journal.py`, `tests/backlot/test_claude_session.py`

**Interfaces (the single definition of the broker and page protocols):**
- Consumes: Task 1 (`claude_settings.*`), Task 3 (`claude_ops.prepare`, `OpError`, `warm_estimates`), Tasks 5/6 (`RequestStore`, `Rejected`), `backlot.tty.metadata_root`, `gate_sign._login_environment`, `gate_sign._safe_replay_tail`, `lib.paths.PROJECTS_DIR`, `lib.run_lease.process_start_time`. It does **not** use `backlot.tty`'s framing (its JSON limit is 4 KB and it rejects types 7/8; the signing socket keeps those limits).
- `backlot/claude_frames.py`: header `!BI` (type, length) like `tty.py`; types `HELLO=1, IN=2, OUT=3, RESIZE=4, STATUS=5, BYE=6, EVENT=7, ACTION=8`; limits IN 4096, OUT 65536, JSON types 256 KiB; `encode(type, bytes)`, `encode_json(type, dict)`, `decode_json(type, bytes)`, `async read_frame(reader)`, `FrameError(ValueError)`.
- `backlot/claude_journal.py`: `Journal(path, keep=2000)`; `append(event) -> int` (seq); `since(seq, snapshot: Callable[[], dict]) -> tuple[list[dict], dict | None]` — events after `seq`, or `([], {**snapshot(), "kind": "snapshot", "cursor": last_seq})` when `seq` is older than retained or newer than the journal.
- Broker CLI: `scripts/claude_session.py --broker --project <slug> [--resume]`; exits 0 after a normal shutdown, 3 when unavailable (after writing the unavailable file).
- Broker paths, `metadata_root()/"claude"/<slug>` + suffix: `.sock` (client socket), `.lock` (held by the broker for its whole life), `.spawn.lock` (servers only), `.json` (identity), `.live.sock`, `.events.jsonl`, `.settings.json`, `.brief.md`, `.session` (`{"session_id"}`, kept after exit for Pick back up), `.unavailable.json` (`{"reason": "missing"|"old-version"|"signed-out"|"sandbox", "version"?}`), `.sandbox-ok.json` (`{"version", "settings_sha256"}`), `.canary` (self-check only), `.spend.jsonl` (Task 5); request files under `<slug>/requests/`, snapshots under `<slug>/snap/<key>/`.
- Identity `<slug>.json` = `{session_id, broker_pid, broker_started, claude_pid, claude_pgid, claude_started, boot_time, claude_path, claude_version}`; `*_started` from `proc_started()` (seconds since epoch, `ps -o lstart=`).
- Client socket frames:
  - client → broker `HELLO {"subscribe_from": int, "controller": bool, "page": str}`, then `IN`/`RESIZE` (controller only; others ignored) and `ACTION {type: "submit", text} | {type: "stop"} | {type: "spend-decision", requestId, go: bool} | {type: "take-control"} | {type: "end"}`.
  - broker → client `STATUS {"controller": bool, "state": "starting"|"ready"|"working"|"ended", "session_id": str}` (sent on attach and whenever either field changes), `OUT` (PTY bytes; replay tail first), `EVENT {"seq": int|null, "event": {...}}` (`seq` null = this client only, not journaled), `BYE {"reason"}`.
- Event kinds (journaled unless noted):
  - add-on events as Story-drive reports them (`row`, `delta`, `turn`, `tool`, `mark`, `waiting-for-input`, `input-done`, `session-end`, `channel-error`, `queue-overflow`), each with `"epoch": <add-on epoch>` added;
  - `addon-hello {hello}` (the add-on's `/hello` body); `addon-missing {}` (no hello 8 s after Claude started); `addon-silent {}` (no `/report` or `/ping` for 15 s after a hello); `addon-back {}`;
  - `spend-request {requestId, summary, entity, estimate_usd, paid: true, state: "waiting-for-ben"}`;
  - `spend-decided {requestId, state: "approved"|"declined"|"cancelled"|"expired", reason?}`;
  - `run-started {requestId, summary, entity, paid, state: "running"}`;
  - `run-finished {requestId, summary, entity, paid, state: "done"|"failed"|"uncertain"}`;
  - `session-state {state, reason?}`;
  - `notice {plain}` (client-only, `seq: null`);
  - `snapshot {state, hello, rows, cards, cursor}` (sent instead of events on a gap): `hello` = last add-on hello or null, `rows` = the last 50 add-on events of kind `row` (with `epoch`), `cards` = every request of this conversation as `{requestId, summary, entity, estimate_usd, paid, state}` (record states, `approved`/`launching` shown as `running`).
- Live endpoint `<slug>.live.sock` (0600; header `x-frontlot-token`): Story-drive routes `/hello`, `/report`, `/ping`, `/inbox`, `/inbox-ack`, plus `/run {key, op, params}` and `/run-check {key}` → `{requestId?, status, plain}` with record states mapped: `waiting-for-ben`→`waiting-for-ben`; `approved`/`launching`/`running`→`running`; `uncertain`→`unknown-outcome`; terminal states unchanged; unknown key on `/run-check` → `not-received`; refused → `refused`.
- Controller lease: one controlling client per film. HELLO with `controller: true` is granted when no page holds the lease, when the same `page` holds it, or when the holder has had no live socket for `LEASE_SECONDS` (30; `FRONTLOT_LEASE_SECONDS` overrides in tests). `take-control` always transfers it (the previous holder gets `STATUS controller:false` and a notice). Every mutating `ACTION` from a non-controller gets the notice "This window is read-only. Use Take control to act here."
- Request binding: requests are created with `session = session_id`, `epoch = current add-on epoch`; decisions are checked against the same pair.

- [ ] **Step 1: Framing (tests, then code)**

```python
# tests/backlot/test_claude_frames.py
import asyncio

import pytest

from backlot import claude_frames as cf, tty


def test_event_frames_carry_large_json():
    payload = {"seq": 1, "event": {"kind": "row", "text": "x" * 100_000}}
    raw = cf.encode_json(cf.EVENT, payload)
    t, body = raw[0], raw[5:]
    assert t == cf.EVENT and cf.decode_json(cf.EVENT, body) == payload


def test_limits_and_unknown_types():
    with pytest.raises(cf.FrameError):
        cf.encode_json(cf.EVENT, {"x": "y" * (300 * 1024)})
    with pytest.raises(cf.FrameError):
        cf.encode(9, b"")
    with pytest.raises(cf.FrameError):
        cf.encode(cf.IN, b"x" * 5000)


def test_read_frame_round_trip():
    async def go():
        r = asyncio.StreamReader(); r.feed_data(cf.encode_json(cf.ACTION, {"type": "end"})); r.feed_eof()
        return await cf.read_frame(r)
    t, body = asyncio.run(go())
    assert t == cf.ACTION and cf.decode_json(t, body) == {"type": "end"}


def test_signing_framing_is_untouched():
    assert tty.MAX_JSON == 4096 and 7 not in tty._KNOWN_TYPES and 8 not in tty._KNOWN_TYPES
```

```python
# backlot/claude_frames.py
"""Frames between Front Lot's server and a Claude session broker.

Same 5-byte header as the signing terminal (backlot/tty.py) but its own types
and limits: conversation events and snapshots are far larger than the signing
program's 4 KB JSON cap, and widening tty.py would loosen the signing socket's
parser. Nothing here is shared with the signing path.
"""
from __future__ import annotations

import asyncio, json, struct

HEADER = struct.Struct("!BI")
HELLO, IN, OUT, RESIZE, STATUS, BYE, EVENT, ACTION = 1, 2, 3, 4, 5, 6, 7, 8
JSON_TYPES = frozenset({HELLO, RESIZE, STATUS, BYE, EVENT, ACTION})
LIMITS = {IN: 4096, OUT: 65536, **{t: 256 * 1024 for t in JSON_TYPES}}


class FrameError(ValueError):
    pass


def encode(frame_type: int, payload: bytes = b"") -> bytes:
    if frame_type not in LIMITS:
        raise FrameError("unknown frame type")
    payload = bytes(payload)
    if len(payload) > LIMITS[frame_type]:
        raise FrameError("oversized frame")
    return HEADER.pack(frame_type, len(payload)) + payload


def encode_json(frame_type: int, obj: dict) -> bytes:
    if frame_type not in JSON_TYPES:
        raise FrameError("frame type is not JSON")
    return encode(frame_type, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def decode_json(frame_type: int, payload: bytes) -> dict:
    if frame_type not in JSON_TYPES:
        raise FrameError("frame type is not JSON")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FrameError("malformed JSON frame") from exc
    if not isinstance(value, dict):
        raise FrameError("JSON frame must carry an object")
    return value


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Raises asyncio.IncompleteReadError on EOF, FrameError on a bad frame."""
    frame_type, length = HEADER.unpack(await reader.readexactly(HEADER.size))
    if frame_type not in LIMITS:
        raise FrameError("unknown frame type")
    if length > LIMITS[frame_type]:
        raise FrameError("oversized frame")
    return frame_type, await reader.readexactly(length)
```

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_frames.py -v` → 4 passed.

- [ ] **Step 2: Journal (tests, then code)**

```python
# tests/backlot/test_claude_journal.py
from backlot.claude_journal import Journal

SNAP = lambda: {"state": "ready", "hello": None, "rows": [], "cards": []}


def test_since_returns_tail(tmp_path):
    j = Journal(tmp_path / "e.jsonl", keep=5)
    for i in range(3):
        j.append({"kind": "row", "i": i})
    events, snap = j.since(1, SNAP)
    assert [e["event"]["i"] for e in events] == [1, 2] and snap is None


def test_gap_returns_snapshot(tmp_path):
    j = Journal(tmp_path / "e.jsonl", keep=3)
    for i in range(10):
        j.append({"kind": "row", "i": i})
    events, snap = j.since(1, SNAP)
    assert events == [] and snap["kind"] == "snapshot" and snap["state"] == "ready" and snap["cursor"] == 10


def test_cursor_from_the_future_returns_snapshot(tmp_path):
    j = Journal(tmp_path / "e.jsonl", keep=5); j.append({"kind": "row"})
    events, snap = j.since(99, SNAP)
    assert events == [] and snap["cursor"] == 1


def test_survives_reopen(tmp_path):
    j = Journal(tmp_path / "e.jsonl", keep=5); j.append({"kind": "row"})
    j2 = Journal(tmp_path / "e.jsonl", keep=5)
    assert j2.append({"kind": "row"}) == 2
```

```python
# backlot/claude_journal.py
"""Bounded, durable event journal for one film's Claude session (spec §3.1)."""
from __future__ import annotations

import json, os
from pathlib import Path
from typing import Callable


class Journal:
    def __init__(self, path: Path, keep: int = 2000):
        self.path = Path(path); self.keep = keep
        self.events: list[dict] = []
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    self.events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        self.events = self.events[-keep:]
        self.seq = self.events[-1]["seq"] if self.events else 0

    def append(self, event: dict) -> int:
        self.seq += 1
        rec = {"seq": self.seq, "event": event}
        self.events.append(rec)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        if len(self.events) > self.keep:
            self.events = self.events[-self.keep:]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(e) + "\n" for e in self.events))
            os.replace(tmp, self.path)
        return self.seq

    def since(self, seq: int, snapshot: Callable[[], dict]):
        gap = bool(self.events) and seq < self.events[0]["seq"] - 1
        if gap or seq > self.seq:
            return [], {**snapshot(), "kind": "snapshot", "cursor": self.seq}
        return [e for e in self.events if e["seq"] > seq], None
```

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_journal.py -v` → 4 passed. Commit: `git add backlot/claude_frames.py backlot/claude_journal.py tests/backlot/test_claude_frames.py tests/backlot/test_claude_journal.py && git commit -m "feat(front-lot): broker framing and session event journal"`.

- [ ] **Step 3: Test helpers and broker tests (with a fake `claude`)**

```python
# tests/backlot/claude_fakes.py
"""Shared fakes for Front Lot's Claude session tests (no real claude, no spend)."""
from __future__ import annotations

import json, os, signal, socket, sys, time
from pathlib import Path

from tests.backlot.tty_helpers import wait_until

REPO = Path(__file__).resolve().parents[2]

# Runs as `claude` in the film's work area with the broker's allowlisted env. It records the live
# endpoint it was given (tests cannot read Claude's env any other way), then echoes its stdin.
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
with open("live.json", "w") as fh:
    json.dump({"socket": os.environ.get("FRONTLOT_LIVE_SOCKET"), "token": os.environ.get("FRONTLOT_LIVE_TOKEN")}, fh)
print("FAKE CLAUDE READY", flush=True)
for line in sys.stdin:
    print("echo:" + line.strip(), flush=True)
"""

# Test-only operations, injected into the broker subprocess (monkeypatching in pytest cannot reach it).
STUB_SITECUSTOMIZE = """import os, sys
sys.path.insert(0, os.environ["FRONTLOT_TEST_REPO"])
from backlot import claude_ops as _ops

def _build(paid):
    def build(ctx, p):
        return _ops.Prepared(ctx.op, paid, [sys.executable, "-c", "print('made 1')"], [],
                             summary="Make one test picture for hero-a", entity="hero-a",
                             estimate_usd=0.12 if paid else None)
    return build

_ops.OPERATIONS["test_paid"] = _ops.Operation(True, frozenset(), _build(True))
_ops.OPERATIONS["test_free"] = _ops.Operation(False, frozenset(), _build(False))
"""


def write_fake_claude(directory: Path) -> Path:
    fake = directory / "claude"
    fake.write_text(FAKE_CLAUDE); fake.chmod(0o755)
    return fake


def stub_ops_env(directory: Path) -> dict[str, str]:
    hook = directory / "stub_ops"; hook.mkdir(exist_ok=True)
    (hook / "sitecustomize.py").write_text(STUB_SITECUSTOMIZE)
    existing = os.environ.get("PYTHONPATH")
    return {"PYTHONPATH": str(hook) if not existing else str(hook) + os.pathsep + existing,
            "FRONTLOT_TEST_REPO": str(REPO)}


def live_endpoint(film: Path, timeout: float = 10) -> tuple[str, str]:
    f = film / "frontlot-work" / "live.json"
    wait_until(f.exists, timeout=timeout, description="fake claude wrote live.json")
    data = json.loads(f.read_text())
    return data["socket"], data["token"]


def post_live(sock: str, token: str, route: str, body: dict, timeout: float = 10) -> dict:
    raw = json.dumps(body).encode()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(timeout); s.connect(sock)
    s.sendall(f"POST {route} HTTP/1.1\r\nHost: frontlot\r\nContent-Type: application/json\r\n"
              f"x-frontlot-token: {token}\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw)
    data = b""
    while chunk := s.recv(65536):
        data += chunk
    s.close()
    head, _, payload = data.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200"), head
    return json.loads(payload or b"{}")


def hello(sock: str, token: str, epoch: str) -> None:
    post_live(sock, token, "/hello", {"epoch": epoch, "sessionId": "s", "source": "launch", "cwd": "/w",
                                      "claudeVersion": "2.1.294", "history": []})


def stop_brokers(gates: Path, timeout: float = 15) -> None:
    """SIGTERM every broker recorded under this test's private gates dir (never a global pkill)."""
    for ident in (gates / "claude").glob("*.json"):
        if ident.name.count(".") != 1:
            continue  # <slug>.unavailable.json, <slug>.sandbox-ok.json
        try:
            pid = json.loads(ident.read_text())["broker_pid"]
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            continue
        end = time.time() + timeout
        while time.time() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
```

```python
# tests/backlot/test_claude_session.py
import json, os, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path

import pytest

from backlot import claude_frames as cf
from tests.backlot.claude_fakes import REPO, hello, live_endpoint, post_live, stub_ops_env, write_fake_claude
from tests.backlot.tty_helpers import recv_frame, wait_until


@pytest.fixture
def world(tmp_path):
    gates = Path(tempfile.mkdtemp(prefix="omcs-", dir="/tmp"))  # AF_UNIX path limit (~104 bytes)
    projects = tmp_path / "projects"; film = projects / "film"; film.mkdir(parents=True)
    (film / "project.json").write_text(json.dumps({"title": "Film"}))
    env = dict(os.environ, OPENMONTAGE_GATES_DIR=str(gates), OPENMONTAGE_PROJECTS_DIR=str(projects),
               FRONTLOT_CLAUDE=str(write_fake_claude(tmp_path)), FRONTLOT_SKIP_PREFLIGHT="1",
               FRONTLOT_LEASE_SECONDS="1", **stub_ops_env(tmp_path))
    procs: list[subprocess.Popen] = []
    yield {"env": env, "gates": gates, "film": film, "procs": procs, "dir": gates / "claude"}
    for p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(5)
    shutil.rmtree(gates, ignore_errors=True)


def start(world, *extra, env=None):
    p = subprocess.Popen([sys.executable, "scripts/claude_session.py", "--broker", "--project", "film", *extra],
                         cwd=REPO, env=env or world["env"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    world["procs"].append(p)
    return p


def connect(world, *, page="pa", subscribe_from=0, controller=True):
    sock_path = world["dir"] / "film.sock"
    wait_until(sock_path.exists, timeout=10, description="broker socket")
    s = socket.socket(socket.AF_UNIX); s.settimeout(10); s.connect(str(sock_path))
    s.sendall(cf.encode_json(cf.HELLO, {"subscribe_from": subscribe_from, "controller": controller, "page": page}))
    return s


def next_of(s, want_type, pred=lambda d: True):
    while True:
        t, payload = recv_frame(s)
        if t == want_type and (want_type == cf.OUT or pred(json.loads(payload))):
            return payload if want_type == cf.OUT else json.loads(payload)


def status(s):
    return next_of(s, cf.STATUS)


def test_identity_written_before_socket_published(world):
    start(world)
    wait_until((world["dir"] / "film.sock").exists, timeout=10, description="broker socket")
    data = json.loads((world["dir"] / "film.json").read_text())
    for k in ("session_id", "broker_pid", "broker_started", "claude_pid", "claude_pgid", "claude_started", "boot_time"):
        assert k in data
    assert json.loads((world["dir"] / "film.session").read_text())["session_id"] == data["session_id"]


def test_pty_output_and_input_relay(world):
    start(world)
    s = connect(world)
    s.sendall(cf.encode(cf.IN, b"hello\n"))
    buf, end = b"", time.time() + 10
    while b"echo:hello" not in buf and time.time() < end:
        t, payload = recv_frame(s)
        if t == cf.OUT:
            buf += payload
    assert b"echo:hello" in buf


def test_second_client_is_read_only_and_told_why(world):
    start(world)
    a = connect(world, page="pa"); assert status(a)["controller"] is True
    b = connect(world, page="pb"); assert status(b)["controller"] is False
    b.sendall(cf.encode_json(cf.ACTION, {"type": "submit", "text": "hi"}))
    n = next_of(b, cf.EVENT, lambda d: d["event"].get("kind") == "notice")
    assert n["seq"] is None and "control" in n["event"]["plain"].lower()


def test_controller_lease_survives_a_short_disconnect_then_expires(world):
    start(world)
    a = connect(world, page="pa"); assert status(a)["controller"] is True
    a.close(); time.sleep(0.2)
    b = connect(world, page="pb"); assert status(b)["controller"] is False      # held for pa
    a2 = connect(world, page="pa"); assert status(a2)["controller"] is True     # same page regains it
    a2.close(); time.sleep(1.5)                                                 # FRONTLOT_LEASE_SECONDS=1
    c = connect(world, page="pc"); assert status(c)["controller"] is True


def test_end_kills_claude_group_and_cleans_up(world):
    p = start(world)
    s = connect(world)
    ident = json.loads((world["dir"] / "film.json").read_text())
    s.sendall(cf.encode_json(cf.ACTION, {"type": "end"}))
    p.wait(timeout=20)
    with pytest.raises(ProcessLookupError):
        os.killpg(ident["claude_pgid"], 0)
    assert not (world["dir"] / "film.sock").exists() and not (world["dir"] / "film.json").exists()
    assert (world["dir"] / "film.session").exists()  # Pick back up still possible


def test_sigterm_shuts_down_cleanly(world):
    p = start(world)
    connect(world)
    p.terminate()
    p.wait(timeout=20)
    assert not (world["dir"] / "film.sock").exists() and not (world["dir"] / "film.live.sock").exists()


def test_unavailable_is_written_when_claude_is_missing(world):
    env = dict(world["env"], FRONTLOT_CLAUDE="/nonexistent/claude")
    p = start(world, env=env)
    assert p.wait(timeout=20) == 3
    assert json.loads((world["dir"] / "film.unavailable.json").read_text())["reason"] == "missing"
    assert not (world["dir"] / "film.sock").exists()


def test_run_is_idempotent_by_key_and_unknown_keys_are_not_received(world):
    start(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    a = post_live(sock, token, "/run", {"key": "k1", "op": "test_paid", "params": {}})
    b = post_live(sock, token, "/run", {"key": "k1", "op": "test_free", "params": {}})
    assert a["status"] == "waiting-for-ben" and b["requestId"] == a["requestId"] and b["status"] == "waiting-for-ben"
    assert post_live(sock, token, "/run", {"key": "k2", "op": "rm_rf", "params": {}})["status"] == "refused"
    assert post_live(sock, token, "/run-check", {"key": "k2"})["status"] == "not-received"
    assert post_live(sock, token, "/run-check", {"key": "k1"})["status"] == "waiting-for-ben"


def test_a_second_hello_cancels_unstarted_requests(world):
    start(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    assert post_live(sock, token, "/run", {"key": "k1", "op": "test_paid", "params": {}})["status"] == "waiting-for-ben"
    hello(sock, token, "e2")  # add-on reloaded
    assert post_live(sock, token, "/run-check", {"key": "k1"})["status"] == "cancelled"
```

- [ ] **Step 4: Implement the broker**

Write `scripts/claude_session.py` (one file, ~550 lines). The helpers below are complete; the `Broker` class follows the numbered list exactly.

```python
# scripts/claude_session.py
"""Per-film broker for Front Lot's embedded Claude (spec §3.1).

Owns: the claude PTY (raw terminal + 64 KB replay), the add-on live endpoint
(Story-drive protocol + /run), the request store, the event journal, and the
controller lease. Survives Front Lot restarts. Ends on End/New, SIGTERM, or
when Claude exits.
"""
from __future__ import annotations

import argparse, asyncio, collections, fcntl, hashlib, json, os, pty, re, secrets, signal, subprocess, sys, time, uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from backlot import claude_frames as cf                       # noqa: E402
from backlot import claude_settings as cs                     # noqa: E402
from backlot.claude_journal import Journal                    # noqa: E402
from backlot.claude_ops import OpError, prepare, warm_estimates  # noqa: E402
from backlot.claude_requests import Rejected, RequestStore    # noqa: E402
from backlot.tty import metadata_root                         # noqa: E402
from lib.paths import PROJECTS_DIR                            # noqa: E402
from scripts.gate_sign import _login_environment, _safe_replay_tail  # noqa: E402

REPLAY_BYTES = 64 * 1024
LEASE_SECONDS = float(os.environ.get("FRONTLOT_LEASE_SECONDS", "30"))
NO_HELLO_SECONDS, SILENT_SECONDS = 8, 15          # Story-drive's add-on timeouts (spec §5)
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
READ_ONLY = "This window is read-only. Use Take control to act here."
REPLY_STATUS = {"waiting-for-ben": "waiting-for-ben", "approved": "running", "launching": "running",
                "running": "running", "uncertain": "unknown-outcome", "done": "done", "failed": "failed",
                "declined": "declined", "cancelled": "cancelled", "expired": "expired"}
CARD_STATE = {"approved": "running", "launching": "running"}


class Unavailable(Exception):
    def __init__(self, reason: str, version: str | None = None):
        super().__init__(reason)
        self.reason, self.version = reason, version


def claude_dir() -> Path:
    d = metadata_root() / "claude"; d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def paths(slug: str) -> dict[str, Path]:
    d = claude_dir()
    return {k: d / f"{slug}{suffix}" for k, suffix in {
        "sock": ".sock", "lock": ".lock", "spawn_lock": ".spawn.lock", "ident": ".json", "live": ".live.sock",
        "events": ".events.jsonl", "settings": ".settings.json", "brief": ".brief.md", "session": ".session",
        "unavailable": ".unavailable.json", "sandbox_ok": ".sandbox-ok.json", "canary": ".canary"}.items()}


def boot_time() -> float:
    out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
    return float(out.split("sec =")[1].split(",")[0])   # "{ sec = 1791400000, usec = 0 } ..."


def proc_started(pid: int) -> float | None:
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not out:
        return None
    return time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y"))


def resolve_claude() -> tuple[str, str]:
    pinned = os.environ.get("FRONTLOT_CLAUDE")   # when set, it is the only candidate
    cand = [pinned] if pinned else [str(Path.home() / ".local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    for c in cand:
        if not (Path(c).is_file() and os.access(c, os.X_OK)):
            continue
        if os.environ.get("FRONTLOT_SKIP_PREFLIGHT"):
            return c, "test"
        try:
            v = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
            nums = tuple(int(x) for x in v.split()[0].split(".")[:3])
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            raise Unavailable("missing") from None
        if nums < cs.MIN_LIVE_VERSION:
            raise Unavailable("old-version", v)
        auth = subprocess.run([c, "auth", "status", "--json"], capture_output=True, text=True, timeout=20)
        try:
            logged_in = json.loads(auth.stdout or "{}").get("loggedIn") is True
        except ValueError:
            logged_in = False
        if auth.returncode != 0 or not logged_in:
            raise Unavailable("signed-out", v)
        return c, v
    raise Unavailable("missing")


def sandbox_selfcheck(slug: str, claude: str, version: str, settings_file: Path, work: Path, env: dict) -> bool:
    """Fail closed (spec §4.1): the session starts only if a sandboxed Bash run can read an allowed canary
    and cannot read one under the denied metadata root. One short Claude turn, cached per version+settings."""
    p = paths(slug)
    want = {"version": version, "settings_sha256": hashlib.sha256(settings_file.read_bytes()).hexdigest()}
    try:
        if json.loads(p["sandbox_ok"].read_text()) == want:
            return True
    except (OSError, ValueError):
        pass
    deny_secret, allow_secret = secrets.token_hex(8), secrets.token_hex(8)
    allow_file = work / ".frontlot-canary"
    p["canary"].write_text(deny_secret); allow_file.write_text(allow_secret)
    try:
        out = subprocess.run(cs.selfcheck_argv(claude=claude, settings_file=settings_file,
                                               prompt=cs.selfcheck_prompt(p["canary"], allow_file)),
                             cwd=work, env=env, capture_output=True, text=True, timeout=180)
        passed = cs.selfcheck_passed(out.stdout + out.stderr, deny_secret=deny_secret, allow_secret=allow_secret)
    except (OSError, subprocess.TimeoutExpired):
        passed = False
    finally:
        p["canary"].unlink(missing_ok=True); allow_file.unlink(missing_ok=True)
    if passed:
        p["sandbox_ok"].write_text(json.dumps(want))
    return passed


def run_env() -> dict[str, str]:
    """Pipeline scripts run outside the sandbox as Ben, with his normal environment and keys."""
    return {k: v for k, v in os.environ.items()
            if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_") and not k.startswith("FRONTLOT_LIVE_")}


async def read_http(reader: asyncio.StreamReader) -> tuple[str, dict[str, str], dict]:
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    lines = head.decode("latin-1").split("\r\n")
    method, route, _ = lines[0].split(" ", 2)
    headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}
    length = int(headers.get("content-length", "0"))
    if method != "POST" or length > 1024 * 1024:
        raise ValueError("bad request")
    raw = await asyncio.wait_for(reader.readexactly(length), 10) if length else b""
    return route, headers, json.loads(raw or b"{}")


def http_reply(writer: asyncio.StreamWriter, status: str, body: dict) -> None:
    raw = json.dumps(body).encode()
    writer.write(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\n"
                 f"Connection: close\r\n\r\n".encode() + raw)


def reply_for(rec: dict) -> dict:
    return {"requestId": rec["id"], "status": REPLY_STATUS[rec["state"]], "plain": rec["summary"]}


def card(rec: dict) -> dict:
    return {"requestId": rec["id"], "summary": rec["summary"], "entity": rec.get("entity"),
            "estimate_usd": rec.get("estimate_usd"), "paid": rec["paid"],
            "state": CARD_STATE.get(rec["state"], rec["state"])}
```

Then, in the same file, `class Broker` with:

1. `__init__(slug, resume)`: `film = PROJECTS_DIR / slug` (must be a directory, else `Unavailable("missing-film")`); title from `project.json` `"title"` if present, else the slug; `work = cs.work_dir(film)`; `store = RequestStore(claude_dir(), slug)`; `journal = Journal(paths["events"])`; session id = `json.loads(paths["session"].read_text())["session_id"]` when `resume` and that file exists, else `str(uuid.uuid4())` and `resume = False` (the identity file is never a resume source); `token = secrets.token_hex(32)`; `addon_epoch = None`; `last_seq = 0`; `rows = collections.deque(maxlen=50)`; `last_hello = None`; `state = "starting"`; `turn_id = None`; `clients = {}` (writer → `{"page", "controller"}`); `lease = {"page": None, "until": None}`; `inbox = asyncio.Queue()`; `unacked = {}`; timestamps `spawned_at`, `last_heard`, flags `missing_sent`, `silent_sent`.
2. `async run()`: take the lifetime lock: open `paths["lock"]` and try `fcntl.flock(fd, LOCK_EX | LOCK_NB)` every 0.1 s for up to 3 s (a server may hold it for a moment while it checks); still busy → another broker is alive → exit 0. Then `paths["unavailable"].unlink(missing_ok=True)`. `claude, version = resolve_claude()`. Write settings (`cs.build_settings(repo_root=REPO, film_root=film, meta_root=metadata_root())`) and brief (`cs.build_brief(...)`) to `paths`, mode 0600. `env = cs.allowed_env(os.environ, login_path=_login_environment()["PATH"], live_socket=str(paths["live"]), live_token=token)`. Unless `FRONTLOT_SKIP_PREFLIGHT`: `sandbox_selfcheck(...)` with `cs.allowed_env(..., live_socket="", live_token="")`; False → `Unavailable("sandbox", version)`. Start the live endpoint (step 4) **before** Claude so its first `/hello` has somewhere to go. Spawn Claude (step 3). `write_identity()` then `paths["session"]` (atomic, 0600). Bind the client socket last (`asyncio.start_unix_server(handle_client, path=paths["sock"])`, chmod 0600) — this is the publication. Start `threading.Thread(target=warm_estimates, daemon=True)`, the watcher (step 7), the PTY reader (step 8). `loop.add_signal_handler(SIGTERM, lambda: asyncio.ensure_future(shutdown("terminated")))`; ignore SIGHUP. `main()` wraps `run()`: on `Unavailable as u` write `paths["unavailable"]` = `{"reason": u.reason, "version": u.version}` (omit null), release the lock, exit 3.
3. `spawn_claude()`: `master, slave = pty.openpty()`; `self.claude = subprocess.Popen(cs.launch_argv(claude=..., mod_dir=REPO / "backlot" / "claude_mod", settings_file=paths["settings"], brief_file=paths["brief"], session_id=session_id, resume=resume, prompt=...), cwd=work, env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)` (new session ⇒ own process group); close `slave`; prompt `"Give Ben the short check-in now."` or, on resume, `"Picking back up. Give Ben the short check-in now."`; `spawned_at = time.time()`.
4. Live endpoint: `asyncio.start_unix_server(handle_live, path=paths["live"])`, chmod 0600. `handle_live`: `route, headers, body = await read_http(reader)` (any error → close); `x-frontlot-token` must equal `token` (`secrets.compare_digest`) else `http_reply(writer, "401 Unauthorized", {})`; then route:
   - `/hello`: if `addon_epoch is not None` (a reload, `/clear`, or resume inside Claude): `cancel_and_journal("addon-reload")`. Set `addon_epoch = body["epoch"]`, `last_seq = 0`, `last_hello = body`, `last_heard = now`; journal `{"kind": "addon-hello", "hello": body}`; `set_state("ready")`; reply `{}`.
   - `/report`: if `body["epoch"] != addon_epoch` reply `{"acceptedThrough": 0}`; else for each event with `seq == last_seq + 1`: `last_seq = seq`, journal `{**event, "epoch": addon_epoch}` (fan-out happens in `journal_event`), keep `row` events in `rows`, track `turn` events (`phase == "start"` and no `agentId` → `turn_id = turnId`, `set_state("working")`; `complete` of that turn → `turn_id = None`, `set_state("ready")`); `last_heard = now`; reply `{"acceptedThrough": last_seq}`.
   - `/ping`: `last_heard = now`; reply `{}`.
   - `/inbox`: if `body.get("epoch") != addon_epoch` reply `{}`; else first redeliver any `unacked` action older than 10 s, otherwise `await asyncio.wait_for(inbox.get(), 25)` (timeout → `{}`); stamp `epoch = addon_epoch` on the action at delivery, remember it in `unacked[id]` with the time; reply the action.
   - `/inbox-ack`: status `submitted` or `rejected` removes `unacked[id]`; `queued` keeps it; reply `{}`.
   - `/run`: `key = body.get("key")`; not matching `KEY_RE` → `{"status": "refused", "plain": "missing request key"}`. `existing = store.by_key(key)` → reply `reply_for(existing)` **without preparing again**. Else `prep = prepare(body.get("op"), body.get("params") or {}, repo=REPO, film_slug=slug, film_root=film, snapshot_dir=claude_dir() / slug / "snap" / key)`; `OpError as e` → `{"status": "refused", "plain": str(e)}` (nothing stored). `rec = store.create(prep, key=key, session=session_id, epoch=addon_epoch or "")`. Free → `launch_and_journal(rec)` then reply `reply_for(store.get(rec["id"]))`; paid → journal `{"kind": "spend-request", **card(rec)}`, reply `reply_for(rec)`. No tool discovery happens here (Task 3), so the reply returns well inside the add-on's 5 s.
   - `/run-check`: `rec = store.by_key(body.get("key", ""))` → `reply_for(rec)`, else `{"status": "not-received", "plain": "Front Lot has no request with that key."}`.
   - anything else → `404`.
5. Helpers: `journal_event(ev)` → `seq = journal.append(ev)` and send `EVENT {"seq": seq, "event": ev}` to every client (an event too big for a frame is replaced by `{"kind": "notice", "plain": "Part of the conversation is too long to show here; it is in the terminal view."}`); `notice(writer, plain)` → `EVENT {"seq": None, "event": {"kind": "notice", "plain": plain}}` to that client only; `set_state(s)` → if changed, journal `{"kind": "session-state", "state": s}` and send each client a fresh `STATUS`; `cancel_and_journal(reason)` → for each id from `store.cancel_unstarted(reason=reason)`: journal `{"kind": "spend-decided", "requestId": id, "state": "cancelled", "reason": reason}`; `launch_and_journal(rec)` → `store.launch(rec["id"], repo=REPO, env=run_env())`, journal `{"kind": "run-started", **card(store.get(id))}`; `Rejected as e` → journal `{"kind": "notice", "plain": e.plain}`; `tell_claude(text)` → `inbox.put({"id": uuid4().hex, "submit": text})`; `snapshot()` → `{"state": state, "hello": last_hello, "rows": list(rows), "cards": [card(r) for r in store.all_requests() if r["session"] == session_id]}`.
6. Client socket `handle_client`: the first frame must be `HELLO {"subscribe_from", "controller", "page"}` (anything else → close). Grant control when `controller` is true and (`lease["page"] is None` or `lease["page"] == page` or (no connected client holds it and `lease["until"]` has passed)); on grant set `lease = {"page": page, "until": None}` and mark this client controller. Send `STATUS`, then `OUT` with `_safe_replay_tail(replay)`, then `journal.since(subscribe_from, snapshot)` as `EVENT` frames (or one `EVENT {"seq": None, "event": snapshot}`). Loop on `cf.read_frame`:
   - `IN`/`RESIZE`: controller only (others ignored); `IN` → `os.write(master, payload)`; `RESIZE` → `fcntl.ioctl(master, termios.TIOCSWINSZ, ...)` with the same bounds as `tty.valid_resize`.
   - `ACTION` from a non-controller with type in `{submit, stop, spend-decision, end}` → `notice(writer, READ_ONLY)`.
   - `submit {text}` → `tell_claude(text)`.
   - `stop` → if `turn_id`: `inbox.put({"id": uuid4().hex, "stop": {"turnId": turn_id}})`; `cancel_and_journal("stop")`.
   - `spend-decision {requestId, go}` → `rec = store.decide(requestId, go=bool(go), session=session_id, epoch=addon_epoch or "", controller=True)`; journal `{"kind": "spend-decided", "requestId": requestId, "state": rec["state"]}`; Go → `launch_and_journal(rec)`; Not now → `tell_claude(f"[Front Lot] Ben said Not now to: {rec['summary']}.")`. `Rejected as e` → `notice(writer, e.plain)`; if the record is now `expired`/`cancelled`, also journal its `spend-decided`.
   - `take-control` → demote the holder (`STATUS controller:false` + `notice(old, "Another window took control.")`), promote this client, `lease = {"page": page, "until": None}`.
   - `end` → `await shutdown("ended")`.
   On disconnect of the controller: `lease["until"] = time.time() + LEASE_SECONDS` (the page id keeps the lease until then).
7. Watcher, every 2 s: (a) `for id in store.reconcile()`: if the record is now `done`/`failed`/`uncertain`: journal `{"kind": "run-finished", **card(rec)}` and `tell_claude(f"[Front Lot] {rec['summary']}: {word}. {tail}")` with `word` = finished / failed / "not sure it ran — it will not be retried" and `tail` = last lines of `rec["result"]["tail"]` trimmed to 600 chars (empty for uncertain); (b) `for id in store.expire_stale()`: journal `spend-decided {state: "expired"}` and `tell_claude(f"[Front Lot] The card for {summary} expired unanswered.")`; (c) add-on health: no hello and `now - spawned_at > NO_HELLO_SECONDS` → journal `{"kind": "addon-missing"}` once; after a hello, `now - last_heard > SILENT_SECONDS` → `addon-silent` once, and `addon-back` when heard again; (d) lease expiry: when `lease["until"]` has passed and no client holds control, clear `lease`; (e) `claude.poll() is not None` → `await shutdown("claude-exited")`.
8. PTY reader: `loop.add_reader(master, ...)` → `os.read(master, 65536)`; append to `replay` (trim to `REPLAY_BYTES`); send `OUT` to all clients; `OSError`/empty read → schedule `shutdown("claude-exited")`.
9. `shutdown(reason)` (idempotent): `cancel_and_journal(reason)`; journal `{"kind": "session-state", "state": "ended", "reason": reason}`; `os.killpg(pgid, SIGHUP)`, poll `os.waitpid(pid, WNOHANG)` up to 5 s, then `SIGTERM` the group, 5 s, then `SIGKILL` the group; reap the exact child; send `BYE {"reason"}` to clients; close both servers; unlink `sock`, `live`, then `ident` last; keep `session`; release the lifetime lock; `os._exit(0)` after flushing. Paid jobs are not in Claude's group and keep running (spec §3.1).
10. `write_identity()` runs after `spawn_claude()` and before the client socket is bound: `{session_id, broker_pid: os.getpid(), broker_started: proc_started(os.getpid()), claude_pid, claude_pgid: os.getpgid(claude_pid), claude_started: proc_started(claude_pid), boot_time: boot_time(), claude_path, claude_version}` written atomically (tmp + `os.replace`), mode 0600, while the lifetime lock is held.

- [ ] **Step 5: Run broker tests**

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_session.py tests/backlot/test_claude_journal.py tests/backlot/test_claude_frames.py -v`
Expected: 17 passed (9 + 4 + 4), no broker processes left (`pgrep -f "claude_session.py --broker --project film"` prints nothing).

- [ ] **Step 6: Commit**

```bash
git add scripts/claude_session.py tests/backlot/claude_fakes.py tests/backlot/test_claude_session.py
git commit -m "feat(front-lot): per-film Claude session broker"
```

---
### Task 9: Server routes, relay, safe reconciliation (then probe P5)

**Files:**
- Create: `backlot/claude_live.py`
- Modify: `backlot/server.py:306-308` (call `install_claude(app)` after `install_tty(app)`), `_lifespan` (:275; call `shutdown_claude(app)` after `shutdown_tty` at :286-288)
- Test: `tests/backlot/test_claude_live.py`

**Interfaces:**
- Consumes: Task 8 Interfaces (frames, client protocol, identity file, paths incl. `.spawn.lock`, `.unavailable.json`, `.session`); `scripts.claude_session.{paths, claude_dir, boot_time, proc_started}`; `backlot.claude_frames`; `backlot.tty._first_token`; `server._safe_project_dir`; `tests/backlot/claude_fakes.py`.
- Produces:
  - `install_claude(app: FastAPI) -> None`; `async shutdown_claude(app) -> None` (closes relays; never kills brokers)
  - `GET /api/project/{p}/claude` → `{"state": "none"|"running"|"ended"|"unavailable", "reason"?: str, "can_resume": bool}`
  - `WS /api/project/{p}/claude/live`: origin check (close 4403), first message `{"k": token}` (4401), unknown project (4404). Then page → server: first `{"type": "start"|"resume"|"new"|"attach", "page": <page id>, "from": <last seq the page applied, 0 at first>}` (`attach` never spawns; `start`/`resume` spawn only when no broker runs; `new` replaces); afterwards `{"type": "submit", text} | {"type": "stop"} | {"type": "spend-decision", requestId, go} | {"type": "take-control"} | {"type": "end"} | {"type": "new"}`. Server → page: `{"type": "status", controller, state, session_id}`, `{"type": "event", "seq": int|null, "event": {...}}` (every kind from Task 8, including `notice` and `snapshot`), `{"type": "unavailable", "reason"}`, `{"type": "bye", "reason"}`.
  - `WS /api/project/{p}/claude/tty`: same handshake; then binary passthrough of `OUT` to the page (no `AnsiSanitizer`) and page bytes → `IN` (the broker ignores non-controllers); text messages `{"type": "resize", cols, rows}` → `RESIZE`.
  - `reconcile_orphans(slug) -> str` ("clean" | "terminated" | "stale-record")
  - `session_state(slug) -> dict`; `spawn_broker(slug, mode) -> dict` (sync; called with `asyncio.to_thread`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/backlot/test_claude_live.py
import json, os, shutil, signal, tempfile, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import claude_live, server as server_mod, state as state_mod
from scripts.claude_session import proc_started
from tests.backlot.claude_fakes import stop_brokers, stub_ops_env, write_fake_claude

PORT, TOKEN = 4799, "t" * 43
ORIGIN = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def app_world(tmp_path, monkeypatch):
    gates = Path(tempfile.mkdtemp(prefix="om-cl-", dir="/tmp"))  # AF_UNIX path limit
    projects = tmp_path / "projects"; film = projects / "film"; film.mkdir(parents=True)
    (film / "project.json").write_text(json.dumps({"title": "Film"}))
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(gates))
    monkeypatch.setenv("OPENMONTAGE_PROJECTS_DIR", str(projects))
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", os.path.normcase(str(projects.resolve())))
    monkeypatch.setattr(server_mod, "_summary_cache", {})

    async def no_watch() -> None:
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    monkeypatch.setenv("FRONTLOT_CLAUDE", str(write_fake_claude(tmp_path)))
    monkeypatch.setenv("FRONTLOT_SKIP_PREFLIGHT", "1")
    for k, v in stub_ops_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    app = server_mod.create_app(port=PORT, capability_token=TOKEN)
    yield app, gates, film
    stop_brokers(gates)
    shutil.rmtree(gates, ignore_errors=True)


def ws(client, path):
    return client.websocket_connect(path, headers={"origin": ORIGIN})


def until(w, pred, limit=400):
    for _ in range(limit):
        m = w.receive_json()
        if pred(m):
            return m
    raise AssertionError("message never arrived")


def opened(c, kind, page):
    w = ws(c, "/api/project/film/claude/live").__enter__()
    w.send_text(json.dumps({"k": TOKEN})); w.send_text(json.dumps({"type": kind, "page": page, "from": 0}))
    return w


def test_state_none_before_start(app_world):
    app, *_ = app_world
    with TestClient(app) as c:
        assert c.get("/api/project/film/claude").json() == {"state": "none", "can_resume": False}


def test_bad_origin_and_token_refused(app_world):
    app, *_ = app_world
    with TestClient(app) as c:
        with c.websocket_connect("/api/project/film/claude/live", headers={"origin": "http://evil"}) as w:
            with pytest.raises(Exception):
                w.receive_text()
        with ws(c, "/api/project/film/claude/live") as w:
            w.send_text(json.dumps({"k": "wrong"}))
            with pytest.raises(Exception):
                w.receive_text()


def test_start_then_second_socket_is_read_only(app_world):
    app, *_ = app_world
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        assert until(a, lambda m: m["type"] == "status")["controller"] is True
        b = opened(c, "attach", "pb")
        assert until(b, lambda m: m["type"] == "status")["controller"] is False
        b.send_text(json.dumps({"type": "submit", "text": "hi"}))
        n = until(b, lambda m: m["type"] == "event" and m["event"].get("kind") == "notice")
        assert n["seq"] is None and "control" in n["event"]["plain"].lower()
        assert c.get("/api/project/film/claude").json()["state"] == "running"
        a.close(); b.close()


def test_new_replaces_only_after_the_previous_broker_exits(app_world):
    app, gates, film = app_world
    ident = gates / "claude" / "film.json"
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        until(a, lambda m: m["type"] == "status")
        first = json.loads(ident.read_text())
        a.send_text(json.dumps({"type": "new"}))
        until(a, lambda m: m["type"] == "status" and m["session_id"] != first["session_id"])
        assert proc_started(first["broker_pid"]) is None            # the old broker is gone
        assert json.loads(ident.read_text())["session_id"] != first["session_id"]
        a.close()


def test_unavailable_state_names_the_reason(app_world, monkeypatch):
    app, *_ = app_world
    monkeypatch.setenv("FRONTLOT_CLAUDE", "/nonexistent/claude")
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        assert until(a, lambda m: m["type"] == "unavailable")["reason"] == "missing"
        assert c.get("/api/project/film/claude").json()["state"] == "unavailable"
        a.close()


def test_reconcile_stale_record_with_reused_pid_is_not_signalled(app_world):
    app, gates, _ = app_world
    d = gates / "claude"; d.mkdir(exist_ok=True)
    (d / "film.json").write_text(json.dumps({"claude_pid": os.getpid(), "claude_pgid": os.getpgid(0),
                                             "claude_started": 1.0, "boot_time": 1.0}))
    assert claude_live.reconcile_orphans("film") == "stale-record"
    assert not (d / "film.json").exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_live.py -v`
Expected: FAIL with `ImportError: cannot import name 'claude_live'`.

- [ ] **Step 3: Implement `backlot/claude_live.py`**

Structure (follow `backlot/tty.py` patterns for origin, token, and bounded send queues):

```python
# backlot/claude_live.py
"""Front Lot server side of the embedded Claude session (spec §3.3).

Spawns and attaches per-film brokers, relays their events to pages, and
reconciles orphaned sessions safely. Holds no conversation state of its own:
the broker owns the journal and the controller lease; the page owns its
cursor (the last event seq it applied).
"""
from __future__ import annotations

import asyncio, fcntl, json, os, signal, subprocess, sys, time
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from backlot import claude_frames as cf, tty
from scripts.claude_session import boot_time, claude_dir, paths as broker_paths, proc_started

REPO = Path(__file__).resolve().parents[1]
SPAWN_WAIT = 200        # first start may run the sandbox self-check (one model turn)
REPLACE_WAIT = 25       # End escalation is at most ~12 s; spec §3.1


def _started_matches(pid, recorded) -> bool:
    now = proc_started(pid) if pid else None
    return now is not None and recorded is not None and abs(now - float(recorded)) <= 1


def broker_alive(slug: str) -> bool:
    """From the identity file only (never touches the broker's lock)."""
    try:
        ident = json.loads(broker_paths(slug)["ident"].read_text())
    except (OSError, ValueError):
        return False
    return _started_matches(ident.get("broker_pid"), ident.get("broker_started"))


@contextmanager
def _lifetime_lock_if_free(path: Path):
    """Yields True while holding the film's lifetime lock if no broker holds it; never waits."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    held = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); held = True
        except BlockingIOError:
            held = False
        yield held
    finally:
        if held:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def reconcile_orphans(slug: str) -> str:
    """Call only while holding the film's lifetime lock (no broker alive)."""
    p = broker_paths(slug)
    if not p["ident"].exists():
        return "clean"
    ident = json.loads(p["ident"].read_text())
    pid, pgid = ident.get("claude_pid"), ident.get("claude_pgid")
    same_boot = abs(boot_time() - float(ident.get("boot_time", 0))) < 2
    if not same_boot or not _started_matches(pid, ident.get("claude_started")):
        for k in ("ident", "sock", "live"):
            p[k].unlink(missing_ok=True)
        return "stale-record"
    for sig, wait in ((signal.SIGHUP, 5), (signal.SIGTERM, 5), (signal.SIGKILL, 2)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        end = time.time() + wait
        while time.time() < end and proc_started(pid) is not None:
            time.sleep(0.2)
    for k in ("ident", "sock", "live"):
        p[k].unlink(missing_ok=True)
    return "terminated"


def session_state(slug: str) -> dict:
    p = broker_paths(slug)
    can_resume = p["session"].exists()
    if p["unavailable"].exists():
        try:
            info = json.loads(p["unavailable"].read_text())
        except (OSError, ValueError):
            info = {}
        out = {"state": "unavailable", "reason": info.get("reason", "unknown"), "can_resume": can_resume}
        if info.get("version"):
            out["version"] = info["version"]
        return out
    if broker_alive(slug):
        return {"state": "running", "can_resume": False}
    return {"state": "ended" if can_resume else "none", "can_resume": can_resume}


def _end_live_broker(slug: str) -> bool:
    """SIGTERM the verified broker, then wait until it has exited and its identity file is gone."""
    p = broker_paths(slug)
    try:
        ident = json.loads(p["ident"].read_text())
        if _started_matches(ident.get("broker_pid"), ident.get("broker_started")):
            os.kill(ident["broker_pid"], signal.SIGTERM)
    except (OSError, ValueError, KeyError):
        pass
    end = time.time() + REPLACE_WAIT
    while time.time() < end:
        with _lifetime_lock_if_free(p["lock"]) as free:
            if free:
                reconcile_orphans(slug)            # clears a record left by a broker that died mid-shutdown
                return not p["ident"].exists()
        time.sleep(0.2)
    return False


def spawn_broker(slug: str, mode: str) -> dict:
    """mode: "start" | "resume" | "new". One spawner per film (spawn lock); the broker's own
    lifetime lock is only probed, never held while waiting for the new broker."""
    p = broker_paths(slug)
    with open(p["spawn_lock"], "a+") as spawn:
        fcntl.flock(spawn, fcntl.LOCK_EX)
        if broker_alive(slug) and mode != "new":
            return {"state": "running"}
        if broker_alive(slug) and not _end_live_broker(slug):
            return {"state": "running", "notice": "The previous conversation is still closing. Try again in a moment."}
        with _lifetime_lock_if_free(p["lock"]) as free:
            if not free:
                return {"state": "running"}
            reconcile_orphans(slug)                # under the film's lock (spec §3.1)
        if mode == "new":
            p["session"].unlink(missing_ok=True)
        p["unavailable"].unlink(missing_ok=True)
        argv = [sys.executable, str(REPO / "scripts" / "claude_session.py"), "--broker", "--project", slug]
        if mode == "resume" and p["session"].exists():
            argv.append("--resume")
        subprocess.Popen(argv, cwd=REPO, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + SPAWN_WAIT
        while time.time() < end:
            if p["sock"].exists():
                return {"state": "running"}
            if p["unavailable"].exists():
                return session_state(slug)
            time.sleep(0.1)
        return {"state": "unavailable", "reason": "no-start"}
```

Then implement:
- `class _Relay`: one per page socket. `async open(slug, page, subscribe_from)` connects with `asyncio.open_unix_connection(broker_paths(slug)["sock"])` and sends `HELLO {"subscribe_from", "controller": True, "page"}` (the broker decides who controls). Pump broker → page: `STATUS` → `{"type": "status", ...}`; `EVENT` → `{"type": "event", "seq", "event"}`; `OUT` → only to `/tty` sockets (bytes); `BYE` → `{"type": "bye", ...}` and close. Pump page → broker: `submit`/`stop`/`spend-decision`/`take-control`/`end` → `ACTION` frames unchanged; `new` → close this relay, `await asyncio.to_thread(spawn_broker, slug, "new")`, reopen with `subscribe_from=0` (the page resets its model on the new `addon-hello`). Bounded send queue per page (`tty.MAX_WS_PENDING`); a page that cannot keep up is closed (it reconnects with its cursor).
- Live WebSocket handler: origin check exactly as `tty._tty_websocket` (`{http://127.0.0.1:<port>, http://localhost:<port>}` from `app.state.server_port`, close 4403); token via `tty._first_token(websocket, app.state.capability_token)` (close 4401); unknown project → 4404 via `server._safe_project_dir`. First message `{type, page, from}`: `start`/`resume`/`new` → `res = await asyncio.to_thread(spawn_broker, slug, type)`; `attach` → no spawn. If the result (or `session_state`) is `unavailable` → send `{"type": "unavailable", "reason"}` and keep the socket open (the page shows the fix). If `res` has a `notice` → send it as `{"type": "event", "seq": None, "event": {"kind": "notice", "plain": ...}}`. If a broker is running → open a `_Relay` with `subscribe_from = from`. If none is running on `attach` → send `{"type": "status", "controller": False, "state": session_state(slug)["state"], "session_id": None}`.
- `/tty` WebSocket: same handshake; first text message `{"page"}`; relay with `subscribe_from` = the journal's current end (a large number such as `2**62`, which the broker answers with a snapshot the tty socket ignores) so only PTY replay + live bytes flow.
- `install_claude(app)`: register the GET route (`session_state`) and both WebSockets; keep relays in `app.state.claude_relays`. `shutdown_claude(app)` closes every relay (brokers keep running).

- [ ] **Step 4: Wire into the server**

```python
# backlot/server.py, after install_tty(app) (around line 308)
    from backlot.claude_live import install_claude
    install_claude(app)
```

and in `_lifespan` after `await shutdown_tty(app)`: `from backlot.claude_live import shutdown_claude; await shutdown_claude(app)`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/backlot -q`
Expected: the 6 new tests pass; the Step 0 baseline still holds (263 passing before this work, plus every test added since), the one pre-existing Playwright-browser failure unchanged; `pgrep -f "claude_session.py --broker"` prints nothing afterwards.

- [ ] **Step 6: Probe P5 (append to PROBES)**

With the real server on a test port (`BACKLOT_PORT=4752`), `OPENMONTAGE_GATES_DIR` set to a `mkdtemp` dir, and the fake claude (`FRONTLOT_CLAUDE`, `FRONTLOT_SKIP_PREFLIGHT=1`): start a session from a WebSocket; `kill -9` the broker pid from the identity file; reconnect with `start` → the server's `spawn_broker` runs `reconcile_orphans` → returns `terminated`, the fake claude's process group is gone (`ps -g <pgid>` empty), and a new session starts. Then write an identity file whose `claude_pid` is a live unrelated process (a `sleep 300` started for the probe) with a wrong `claude_started` → `reconcile_orphans` returns `stale-record` and the `sleep` is still alive. Record PASS/FAIL.

- [ ] **Step 7: Commit**

```bash
git add backlot/claude_live.py backlot/server.py tests/backlot/test_claude_live.py docs/superpowers/specs/*PROBES.md
git commit -m "feat(front-lot): Claude session routes, relay, safe reconciliation; probe P5"
```

---

### Task 10: Early real run (free work only, with Ben)

**Files:** none (manual run); notes appended to `docs/superpowers/specs/2026-10-07-front-lot-claude-session-PROBES.md` under "Early real run".

- [ ] **Step 1: Start a test server from the worktree**

Ben picks the film (`FILM=<its project id>`). Run (Ben's Terminal or `!`):
`cd ~/Projects/OpenMontage-worktrees/front-lot-redesign && BACKLOT_PORT=4751 OPENMONTAGE_PROJECTS_DIR=$HOME/Projects/OpenMontage/projects .venv/bin/python -m backlot open "$FILM"`
(`backlot open` has no `--port`; it reads `BACKLOT_PORT`, starts the server if needed, and opens the board with the capability token.) The film lives outside the worktree; the session's record rules follow the film's real folder (Task 1).

- [ ] **Step 2: From a browser console on the board, drive the live socket**

Paste a minimal client (the page UI arrives in Task 11):

```js
const film = location.pathname.split("/")[2];
const w = new WebSocket(`ws://${location.host}/api/project/${film}/claude/live`);
w.onopen = () => {
  w.send(JSON.stringify({ k: sessionStorage.getItem("backlot.capability-token") }));
  w.send(JSON.stringify({ type: "start", page: "console", from: 0 }));
};
w.onmessage = (m) => console.log(JSON.parse(m.data));
```

Expected: `status` with `controller: true`; on first start, a pause while the sandbox self-check runs; then `addon-hello` and `row` events with Claude's check-in in plain words. If `unavailable` arrives instead, its `reason` names the fix (`sandbox` means stop and report: the self-check failed).

- [ ] **Step 3: Ask for free work**

`w.send(JSON.stringify({type: "submit", text: "Do a dry run of the look for the first character that has none."}))`
Expected: a `row` carrying a `tool_use` block named `mcp__frontlot-live__frontlot_run`, then `run-started` and `run-finished` events for a `look` request with `--dry-run`, and Claude summarising the dry run. No `spend-request` events. No `~/.openmontage/gates/generation-ledger.jsonl` growth.

- [ ] **Step 4: Record what broke**

Write every gap Ben or the run shows into the PROBES file under "Early real run", and fix them (each as its own commit with a test) before Task 11.

---
### Task 11: The column: conversation, composer, Stop, terminal, states

**Files:**
- Create: `backlot/ui/session_model.js` (pure reducer), `backlot/ui/session.js` (rendering + sockets)
- Modify: `backlot/ui/board.html` (log column structure), `backlot/ui/board.js` (`renderLog` returns `{needs, history}`; the caller at :2241 fills `#log-feed` and `#history-body`; mounts the session), `backlot/ui/board.css` (session styles, `.log-feed` sizing, `.log-history`)
- Test: `tests/backlot/session_model.test.mjs` (`node --test`), `tests/backlot/test_ui_session.py` (Playwright, skipped when the browser is not installed, same pattern as `test_ui_bug_bash.py`)

**Interfaces:**
- Consumes: Task 9 page protocol (Task 8 event kinds); `lib.js` `el`, `CAPABILITY_TOKEN_KEY`; Story-drive's `src/live/model.ts` (`initialModel` :68, `toolLabel` :87, `applyEvent` :251, `reduce` :331) and `src/live/markdown.ts`.
- Produces:
  - `session_model.js`: `initialModel()` (Story-drive's model plus `cards: {}`, `session: "starting"`, `cursor: 0`), `reduce(model, msg)` (Story-drive's `LiveMessage` reducer, ported), `apply(model, brokerEvent, seq)` (maps one Task 8 event onto `reduce` or the Front Lot cases and records `cursor = seq` when `seq` is a number), `toolLabel(tools)`.
  - `session.js`: `export function mountSession({ projectId, feedEl, terminalEl, composerEl, stateEl, onRunFinished })`.

- [ ] **Step 1: Port the reducer with a unit test**

Create `backlot/ui/session_model.js` by porting Story-drive's `src/live/model.ts` to plain JS: keep `initialModel`, `toolLabel`, `applyEvent`, `reduce` and their helpers (`goLost`, `holding`, `adaptHistory`, `textOf`, `isPerson`, `id`) and the notice constants; drop ticket-specific code (`steps`, `takeQuestion`, `Ticket` imports, question/draft/checklist marks stay harmless). Story-drive's reducer takes `LiveMessage`s: `{type: "hello", hello}` (switches to the live view and resets the model), `{type: "events", epoch, events}` (ignored unless `epoch` matches), `{type: "lost", epoch, reason}`, `{type: "recovered", epoch}`, `{type: "session-ended"}`. Add:

```js
export function apply(m, e, seq) {
  const w = applyKind(m, e);
  return typeof seq === "number" ? { ...w, cursor: seq } : w;
}

function cardState(m, e) {
  const card = m.cards[e.requestId];
  return card ? { ...m.cards, [e.requestId]: { ...card, state: e.state } } : m.cards;
}

function applyKind(m, e) {
  switch (e.kind) {
    case "addon-hello": {  // a reload resets the conversation view but never the cards
      const w = reduce(m, { type: "hello", hello: e.hello });
      return { ...w, cards: m.cards, session: m.session, cursor: m.cursor };
    }
    case "addon-missing": return reduce(m, { type: "lost", epoch: null, reason: "no-hello" });
    case "addon-silent": return reduce(m, { type: "lost", epoch: m.epoch, reason: "silent" });
    case "addon-back": return reduce(m, { type: "recovered", epoch: m.epoch });
    case "session-state":
      return e.state === "ended" ? { ...reduce(m, { type: "session-ended" }), session: "ended" } : { ...m, session: e.state };
    case "spend-request": return { ...m, cards: { ...m.cards, [e.requestId]: { ...e } } };
    case "spend-decided":
    case "run-started":
      return { ...m, cards: cardState(m, e) };
    case "run-finished":
      return { ...m, cards: cardState(m, e),
        feed: [...m.feed, { id: `run-${e.requestId}`, kind: "run", summary: e.summary, state: e.state, entity: e.entity }] };
    case "notice": return { ...m, notice: e.plain };
    case "snapshot": {
      let w = initialModel();
      if (e.hello) {
        w = reduce(w, { type: "hello", hello: e.hello });
        w = reduce(w, { type: "events", epoch: e.hello.epoch, events: e.rows });
      }
      return { ...w, session: e.state, cursor: e.cursor,
        cards: Object.fromEntries(e.cards.map((c) => [c.requestId, c])) };
    }
    default:  // Story-drive add-on events carry their epoch (Task 8)
      return reduce(m, { type: "events", epoch: e.epoch, events: [e] });
  }
}
```

Test with node:

```js
// tests/backlot/session_model.test.mjs
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
```

Run: `node --test tests/backlot/session_model.test.mjs` → 6 pass.

- [ ] **Step 2: Build the column**

`board.html` log column becomes:

```html
<aside class="log" id="log" aria-label="Log">
  <div class="log-feed" id="log-feed"></div>            <!-- Needs you (from board.js) -->
  <section class="session" id="session" aria-label="Conversation with Claude">
    <p class="session-state" id="session-state" aria-live="polite"></p>
    <p class="session-note" id="session-note" aria-live="polite" hidden></p>
    <div class="session-feed" id="session-feed" aria-live="polite"></div>
    <div class="session-terminal" id="session-terminal" hidden></div>
    <form class="composer" id="composer">
      <textarea id="composer-text" rows="2" placeholder="Talk to Claude about this film"></textarea>
      <button type="submit" class="quiet-btn" id="composer-send">Send</button>
      <button type="button" class="quiet-btn" id="composer-stop" hidden>Stop</button>
      <button type="button" class="quiet-btn" id="terminal-toggle" aria-pressed="false">Show terminal</button>
    </form>
  </section>
  <details class="log-section log-more log-history" id="history"><summary>History</summary><div id="history-body"></div></details>
  <section class="terminal-shell" id="gate-terminal-shell" ...>  <!-- unchanged signing bay -->
</aside>
```

`board.js`: `renderLog(s, roster)` returns `{ needs, history }`: `needs` = the "Needs you" section (unchanged); `history` = "The log" (decided approvals, with its Show all button), `renderDecisions(s)` ("Production decisions") and `renderActivity(s)` (Machine room), in that order. At the caller (:2241) replace `logFeed.append(renderLog(s, roster))` with `const { needs, history } = renderLog(s, roster); logFeed.append(needs); document.getElementById("history-body").replaceChildren(history);` (the `<details>` element itself is static, so its open state survives re-renders). Mount the session once at page start: `mountSession({ projectId, feedEl: #session-feed, terminalEl: #session-terminal, composerEl: #composer, stateEl: #session-state, onRunFinished })`.

`session.js`: one page id per page load (`crypto.randomUUID()`); opens `/claude/live`, sends `{k: sessionStorage.getItem(CAPABILITY_TOKEN_KEY)}` then `{type: "start", page, from: model.cursor}` (on reconnect after a server restart it sends `{type: "attach", page, from: model.cursor}` so nothing is replayed twice and nothing is lost); applies `status` (controller flag + `state`), `event` (`model = apply(model, msg.event, msg.seq)`), `unavailable`, `bye`. Renders: assistant prose (Markdown subset from Story-drive's `markdown.ts`: paragraphs, lists, bold; escaped), tool steps as one line each (`toolLabel`) with `<details>` for the detail, run lines ("Made 3 headshot candidates for hero-a — done"), spend cards:

```js
function spendCard(card, send) {
  const cost = card.estimate_usd != null ? `about $${card.estimate_usd.toFixed(2)}` : "cost unknown";
  const go = el("button", { class: "sign-btn", type: "button", onclick: () => send({ type: "spend-decision", requestId: card.requestId, go: true }) }, "Go");
  const no = el("button", { class: "quiet-btn", type: "button", onclick: () => send({ type: "spend-decision", requestId: card.requestId, go: false }) }, "Not now");
  const waiting = card.state === "waiting-for-ben";
  return el("article", { class: `spend-card ${card.state}` },
    el("h3", {}, card.summary), el("p", {}, cost),
    waiting ? el("div", { class: "spend-actions" }, go, no) : el("p", { class: "spend-state" }, PLAIN_STATE[card.state] || ""));
}
const PLAIN_STATE = { approved: "Go. Starting…", launching: "Starting…", running: "Running…", done: "Done",
  failed: "Failed. Claude will explain.", uncertain: "Not sure it ran. Check the board before trying again.",
  declined: "Not now", cancelled: "Cancelled", expired: "Expired" };
```

States line (`#session-state`): Starting / Ready / Working / Waiting for you (a card is `waiting-for-ben` or the view is raw because Claude is waiting) / Ended + "Pick back up" (`{type:"resume"}`) + "New conversation" (`{type:"new"}`, confirm inline) / Unavailable + the fix text (`missing`: "Install Claude Code, then reload." `signed-out`: "Open Terminal and run: claude auth login" `old-version`: "Update Claude Code, then reload." `sandbox`: "Front Lot couldn't confirm Claude's safety sandbox, so the session stays off. Tell Claude in your own terminal: the Front Lot sandbox check failed."). The model's `notice` shows in `#session-note`. Read-only mode (controller false): composer disabled and a "Take control" button (`{type:"take-control"}`). Stop (`{type:"stop"}`) shows while `model.working`. Raw terminal: one page-lifetime xterm bound to `/claude/tty`, shown in `#session-terminal` when `model.view === "raw"` or the toggle is on.

When a `run-finished` event names an entity, `onRunFinished(entity)` fires and board.js calls `selectEntity("character:" + entity)` (or `location:`) so new frames land in the viewer.

- [ ] **Step 3: Styles**

In `board.css` (Cutting Room tokens only):
- `.log-feed { flex: none; max-height: 34%; overflow-y: auto; ... }` (was `flex: 1 1 auto`; the conversation now owns the free height, so only one region grows).
- `.session { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; border-top: 1px solid var(--seam); }`; `.session-feed { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 16px 20px; }`; `.session-terminal { flex: 1 1 auto; min-height: 0; }`.
- Assistant text `font-size: 15px; line-height: 1.55; color: var(--ink)`; tool lines `.entry`-style with tape marks.
- `.spend-card` = `.need` styling (grease top rule); `.spend-actions { display: flex; gap: 12px; margin-top: 12px }` with Go isolated from Not now by space.
- `.composer` docked at the bottom with `textarea { background: var(--glass); color: var(--ink); border: 0; box-shadow: inset 0 0 0 1px var(--seam) }`.
- `.log-history { flex: none; max-height: 30%; overflow-y: auto; padding: 0 20px; }` — a new class; the existing `.history` (:391) stays the grid list used by `ul.history` (board.js :1726).
No new colours; yellow only on Go and the card's top rule.

- [ ] **Step 4: Browser test**

```python
# tests/backlot/test_ui_session.py
import pytest
pytest.importorskip("playwright.sync_api")
# Follow test_ui_bug_bash.py's staged_backlot_server fixture (server subprocess, capability token, page
# helper), with its own module fixture: a film folder under a temp projects dir, OPENMONTAGE_GATES_DIR from
# tempfile.mkdtemp(dir="/tmp"), FRONTLOT_CLAUDE = tests.backlot.claude_fakes.write_fake_claude(tmp),
# FRONTLOT_SKIP_PREFLIGHT=1; teardown calls claude_fakes.stop_brokers(gates) and removes the dir.
# Open /p/film; the session starts on its own. Assert within 15 s:
#   - #session-terminal is visible and contains "FAKE CLAUDE READY" (the fake has no add-on, so after the
#     8 s no-hello window the column falls back to the raw terminal),
#   - #session-note is visible and not empty (the plain note saying the live view is unavailable),
#   - #history exists with class log-history and #log-feed still shows "Needs you",
#   - no horizontal overflow at 390 px width (document.documentElement.scrollWidth <= 390).
```

Write it fully in the style of `test_ui_bug_bash.py`, then run: `.venv/bin/python -m pytest tests/backlot/test_ui_session.py -v` (skips if the Playwright browser is missing, as today).

- [ ] **Step 5: Screenshot check and design detector**

Capture desktop (1440×900) and mobile (390) of a film with a live session into `backlot/.impeccable/review/`; run `~/.claude/skills/impeccable/scripts/impeccable detect --json backlot/ui/board.css backlot/ui/board.html backlot/ui/session.js` and fix findings.

- [ ] **Step 6: Commit**

```bash
git add backlot/ui/session_model.js backlot/ui/session.js backlot/ui/board.html backlot/ui/board.js backlot/ui/board.css \
  tests/backlot/session_model.test.mjs tests/backlot/test_ui_session.py
git commit -m "feat(front-lot): conversation column with composer, Stop, terminal view, states"
```

---

### Task 12: Spend card end to end, then one real paid run

**Files:** `tests/backlot/test_claude_live.py` (append); notes in PROBES under "First paid run".

- [ ] **Step 1: End-to-end with a stubbed paid op**

Append to `tests/backlot/test_claude_live.py` (its `app_world` fixture already puts the stub operations `test_paid`/`test_free` on the broker's `PYTHONPATH` via `claude_fakes.stub_ops_env`; the fake claude records the live socket and token in `frontlot-work/live.json`):

```python
from tests.backlot.claude_fakes import hello as addon_hello, live_endpoint, post_live


def test_spend_card_go_runs_once_and_reports_the_outcome(app_world):
    app, gates, film = app_world
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        until(a, lambda m: m["type"] == "status" and m["controller"] is True)
        sock, token = live_endpoint(film)
        addon_hello(sock, token, "e1")
        r = post_live(sock, token, "/run", {"key": "toolu_1", "op": "test_paid", "params": {}})
        assert r["status"] == "waiting-for-ben"
        card = until(a, lambda m: m["type"] == "event" and m["event"].get("kind") == "spend-request")["event"]
        assert card["requestId"] == r["requestId"] and card["estimate_usd"] == 0.12
        a.send_text(json.dumps({"type": "spend-decision", "requestId": r["requestId"], "go": True}))
        until(a, lambda m: m["type"] == "event" and m["event"].get("kind") == "spend-decided"
              and m["event"]["state"] == "approved")
        done = until(a, lambda m: m["type"] == "event" and m["event"].get("kind") == "run-finished")["event"]
        assert done["state"] == "done"
        a.send_text(json.dumps({"type": "spend-decision", "requestId": r["requestId"], "go": True}))
        n = until(a, lambda m: m["type"] == "event" and m["event"].get("kind") == "notice")
        assert n["event"]["plain"] == "That card was already answered."
        log = [json.loads(x)["state"] for x in (gates / "claude" / "film.spend.jsonl").read_text().splitlines()]
        assert log == ["waiting-for-ben", "approved", "launching", "running", "done"]
        assert post_live(sock, token, "/run-check", {"key": "toolu_1"})["status"] == "done"
        a.close()


def test_not_now_is_recorded_and_never_runs(app_world):
    app, gates, film = app_world
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        until(a, lambda m: m["type"] == "status")
        sock, token = live_endpoint(film)
        addon_hello(sock, token, "e1")
        r = post_live(sock, token, "/run", {"key": "toolu_2", "op": "test_paid", "params": {}})
        a.send_text(json.dumps({"type": "spend-decision", "requestId": r["requestId"], "go": False}))
        until(a, lambda m: m["type"] == "event" and m["event"].get("kind") == "spend-decided"
              and m["event"]["state"] == "declined")
        assert post_live(sock, token, "/run-check", {"key": "toolu_2"})["status"] == "declined"
        assert not list((gates / "claude" / "film" / "requests").glob("*.claim"))
        a.close()
```

Run: `.venv/bin/python -m pytest tests/backlot/test_claude_live.py -v` → all pass. Commit: `git add tests/backlot/test_claude_live.py && git commit -m "test(front-lot): spend card end to end"`.

- [ ] **Step 2: One real paid run, with Ben**

On the test server (Task 10 setup), Ben asks Claude for one paid item for one entity he chooses (one item, per the one-at-a-time rule). Ben reads the card and presses Go. Verify: one new line in `generation-ledger.jsonl`, the pictures appear in the viewer, Claude summarises the result, `claude/$FILM.spend.jsonl` shows `waiting-for-ben, approved, launching, running, done`. Record in PROBES. If the run ends `uncertain` or `failed`, nothing is retried: record it and stop for Ben.

- [ ] **Step 3: Full suite and finish**

Run:
- `.venv/bin/python -m pytest -q` (the whole repo, not just `tests/backlot`: Task 4 changed scripts covered by `tests/lib` and `tests/tools`)
- `claude plugin test backlot/claude_mod && claude plugin validate --strict backlot/claude_mod`
- `node --test tests/backlot/session_model.test.mjs`
Expected: compared with the Task 1 Step 0 baseline, every previously passing test still passes, all new tests pass, and the pre-existing Playwright-browser failure is the only failure; `pgrep -f "claude_session.py --broker"` prints nothing.
Then use superpowers:finishing-a-development-branch.
