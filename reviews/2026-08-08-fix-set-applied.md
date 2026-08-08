# Fix Set Applied — authored-film pipeline (2026-08-08)

Response to `2026-08-08-codex-peer-review.md` and the independent review
(`~/Desktop/2026-08-08-independent-authored-film-review.md`). All six items of
the independent review's "minimal fix set before first real production run"
are implemented, TDD-first: 27 new behavioral tests in
`tests/lib/test_authored_film_contract.py` reproduce every review probe and
were watched failing before any production code changed.

## 1. Manifest-aware checkpoint validation (was Critical #1)

`lib/checkpoint.py` — new `_enforce_manifest_artifact_contract`, called from
`write_checkpoint` for every pipeline with a loadable manifest:

- `produces` is binding: completed / awaiting_human checkpoints missing a
  declared output are rejected. The hard-coded `CANONICAL_STAGE_ARTIFACTS`
  map remains only as the legacy fallback when no manifest resolves.
- `required_artifacts_in` is binding: inputs must be carried in the
  checkpoint or produced by a completed predecessor checkpoint.

This is generic (all pipelines). Three pre-existing tests and the QA
end-to-end script were writing `proposal` without its manifest-declared
`decision_log` — they now comply (they were violating the manifest, which is
exactly what the enforcement is for).

## 2. Decision-log repair (was High #4)

- `decision_log.schema.json`: `canon_ruling` added to the category enum;
  new optional `question_id` links a ruling to a canon packet open question.
- `write_checkpoint` reordered: ALL validation (checkpoint schema, artifact
  schemas, authored-canon profile) runs before ANY state persists. A rejected
  checkpoint leaves the project directory untouched — proven by tests.
- `_merge_decision_log` now validates the MERGED log against the schema
  before writing, writes to a temp file, and swaps with `os.replace`.

## 3. Effective-canon state (independent review miss #1/#2)

- `canon_packet.schema.json`: `open_questions[]` items now require stable
  `id` and `status` (open/resolved), with optional `resolution_ref`.
- `lib/canon_enforcement.py` blocks COMPLETION of every post-ingest stage
  while any `blocking: true` question is open and no `canon_ruling` decision
  (cumulative log ∪ current checkpoint) carries its `question_id`.
  `awaiting_human` stays writable — that is the escalation surface.
- `canon_packet` moved from optional to **required** input at `edit` and
  `compose` in the manifest; with generic input enforcement this makes
  effective canon available at every downstream stage.

## 4. Authored validation profile (was High #5)

New manifest key `validation_profile: authored-canon` (added to the manifest
schema; declared by authored-film.yaml) dispatches to
`lib/canon_enforcement.py` at write time:

- **script**: every section needs a `source_ref` matching a canon beat/lock
  id; `metadata.canon_check.locks_honored` required;
  `unresolved_conflicts` must be empty; every protected line must appear
  character-for-character in section text.
- **scene_plan**: every scene needs `canon_refs` (new optional schema field,
  required by the profile) resolving to canon ids.
- **asset_manifest**: image/video/animation assets need the new structured
  `continuity` object (`canon_refs`, `references_applied`,
  `risk_notes_applied`).
- **final_review**: new structured `checks.canon_pass` (schema-defined):
  per-lock verdicts, per-protected-line verbatim/audible verdicts,
  continuity spotchecks, tone verdict, unresolved flags. Compose cannot
  complete unless status is `pass`, every lock is `honored`, and every
  protected line is confirmed verbatim.

