# Plan D20: `look_run.py` and `headshot_run.py` — the two steps before a sheet become commands
_Drafted by Claude for Codex review, 2026-08-28. Follows the claudex-loop Phase 2 protocol; MAX_ROUNDS=5._

## Goal
After D19 the sheet step is one governed command (`scripts/sheet_run.py`): generate, judge, retry within a signed cap, stop at the gate. The two steps before it are still hand-driven: the look ticket is ingested and its `look_lock` request written by an operator in chat, and headshot candidates (or a reference import) are produced by ad-hoc scripts, exactly the shape that produced the 2026-08-26 defects. Ben's ruling (2026-08-28): the deliverable is the workflow, project-agnostic, not Bloodless assets. D20 makes the whole per-character path three commands, each stopping at exactly one human gate, with nothing project-specific in code:

```
look_run.py     --project <slug> --entity <id>            → look_lock gate (ticket → validated look → request)
headshot_run.py --project <slug> --entity <id> [--import <file> --origin-tool <name> | --casting <file>]
                                                          → reference_import gate (if importing) → headshot selection gate
sheet_run.py    --project <slug> --entity <id>            → sheet gate (D19)
```

Out of scope: locations (same pattern later), the writer's authorship of the look ticket (that stays in story-wayfinder), any change to who approves canon, judging likeness.

## Diagnosis (verified 2026-08-28)
- `lib/look_ingest.py` already has `parse_look_ticket`, `look_lock_request`, `active_look_for`, `verify_look_refs`; the operator called them by hand for Ace. No command wraps them.
- `lib/reference_import.py` has `stage_reference_upload`, `prepare_reference_import` (writes the `reference_import` gate request, drops caller metadata), `finalize_reference_import`, `imported_image_ref`; `lib/headshots.py` has `headshot_request` (selection gate request with no record) and `headshot_record`; `scripts/gate_approve.py` selection mode (`headshot_candidates`, `_enforce_candidate`) rebuilds the record from the pending `headshot_packet`. Ace's import and single-candidate packet were assembled by hand.
- `headshots-director.md` specifies the intended flow (imported hero = single candidate; else up to 4 generated candidates from `tools/prompt_builder` role `hero` with `look_refs`; reject-all regenerates with a logged note; approved packet rewritten per character under D18). None of it is a command.
- D19 infrastructure is reusable as-is: QC stream (`lib/qc_receipts`), policy bundle (`lib/sheet_qc/policy`), `SheetJudge`, `qc_call_context`, attempt rows and the signed cap, `qc_override`, `verify_series_against_receipt`. The qc_verdict schema and `SheetJudge` restrict `role` to sheet roles.
- `tools/prompt_builder.py` has a `hero` framing ("head and shoulders portrait, neutral expression, facing camera") and `builder_policy_sha256()`; Seedream `text_to_image` is the governed generation path for candidates (no reference image exists yet).

## Design

### D20.1 `scripts/look_run.py` — ticket to gate, no spend
`look_run.py --project <slug> --entity <id> [--kind character|location] [--dry-run]`
1. Preconditions: verified config (1.0 or 1.1 both fine — no paid call), pin ≥ 1.2, `wayfinder_root` in config, run lease.
2. Locate the resolved ticket for the entity under `<wayfinder_root>/wayfinder/resolved/` by `entity_id` in its `## Look spec` block (never by filename); refuse if the ticket is not resolved, has no answer, or more than one ticket claims the entity.
3. `parse_look_ticket` → validated payload (schema + injection scan + `depends_on` derived from `blocked-by`, existing rules); `look_hash`.
4. If an active look exists for the key: refuse unless `--supersede`, in which case the request carries `supersedes_look_hash` (existing envelope field).
5. Write the `look_lock` gate request via `look_lock_request`; print the approval command; `--dry-run` prints what would be requested and writes nothing.
6. After approval (`look_run.py … --finish`, or auto-detected on the next invocation when the request is in `done/`): write/refresh the `look_lock` checkpoint `in_progress` with the partial packet (`build_look_packet`, existing) so `headshots` can start for this entity (D18). Idempotent.

Nothing here decides appearance; the writer did that in the ticket. The command only moves a resolved ticket to the gate and records the ratification in the checkpoint.

### D20.2 `scripts/headshot_run.py` — one approved face per character
`headshot_run.py --project <slug> --entity <id> [--import <file> --origin-tool <name>] [--casting <file>] [--candidates N≤4] [--note "<reject-all note>"] [--open]`

Preconditions (shared with `sheet_run`; extracted to `lib/run_common.py`): registered project, verified 1.1 config with `qc`, pin = 1.3, run lease, `resume_check`, active look for the entity (else "run look_run first"), no active headshot for the entity unless `--replace` (which makes the eventual receipt carry `supersedes_receipt_id`, existing mechanism, and prints the downstream-invalidation cost before anything runs).

