# Handoff: Front Lot Claude session build, Tasks 1–4 done (2026-10-08)

Start a new chat with: "Read ~/Projects/OpenMontage-worktrees/front-lot-redesign/docs/superpowers/HANDOFF-2026-10-08-front-lot-claude-build.md and continue the build at Task 5."

This replaces `HANDOFF-2026-10-07-front-lot.md`, which is kept for background on the design and spec.

## Where things are

- **Worktree:** `~/Projects/OpenMontage-worktrees/front-lot-redesign`, branch `front-lot-redesign`. HEAD is `64da51f`. Nothing is pushed, merged or deployed, and Ben's live Front Lot (port 4750) is untouched.
- **Spec:** `docs/superpowers/specs/2026-10-07-front-lot-claude-session-design.md` (rev 4, Codex approved).
- **Plan:** `docs/superpowers/plans/2026-10-07-front-lot-claude-session.md` (12 tasks, Codex approved round 5). Task 7 was amended on 2026-10-08 (see below).
- **Build method:** superpowers `subagent-driven-development`. The controller dispatches one implementer per task, one task review, and fix rounds.
- **Ledger (read this first, trust it over memory):** `.superpowers/sdd/2026-10-07-front-lot-claude-session/progress.md`. This folder is git-ignored scratch. It also holds every task brief, report and review diff. Don't run `git clean -fdx`.
- **Untracked:** `backlot/.impeccable/questions/` and `review/` are design scratch. Leave them alone.

## Done

| Task | What | Commits | Review |
|---|---|---|---|
| 1 | Settings, brief, env, launch argv, sandbox self-check | 937322d..d176e4a | clean |
| 2 | Probes P1–P3 (security build gate) | d176e4a..354835e | clean after 1 fix round |
| 3 | Operation allowlist, argv adapters, cost estimates (`backlot/claude_ops.py`) | a6c98f7..6a3a7f8 | clean after 1 fix round |
| 4 | Paid scripts refuse if approved inputs changed (`--expect-*` flags) | 6a3a7f8..64da51f | clean after 1 fix round |

**Task 2 gate.** The first run failed four rows. Ben approved all four fixes on 2026-10-08:
- `~/Library/Keychains` added to the denies.
- `--no-chrome` added to the launch arguments, plus a deny rule for `mcp__claude-in-chrome`.
- The `mark` tool removed from the add-on, because add-on tools bypass the allowlist.
- The brief now tells Claude to reach WriterOS with `curl --noproxy ''`.

The re-run gave 53 PASS, 0 FAIL. Evidence is in `docs/superpowers/specs/2026-10-07-front-lot-claude-session-PROBES.md` (re-run section at the bottom).

**Plan change.** Task 7 no longer mentions `registerMark`, `MARK_TOOL` or `MARK_DESCRIPTION`. `registerRun` goes right after `const isReload = await read($, opened)` in `session.start`, and `frontlot_run` must stay the add-on's only tool.

## Facts later tasks need (also in the ledger)

- `lib.run_common.EXIT_INPUT_CHANGED = 5` means "refused: an approved input changed after Go; nothing was spent". It applies to headshot_run, sheet_run and supervised_shot. The other exit codes are 1 (other error), 2 (usage), 3 (blocked) and 4 (declined).
  - Task 5's `reconcile()` should settle an exit-5 outcome as `failed`, never `uncertain` or success.
  - Task 6's wrapper and Task 12's card should show it as "refused, nothing spent".
- Folder trust: the first interactive launch in a new work area shows Claude Code's trust dialog with the cursor on "No, exit". Task 8's broker must handle this or pre-trust the work area.
- Some sandboxed Bash command shapes are refused by dontAsk (e.g. `curl -w '%{http_code}'`). The brief and probes use `curl -D -`.
- `claude_ops` card summaries still show entity ids and wording like "Shot step: inspect". Check these against the plain-language rule when Tasks 8, 11 and 12 render them.
- Deferred minors for the final whole-branch review are listed in the ledger as `minor (deferred)` lines.

## Next step

**Task 5: Request store and state machine** (`backlot/claude_requests.py`).
- BASE is `64da51f`. The brief has already been extracted to `.superpowers/sdd/2026-10-07-front-lot-claude-session/task-5-brief.md`.
- The dispatch prompt should carry the `EXIT_INPUT_CHANGED = 5` fact above.
- Then Tasks 6–9 follow: launcher (then probe P4), add-on `frontlot_run`, session broker, server routes (then probe P5). All of these are code and tests only.

**Why it stopped.** The Task 5 dispatch was blocked by Claude Code's auto-mode permission check, which reported "Auto-Mode Bypass" with no detail. Ben's "yes" had covered starting Task 3. Before continuing, get Ben's explicit go for how far to run, e.g. "continue through Task 9". Don't work around the check.

## Where Ben is needed

- **Task 10:** the first real run with him, free work only.
- **Task 12:** one real paid run, which he approves with Go on the spend card.
- Anything paid, any merge or push, and any change to his live Front Lot.

## Tests

- `.venv/bin/python -m pytest tests/backlot tests/lib -q`: everything passes except `tests/backlot/test_visual_eval.py::test_headshot_gate_sse_preserves_real_xterm_dom_and_session`. That test was failing before this build started; it's a Node Playwright browser issue.
- Add-on: `claude plugin validate --strict backlot/claude_mod` and `claude plugin test backlot/claude_mod` (24 pass).
- Probes (take ~20 min and drive real Claude sessions, never paid):
  - `.venv/bin/python scripts/claude_probe.py` (P1)
  - `--p2-auto`
  - `--p2-pty`
  - `--p3`
