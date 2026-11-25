# Handoff: Front Lot redesign + Claude session (2026-10-07)

Start a new chat with: "Read ~/Projects/OpenMontage-worktrees/front-lot-redesign/docs/superpowers/HANDOFF-2026-10-07-front-lot.md and continue."

## Where things are

- **Worktree:** `~/Projects/OpenMontage-worktrees/front-lot-redesign`, branch `front-lot-redesign` (off `authored-film` bf827c6). `.venv` is a symlink to the main repo's venv.
- **Ben's live Front Lot** (`~/Projects/OpenMontage`, port 4750, the Front Lot icon) is untouched. Nothing is deployed or pushed.
- Test server was on port 4751; it has been stopped. Restart with:
  `cd ~/Projects/OpenMontage-worktrees/front-lot-redesign && OPENMONTAGE_PROJECTS_DIR=$HOME/Projects/OpenMontage/projects .venv/bin/python -m backlot serve --port 4751`
  then open `http://127.0.0.1:4751/p/bloodless`.

## Phase 1: new look ("The Cutting Room"). DONE, NOT COMMITTED

Ben's direction: Front Lot was ugly and only showed visuals. Each app has one job: Story-drive = beat sheet, WriterOS = what images look like, Front Lot = making images. Impeccable was required. It must not copy Story-drive's look.

- Built with Impeccable (direction chosen by Ben on the decision page, seed 391d43a6): machine bar / trim bin (characters + places, three slots: look, headshot, sheet) / big viewer + frame strip / log column (Needs you, the log, signing bay). Yellow = only "needs you". The light/dark switch was removed.
- Two finish-review rounds done; every flagged item fixed. Design detector: clean.
- Files: `backlot/ui/{board.css,board.html,board.js,index.html,library.js}`, `backlot/state.py` (library poster falls back to the first canon headshot), `backlot/ui/vendor/fonts/` (Barlow Condensed, OFL), `backlot/PRODUCT.md`, `backlot/DESIGN.md`, `backlot/.impeccable/`.
- **These changes are still uncommitted** in the worktree. The six spec/plan commits sit on top of them. Ask Ben before committing them (suggested message: `feat(front-lot): Cutting Room redesign`).
- Tests: `.venv/bin/python -m pytest tests/backlot -q` → 263 pass. 1 failure existed before any change: Node Playwright's browser isn't installed (`npx playwright install`).
- Not seen live: the yellow needs-you cards and the Start signing button. Bloodless has no pending approvals, so they were checked from code only.

## Phase 2: Claude session in the log column. SPEC + PLAN DONE, BUILD NOT STARTED

Ben's decisions:
- One ongoing conversation per film that picks up where it left off.
- Claude opens with a short check-in.
- A spend card with Go / Not now before anything paid.
- Approach A: Story-drive's live add-on pattern, drawn in Front Lot's look, with the raw terminal as fallback.

- **Spec:** `docs/superpowers/specs/2026-10-07-front-lot-claude-session-design.md` (rev 4). **Codex approved it in round 4.** The full argument is in `...-REVIEW-LOG.md` beside it.
- **Core rule after review:** Claude talks and plans; Front Lot runs the pipeline.
  - Claude runs in Claude Code's OS sandbox: no signing key, no provider keys, no paid network, can't edit Front Lot code. Its working folder is `projects/<film>/frontlot-work/`.
  - Every pipeline step goes through one add-on tool, `frontlot_run`.
  - Paid steps wait for Ben's Go and launch exactly once, with frozen inputs.
  - Threat model: a capable, cooperative agent that errs or improvises. It does not cover a hostile program running as Ben.
- **Plan:** `docs/superpowers/plans/2026-10-07-front-lot-claude-session.md` (12 tasks). Task 2 (sandbox probes) is a hard gate: if any probe fails, stop and tell Ben. Ben is needed at Task 10 (early free run) and Task 12 (one real paid run he approves with Go).
- **Open question for Ben** (unanswered when he left):
  - Execution method: subagent-driven (recommended: interlocking, security-sensitive tasks) or native in-session.
  - Whether he wants to read the plan first.

## Background facts the next session needs

- Story-drive's add-on: `~/Projects/story-drive/mod/story-drive-live/`. Its protocol is in `hooks/protocol.ts`, the app side in `src/main/live.ts`, the reducer in `src/live/model.ts`. Leave Story-drive itself untouched; the plan vendors a copy into `backlot/claude_mod/`.
- Claude CLI: `~/.local/bin/claude` 2.1.293. Live mode needs ≥ 2.1.288.
- The embedded claude must not inherit `CLAUDECODE` / `CLAUDE_CODE_*`. Inheriting them breaks transcripts and resume (Story-drive spike).
- Function-hook budget is 10 s, so nothing may block waiting on Ben inside a hook.
- Impeccable was updated to v4.5.0 on 2026-10-07. The update also installed its design-check hooks.
- Ben's rules that apply here:
  - Plain language.
  - Say "Front Lot", never "Backlot".
  - No story names in code or tests.
  - Codex reviews before build.
  - Run it before reviewing it.
  - One paid item at a time.
  - Don't cripple agents: every limit names what it protects.
