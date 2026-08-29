# Plan D20: `look_run.py` and `headshot_run.py` — the two steps before a sheet become commands
_Drafted by Claude for Codex review, 2026-08-28. Follows the claudex-loop Phase 2 protocol; MAX_ROUNDS=5. Revision 2 after Codex round 1 (17 findings, all accepted — see REVIEW-LOG)._

## Goal
After D19 the sheet step is one governed command (`scripts/sheet_run.py`): generate, judge, retry within a signed cap, stop at the gate. The two steps before it are still hand-driven: the look ticket is ingested and its `look_lock` request written by an operator in chat, and headshot candidates (or a reference import) are produced by ad-hoc scripts, exactly the shape that produced the 2026-08-26 defects. Ben's ruling (2026-08-28): the deliverable is the workflow, project-agnostic, not Bloodless assets. D20 makes the whole per-character path three commands, each stopping at exactly one human gate, with nothing project-specific in code:

```
look_run.py     --project <slug> --entity <id> [--casting <file>]   → (casting import gate) → look_lock gate
headshot_run.py --project <slug> --entity <id> [--import <file> --origin-tool <name>]
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

### D20.0 Versions: authored-film 1.4, config 1.2, headshot_packet 1.1 (R1#1, #2, #7)
- **authored-film 1.4** = 1.3 + `headshots.tools_available` adds `sheet_judge`; stage contract: "every generated candidate presented at the selection gate carries a passing (or overridden) signed hero verdict; every imported candidate carries one too; casting inspiration is imported during look_lock, never here". 1.3 behaviour is untouched; Bloodless migrates 1.3→1.4 with a signed `pipeline_migration`.
- **project_config 1.2** = 1.1 + required `qc.max_hero_attempts` (int 1..24). Dual-read 1.0/1.1/1.2 in the loader; hero runs require 1.2 (no code default: the cap is signed or the command refuses). Bloodless re-signs once (this also carries the policy bundle 1.1 hash, D20.3).
- **headshot_packet 1.1** = 1.0 + per-candidate `qc_receipt_id` (pending entries: `candidates[].qc_receipt_id`; approved entries: `qc_receipt_id` beside `approval_receipt_id`), `run_state` (D20.2 step 6), and required `prompt_recipe` on generated entries (R1#14). Dual-read 1.0/1.1; 1.4 projects must write 1.1.

### D20.1 `scripts/look_run.py` — ticket to gate, no spend
`look_run.py --project <slug> --entity <id> [--kind character|location] [--dry-run]`
1. Preconditions: verified config (1.0 or 1.1 both fine — no paid call), pin ≥ 1.2, `wayfinder_root` in config, run lease.
2. Locate the resolved ticket for the entity under `<wayfinder_root>/wayfinder/resolved/` by `entity_id` in its `## Look spec` block (never by filename); refuse if the ticket is not resolved, has no answer, or more than one ticket claims the entity.
3. `parse_look_ticket` → validated payload (schema + injection scan + `depends_on` derived from `blocked-by`, existing rules); `look_hash`.
4. If an active look exists for the key: refuse unless `--supersede`, in which case the request carries `supersedes_look_hash` (existing envelope field).
5. Write the `look_lock` gate request via `look_lock_request`; print the approval command; `--dry-run` prints what would be requested and writes nothing.
6. After approval (`look_run.py … --finish`, or auto-detected on the next invocation when the request is in `done/`): write/refresh the `look_lock` checkpoint `in_progress` with the partial packet (`build_look_packet`, existing) so `headshots` can start for this entity (D18). Idempotent.
7. **`--casting <file>`** (R1#9, moved here from headshot_run per D10: the casting question belongs to look drafting, before ratification): `stage_reference_upload` (the writer's original is copied, never consumed — R1#10) → `prepare_reference_import(origin_class=casting_inspiration, entity_id=<entity>)` → `reference_import` gate request; stop. The command refuses `--casting` once the entity's look is ratified (a casting image after ratification cannot have informed the ticket). No command ever opens the file again.

Nothing here decides appearance; the writer did that in the ticket. The command only moves a resolved ticket to the gate and records the ratification in the checkpoint.

### D20.2 `scripts/headshot_run.py` — one approved face per character
`headshot_run.py --project <slug> --entity <id> [--import <file> --origin-tool <name>] [--candidates N≤4] [--replace] [--finish] [--open]`

Preconditions (this command's own validator; neutral helpers from `lib/run_common.py`): registered project, verified 1.2 config with `qc.max_hero_attempts`, pin = 1.4, run lease, `resume_check`, active look for the entity (else "run look_run first"), no active headshot for the entity unless `--replace` (which makes the eventual receipt carry `supersedes_receipt_id`, existing mechanism, and prints the downstream-invalidation cost before anything runs).

**Mode A — import (`--import`)**. The file (JPEG/HEIC/PNG) is the writer's own generated image of the character:
1. `stage_reference_upload` (copy; the original is never consumed) → `prepare_reference_import(origin_class=imported_synthetic, origin_tool=…, entity_id=<entity>)`. **The import record binds the character** (R1#13): `import_record` gains `entity_id`; `synthetic_import_receipt` lookups and `_enforce_candidate` require the receipt's `entity_id` to equal the pending character, so one imported face can never be assigned to another entity. → `reference_import` gate request; **run state persisted** (step 6) before stopping; print the command; stop.
2. Next invocation resumes ONLY the tuple in run state (mode, normalized hash, request id, expected receipt kind): `finalize_reference_import`, then the imported candidate is **judged like any other** (R1#4: an "import" is just a file the agent could have produced; the judge catches pipeline mistakes routed around it, and a human override remains available), then the single-candidate pending packet (`imported_image_ref` + `qc_receipt_id`), checkpoint `awaiting_human`, `headshot_request`; print the command; stop.

**Mode B — casting inspiration.** Removed from `headshot_run` (R1#9); see D20.1 step 7.

**Mode C — generate (default)**.
1. `tools/prompt_builder.build_prompt(look, role="hero", palette)` once (recipe sealed as today).
2. **Affordability preflight** (R1#16), under the cost transaction: `remaining_hero_attempts × (generation estimate + judge estimate)` must fit under the cap or the run refuses to start or resume.
3. Generate candidates through the governed Seedream `text_to_image` path with `stage: headshots`, `look_refs`, `prompt_recipe`, plain background in a palette hue, `num_images: 1` per call so every candidate has its own reservation, attempt row and receipt. Attempts run inside the D19 pre-submit hook with series key `(entity, role="hero", look_hash, headshot_receipt_id=null, policy_bundle_sha256, builder_policy_sha256, generation endpoint/model, judge provider/model)`. **One cap, one key** (R1#5, #6): `qc.max_hero_attempts` is the ONLY hero limit, counted against a stable **hero budget key** `(project_id, entity_id, look_hash)` = every `attempt_started` row whose series key carries role `hero` and that look, across all subordinate series (builder/model/judge rotations open a new series for reuse semantics but never a new allowance). `lib/qc_receipts.start_attempt` gains `budget_key` + `budget_cap` and refuses when the budget count would exceed the cap; the verifier recomputes the same count.
4. **Judge every candidate** with the `hero` checklist (D20.3). Candidates that fail are not presented; the loop keeps generating until `--candidates` (default 4) **unique passing asset hashes** exist (R1#15: a duplicate output consumes an attempt but not a slot) or the cap is hit. Fewer at the cap is still presented (1..N); zero → `Blocked`, override request on the best failure, same as sheets.
5. Write the pending packet 1.1 (`candidates[].qc_receipt_id`, sealed `prompt_recipe` required), checkpoint `awaiting_human`, `headshot_request`; `--open` opens the candidates; print the selection command; stop.
6. **Durable run state** (R1#11): before any stop the command writes `headshot_packet.run_state[entity] = {mode, revision, normalized_pixel_hash?, request_id, expected_kind, checkpoint_digest}` into the checkpoint metadata (schema 1.1). A later invocation resumes exactly that tuple: it looks for a receipt of `expected_kind` whose record matches the persisted hash/request, and refuses anything else ("run state names request X; found none / found a different one").
7. **Reject-all** (R1#12): regeneration is allowed only after the command **consumes the declined request** the human produced at the gate (`.gate-requests/declined/<request_id>.json`, bound to the prior checkpoint digest in run state). The note is copied from that declined request's recorded note, never from the CLI; it is logged in the decision log (`category: revision`, verbatim). The prompt never changes; a note asking for a different appearance is refused with "that is a look change: edit the ticket and run look_run --supersede". Regeneration counts against the same hero budget.
8. After the human selects at the gate, the next invocation (or `--finish`) rewrites the packet `state: approved` for this character (approved entries so far + this one, each with `qc_receipt_id`), checkpoint `in_progress` (D18), rebuilds the canon view (`by-entity/<id>/hero.png`), prints `sheet_run.py …` as the next command.

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

**One verifier, four callers** (R1#3, same invariant as D19): `lib/headshot_verify.verify_headshot_candidate(project_dir, entry, candidate, *, active_look, config, pin)` checks lineage, look binding, sealed recipe (required for generated; forbidden for imported), import receipt entity binding (R1#13), and under a 1.4 pin the hero verdict: `require_candidate_qc` — passing or overridden hero verdict for that exact asset, look, policy bundle and judge, inside a compliant attempt chain within the hero budget. It is called (1) at checkpoint validation of any `headshot_packet` (pending and approved), (2) in the gate's `headshot_candidates`/`_enforce_candidate`, (3) as the gate's transactional `pre_commit_check` (re-run under the consumed token), and (4) by `lib.headshots.verify_headshot_ref` when a sheet call cites the hero. **The relied-on QC receipt id is signed into the headshot record** (`headshot_record` gains `qc_receipt_id`; the record digest therefore changes for 1.4 projects — 1.3 records stay valid under 1.3), so a later config/look change is detectable and the TOCTOU window closes the same way it did for sheets.

**Judge and series for role `hero`** (R1#8): `SheetJudge` and `verify_series_against_receipt` gain an explicit `role == "hero"` branch — `headshot_asset_id`/`headshot_receipt_id` must be **null** (no headshot exists yet; the hero is what is being chosen) and the generation receipt must carry `look_refs` for the entity and **no** `headshot_ref`; sheet roles keep the existing branch unchanged. For an imported candidate the "generation receipt" is the imported receipt (`generator_kind: imported`, no recipe): the series key's `builder_policy_sha256` is the literal `"imported"` sentinel and `generation_endpoint/model` are `"imported"`; the verifier accepts that shape only when the receipt is `imported` and its import receipt names this entity.

### D20.4 Config 1.2
`qc.max_hero_attempts` (required int 1..24; Bloodless will sign 8): the total hero generation budget per entity per look. CLI may only lower. Signed, no code default (R1#7). Bloodless re-signs once; that signature also carries policy bundle 1.1.

### D20.5 Shared run utilities (neutral only, R1#17)
`lib/run_common.py` holds only what has no precondition profile: `resolve_project_root(slug)` (registered, no symlink), `hold_lease(root, config)`, `gate_command(root, request_id)`, `request_state(root, request_id) → pending|done|declined`, `write_decision(...)`. Each command keeps its own explicit validator (`look_run`: config ≥1.0, pin ≥1.2; `headshot_run`: config 1.2, pin 1.4; `sheet_run`: config ≥1.1, pin ≥1.3). `sheet_run.py` is left unchanged in this plan.

### D20.6 Skill docs
`look-lock-director.md` and `headshots-director.md`: "run the command; never assemble packets or requests by hand; present only what the run produced". The hero checklist is added to `headshots-director.md` the way the sheet checklist was.

## Approach (build order)
1. Versions: authored-film 1.4 manifest; project_config 1.2 (dual-read 1.0/1.1/1.2); headshot_packet 1.1 (dual-read); `import_record` entity binding; tests.
2. `lib/run_common.py` (neutral utilities only).
3. Hero policy (bundle 1.1), evidence kinds, local rules, verdict schema role enum; `SheetJudge` + `verify_series_against_receipt` hero branch (null headshot, imported sentinel); hero budget key in `start_attempt`; `lib/headshot_verify.verify_headshot_candidate` + `require_candidate_qc`; wired into checkpoint validation, gate candidates/enforce/pre-commit, and `verify_headshot_ref`; `headshot_record` signs `qc_receipt_id`; tests (generated candidate without verdict refused; imported candidate judged; cross-entity import refused; budget counted across rotated series).
4. `look_run.py` (+ `--casting`) with tests on a temp wayfinder root.
5. `headshot_run.py` Mode A (import, judged) and Mode C (generate, judge, budget, unique slots, declined-request-driven reject-all, run state, finish) with fake generator/judge tests; `Blocked` + override.
6. Skill docs; Bloodless: config 1.2 + migration 1.4 (two signatures); then Sebastian: Ben answers the ticket → `look_run` → `headshot_run` → `sheet_run`.

## Key decisions & tradeoffs
- **Three commands, three gates, one entity at a time.** Matches D18 and the director skills; the commands are the skills' process sections made executable.
- **Imported heroes are judged too** (reversed after R1#4). An "import" is a file the agent could have produced; exempting it is a bypass. The writer can override a failed item at the gate.
- **The reject-all note never edits the prompt.** Same rule as D19: a note that asks for a different appearance is a look change and goes back through the ticket.
- **Hero attempt cap is its own number and its own key.** Sheets are $0.14 with a 3-attempt cap per role; heroes are $0.07 and need up to 4 passing candidates. The hero budget is counted per (entity, look) across every series so no field rotation refills it.
- **`look_run` is separate from `headshot_run`.** Ratifying a look is a writer's gate with no spend; mixing it into the paid command would let a headshot run start before the look is signed.

## Assumptions
1. Seedream `text_to_image` with `stage: headshots` is accepted by the governance boundary with `look_refs` + `prompt_recipe` and no `headshot_ref` (the boundary already treats `headshots` as a look-governed stage; verified in `_shared.verify_look_governance` — to re-confirm in step 5's first governed test).
2. The judge answers `hair_matches` / `age_matches` reliably enough given the look text; measured in step 2 on the four existing Ace headshot-era candidates if still in the vault, else on the first Sebastian run (advisory at first: `age_matches`/`build_matches` are warn-tier).
3. Bloodless's $100 cap has room (spent so far ≈ $1.85).

## Risks / open questions
- R1: Two more Bloodless signatures (config 1.2, migration 1.4). Acceptable; the cost of signed policy and signed manifests.
- R2: `headshot_record` digest changes under 1.4 (adds `qc_receipt_id`); Ace's 1.3-era headshot receipt stays valid because verification is pin-keyed.
- Q1: Should reject-all be allowed more than once per look? Proposal: yes, bounded by `max_hero_attempts`; the cap is the limit, not a reject counter.

## Out of scope
Locations; storyboards; changing the look_spec schema; automatic prompt repair; any Bloodless creative decision (Sebastian's look is the writer's ticket).