**Mode A — import (`--import`)**. The file (JPEG/HEIC/PNG) is the writer's own generated image of the character:
1. `stage_reference_upload` → `prepare_reference_import(origin_class=imported_synthetic, origin_tool=…)` → `reference_import` gate request; print the command; stop. The original file outside the project is never modified.
2. Next invocation detects the approved import receipt (`reference_import_receipts`), runs `finalize_reference_import`, builds the single-candidate pending packet with `imported_image_ref`, writes the `headshots` checkpoint `awaiting_human`, writes the selection request (`headshot_request`); prints the command; stop.
3. No judge call on an imported hero: the writer chose it; the judge's job is to stop the *pipeline's own* mistakes reaching the writer.

**Mode B — casting inspiration (`--casting`)**. `prepare_reference_import(origin_class=casting_inspiration)` → gate request; stop. After approval the file lives under `canon/visual/casting-inspiration/` and is never opened by any command again (existing taint rules). The run then continues as Mode C on the next invocation: the image informs nothing the code does; it informed the writer's ticket.

**Mode C — generate (default)**.
1. `tools/prompt_builder.build_prompt(look, role="hero", palette)` once (recipe sealed as today).
2. Generate candidates through the governed Seedream `text_to_image` path with `stage: headshots`, `look_refs`, `prompt_recipe`, plain background in a palette hue, `num_images: 1` per call so every candidate has its own reservation, attempt row and receipt. Attempts run inside the D19 pre-submit hook: **series key** = `(entity, role="hero", look_hash, headshot_receipt_id=null, policy_bundle_sha256, builder_policy_sha256, generation endpoint/model, judge provider/model)`; cap = `qc.max_attempts_per_series × candidates` for this series (a candidate set of 4 with cap 3 allows at most 12 generations; expressed in the signed config as `qc.max_hero_attempts` default 8 — see D20.4).
3. **Judge every candidate** with a new `hero` checklist (D20.3). Candidates that fail are not presented; the loop keeps generating until `--candidates` (default 4) pass or the cap is hit. Fewer than 4 passes at the cap is still presented (1..N); zero passes → `Blocked`, override request on the best failure, same as sheets.
4. Write the pending packet `{entity_kind, entity_id, look_ref, prompt_recipe, candidates[1..N], qc_receipts{asset_id: receipt}}`, checkpoint `awaiting_human`, `headshot_request`; `--open` opens the candidates; print the selection command; stop.
5. **Reject-all**: the next invocation with `--note "<the writer's words>"` logs the note in the decision log (`category: revision`, verbatim), regenerates under the SAME series (the note never changes the prompt: a note asking for a look change is refused with "that is a look change: edit the ticket and run look_run --supersede"), counts toward the cap.
6. After the human selects at the gate, the next invocation (or `--finish`) rewrites the packet `state: approved` for this character (approved entries so far + this one; existing `approved_entry` shape), checkpoint `in_progress` (D18), rebuilds the canon view (`by-entity/<id>/hero.png`), prints `sheet_run.py …` as the next command.

### D20.3 Hero QC policy (policy bundle 1.1)
`lib/sheet_qc/policy.py` gains role `hero` (the verdict/qc schema and `SheetJudge` accept it; `sheet_run` still refuses it as a sheet role):

| id | question | severity |
|---|---|---|
| single_subject | Exactly one person, no second face or reflection? | fail |
| bust_front | A head-and-shoulders portrait facing the camera, both eyes visible? | fail |
| neutral_expression | A neutral or near-neutral expression, mouth closed or naturally parted? | fail |
| plain_background | A plain, uncluttered background with no scenery, text or props? | fail |
| no_occlusion | Face unobstructed: no hands, glasses, hat, mask, hair across the eyes? | fail |
| hair_matches | Does the hair match the described hair? (evidence: look `hair`) | fail |
| age_matches | Does the apparent age fall in the described band? (evidence: look `age_band`) | warn |
| build_matches | Does the visible build match the described build? (evidence: look `build`) | warn |
| no_text | Free of text, watermark, logo? | fail |
| photoreal_or_treatment | Rendering style consistent within the candidate (no half-illustrated face)? | warn |

Evidence kinds gain `look_hair`, `look_age_band`, `look_build` (text from the signed active look). Local rules for `hero`: square or portrait, long edge ≥ 1024, no alpha. The bundle hash changes → Bloodless re-signs its config once (`qc.policy_bundle_sha256`); every project on 1.3 must, by design.