Shared schemas gained only OPTIONAL fields — other pipelines are unaffected
(pipeline-aware validation, per the review's warning).

## 5. Compose reality check (independent review miss #5, was Critical #2)

At authored-film compose completion, every `render_report.outputs[].path`
must resolve INSIDE the project workspace (path-escape rejected), exist as a
non-empty regular file, and pass ffprobe with playable streams and positive
duration. Fails closed if ffprobe is missing. `final_review` presence is
covered by generic `produces` enforcement.

## 6. Schema provenance hardening (was Medium #7)

`canon_packet.schema.json`: `sha256` now required with `^[a-f0-9]{64}$`
pattern; non-empty `path`/`id`/`decision`/`source_path`/`line`;
`writing_document` authority tier added; `pitch_export` added to lock
`source_type`.

## Skills updated to teach the enforced contract

canon-guard (runtime backstop section), canon-director (question ids/status),
proposal-director (question_id on rulings + composition-runtime HARD RULE),
scene-director (canon_refs), asset-director (continuity field),
compose-director (structured canon_pass + render verification + runtime
routing/blocker language).

## Verification

- 27/27 new contract tests pass, including the full seven-stage happy path
  with a real ffmpeg-rendered file.
- Full suite: 767 passing; remaining failures are the SAME pre-existing
  environment import errors both reviews documented (`requests`, `PIL`,
  `google`, `numpy`, `fastapi` missing from the Python 3.14 interpreter) —
  none touch changed code paths.
- Both runtime-presentation governance tests for authored-film now pass
  (they were failing before this fix set, unnoticed by both reviews).

## Round 2 — response to the fix-record review (same day)

A second independent review of this fix record found six P1s and one P2, all
reproduced. All seven are fixed, again TDD-first: 15 new regression tests
(now 42 total in `tests/lib/test_authored_film_contract.py`), each watched
failing before the fix.

1. **Failed canon pass can now reach the writer.** Compose strictness applies
   on `completed` only; `awaiting_human` requires the structured canon_pass
   to exist but accepts failing verdicts — that checkpoint IS the escalation.
   Same model applied to script tensions (presentable at the gate, cannot
   complete unruled).
2. **Only real rulings release blocking questions.** A `canon_ruling` counts
   only with matching `question_id`, `user_approved: true`, and `selected`
   naming one of its `options_considered`. Schema now conditionally requires
   `question_id` + `user_approved` for the category. Resolved question ids
   join the effective canon id set, so downstream artifacts can trace to
   rulings.
3. **Skill and runtime enforce ONE script contract.** Runtime parses the
   skill-documented composite `source_ref` (`"canon:beat-07 lock-003"`);
   conflict field unified on `tensions` (each needs `resolution_ref` to
   complete; `unresolved_conflicts` kept as an always-empty back-compat
   check); `locks_honored` must cover every canon lock id.
4. **Canon pass now enforces the whole promise.** Schema requires
   `continuity_spotchecks`, `tone` (non-empty verdict), `unresolved_flags`,
   and per-line `audible`. Runtime (on completed): every protected line
   audible, every tracked character/location spot-checked `consistent`,
   unresolved_flags empty.
5. **Asset continuity evidence must be real.** `canon_refs` validated against
   canon ids + tracked entity names; an asset depicting an entity with
   canon `reference_assets` / `visual_continuity_risks` must have non-empty
   `references_applied` / `risk_notes_applied` or an explicit
   `continuity.notes` justification.
6. **Audio-only output is not a film.** ffprobe check requires a video
   stream, duration matching the render report (±max(1s, 15%)), and video
   dimensions matching the reported resolution.
7. **(P2) Ruling and checkpoint commit together or not at all.** The
   checkpoint serializes to its temp file BEFORE the decision log commits;
   if the checkpoint swap then fails, the decision log rolls back to its
   exact pre-merge bytes (injected-failure test proves it; the first probe
   version of that test was itself vacuous — pytest's tmp dir name matched
   the failure filter — caught and fixed).

Verification after round 2: 42/42 contract tests pass; full non-tools suite
539 passed with the identical 25 pre-existing missing-module failures
(`requests`/`google`/PIL class) — no new failures.

## Known deliberate scope choices

- Generic produces/required-inputs enforcement applies at WRITE time only;
  read-path validation is unchanged so legacy checkpoints keep loading.
- The effective-canon model is packet + rulings keyed by `question_id`, not
  a full amendment/revision-hash chain — sufficient for the blocking-question
  contract; a revision chain remains future work if lock EDITS (vs question
  resolutions) need runtime tracking.
- Render existence/ffprobe checks are authored-profile only, to avoid
  breaking other pipelines' existing test fixtures; promoting them to all
  pipelines is a candidate follow-up.