The gate's `_enforce_candidate` under a 1.3 pin additionally requires, for a **generated** chosen candidate, a passing (or overridden) hero verdict bound to that asset, look and policy, inside a compliant attempt chain — the same `require_qc_pass` machinery, applied to one candidate instead of a sheet role set (`lib/sheet_qc/verify.require_candidate_qc`). Imported candidates are exempt (D20.2 Mode A step 3) and must carry `origin: imported_synthetic` + the attestation receipt, as today.

### D20.4 Config 1.1 additions
`qc.max_hero_attempts` (int, default 8): the cap for the `hero` series per entity per look (generation is $0.07 a shot; 8 keeps a bad look from burning money before the writer sees a face). CLI may only lower. This is a config field change → schema minor bump within 1.1 (optional field with default; existing signed 1.1 configs stay valid — enforcement uses the default when absent so Bloodless does not need a second signature for this alone; the policy re-sign in D20.3 will carry it).

### D20.5 Shared run library
`lib/run_common.py`: `open_project(slug) → (root, pipeline_dir, config, qc, pin)` with every precondition; `lease(root)`; `governed_tracker(root, config)`; `print_gate_command(root, request_id)`; `detect_done_request(root, request_id)`; `write_decision(root, category, subject, text)` (append-only decision log helper used for reject-all notes and config bindings). `sheet_run.py` is refactored onto it (no behaviour change; its tests must pass unchanged).

### D20.6 Skill docs
`look-lock-director.md` and `headshots-director.md`: "run the command; never assemble packets or requests by hand; present only what the run produced". The hero checklist is added to `headshots-director.md` the way the sheet checklist was.

## Approach (build order)
1. `lib/run_common.py` + refactor `sheet_run.py` onto it (tests unchanged).
2. Hero policy (bundle 1.1), evidence kinds, local rules, schema role enum, `SheetJudge` accepting `hero`; `require_candidate_qc`; gate `_enforce_candidate` wiring under 1.3; tests (judge with fake adapter on a hero candidate; gate refuses a generated candidate without a passing verdict; imported candidate exempt).
3. `look_run.py` with tests on a temp wayfinder root (resolved/unresolved/duplicate tickets; supersede path; finish writes the partial look_lock checkpoint).
4. `headshot_run.py` Mode A + B (import) with tests (request written; finalize on approval; single-candidate packet; casting file never opened again).
5. `headshot_run.py` Mode C (generate + judge + cap + reject-all + finish) with fake generator/judge tests; `Blocked` + override request.
6. Skill docs; Bloodless: re-sign config for policy bundle 1.1 (one signature); then Sebastian runs `look_run` (Ben answers the ticket first), `headshot_run`, `sheet_run`.

## Key decisions & tradeoffs
- **Three commands, three gates, one entity at a time.** Matches D18 and the director skills; the commands are the skills' process sections made executable.
- **Imported heroes are not judged.** The judge exists to catch the pipeline's own errors before the writer sees them; a writer-supplied image is the writer's decision. Lineage/taint rules still apply in full.
- **The reject-all note never edits the prompt.** Same rule as D19: a note that asks for a different appearance is a look change and goes back through the ticket.
- **Hero attempt cap is its own number.** Sheets are $0.14 with a 3-attempt cap per role; heroes are $0.07 and need up to 4 passing candidates, so one cap for both would be wrong in one direction or the other.
- **`look_run` is separate from `headshot_run`.** Ratifying a look is a writer's gate with no spend; mixing it into the paid command would let a headshot run start before the look is signed.

## Assumptions
1. Seedream `text_to_image` with `stage: headshots` is accepted by the governance boundary with `look_refs` + `prompt_recipe` and no `headshot_ref` (the boundary already treats `headshots` as a look-governed stage; verified in `_shared.verify_look_governance` — to re-confirm in step 5's first governed test).
2. The judge answers `hair_matches` / `age_matches` reliably enough given the look text; measured in step 2 on the four existing Ace headshot-era candidates if still in the vault, else on the first Sebastian run (advisory at first: `age_matches`/`build_matches` are warn-tier).
3. Bloodless's $100 cap has room (spent so far ≈ $1.85).

## Risks / open questions
- R1: A hero series with `headshot_receipt_id=null` in the series key differs from the sheet series shape; `verify_series_against_receipt` must accept a null headshot for role `hero` (generation receipts for candidates carry no `headshot_ref`). Explicit branch, tested.
- R2: The policy bundle re-sign touches every 1.3 project (only Bloodless today). Acceptable; documented as the cost of pinning policy in config.
- Q1: Should reject-all be allowed more than once per look? Proposal: yes, bounded by `max_hero_attempts`; the cap is the limit, not a reject counter.

## Out of scope
Locations; storyboards; changing the look_spec schema; automatic prompt repair; any Bloodless creative decision (Sebastian's look is the writer's ticket).
