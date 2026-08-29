# Plan Review Log: D20 look_run + headshot_run
Phases 0-1 complete — plan locked with Ben 2026-08-28 (workflow first; the two steps before a sheet become commands). MAX_ROUNDS=5. Reviewer model: gpt-5.6-sol (config) — codex-cli 0.146.0.

## Round 1 — Codex

1. **Critical — D20 retrofits hero QC onto immutable manifest 1.3.** D19 defines 1.3 as sheet QC, while its `headshots` stage permits only `seedream_image`; `sheet_judge` belongs only to `visual_bible` ([D19 plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:23), [manifest](/Users/ben/Projects/OpenMontage/pipeline_defs/authored-film@1.3.yaml:173)). Changing runtime enforcement under the same signed pin silently changes an approved contract.  
   Fix: Introduce authored-film 1.4 with `sheet_judge` and hero-QC requirements in `headshots`, requiring a signed migration while preserving 1.3 behavior.

2. **Critical — the proposed pending packet is schema-invalid.** D20 writes `qc_receipts{asset_id: receipt}` ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:52)), but pending entries forbid that property via `additionalProperties: false` ([schema](/Users/ben/Projects/OpenMontage/schemas/artifacts/headshot_packet.schema.json:94)); the build plan mentions changing only the verdict role enum.  
   Fix: Version `headshot_packet` to 1.1 and add an explicit candidate-to-QC-receipt binding with dual-read tests.

3. **Critical — D19’s shared-verifier invariant is lost.** D20 adds QC only to `_enforce_candidate`, but `_construct_headshot` has no transactional `pre_commit_check`, `headshot_record` signs no QC receipt, and `verify_headshot_ref` never revalidates QC ([gate](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:804), [record](/Users/ben/Projects/OpenMontage/lib/headshots.py:28), [consumer](/Users/ben/Projects/OpenMontage/lib/headshots.py:129)); this contradicts D19’s gate/checkpoint/pre-commit contract and leaves a config/look TOCTOU race ([D19](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:62)).  
   Fix: Create one `verify_headshot_candidate` used at checkpoint validation, gate construction, transactional pre-commit, and downstream headshot verification, with the relied-on QC/override receipt signed into the headshot record.

4. **Critical — yes, an unjudged generated image can be approved through Mode A.** `--import` accepts any machine-generated file and caller-supplied `origin_tool`, after which D20 explicitly exempts it from QC ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:41)); nothing proves it was writer-generated elsewhere rather than a pipeline failure routed around the judge.  
   Fix: Judge every `imported_synthetic` hero under the same hero checklist and permit human override rather than exempting imports.

5. **Critical — the hero cap has two contradictory definitions.** D20 says `max_attempts_per_series × candidates`—up to 12—then calls the same limit `max_hero_attempts`, default 8 ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:50)); implementations can therefore choose the larger interpretation.  
   Fix: Define exactly one signed `max_hero_attempts` total, independent of requested candidate count, and pass only that value to attempt creation and verification.

6. **Critical — rotating series fields bypasses the claimed per-entity/per-look cap.** Attempts are counted per series, but the proposed series includes builder policy, generation model, and judge model, so changing any of them starts a fresh allowance even though D20 describes the cap as per entity and look ([QC series](/Users/ben/Projects/OpenMontage/lib/qc_receipts.py:32), [D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:76)).  
   Fix: Add a stable hero-budget key `(project, entity, look_hash)` and count every `attempt_started` across all subordinate series against it.

7. **High — the default cap would not be signed.** D20 makes `max_hero_attempts` optional with a code default so old signatures remain valid, but D19 requires the cap to live in signed configuration; current config 1.1 has `additionalProperties: false` and no such field ([schema](/Users/ben/Projects/OpenMontage/schemas/project_config.schema.json:189), [loader](/Users/ben/Projects/OpenMontage/lib/project_config.py:34)).  
   Fix: Add required `max_hero_attempts` in config 1.2 and require a new config approval before manifest 1.4 can run.

8. **High — the hero judge currently has a headshot chicken-and-egg failure.** `SheetJudge.execute` requires every character attempt to name the active headshot tip, but a hero candidate is judged precisely before any headshot exists ([judge](/Users/ben/Projects/OpenMontage/tools/qa/sheet_judge.py:295)); D20’s risk section mentions only changing `verify_series_against_receipt`.  
   Fix: Specify and test a `role == "hero"` branch across the judge and series verifier that requires both headshot fields to be null while keeping sheet roles unchanged.

9. **High — casting inspiration occurs at the wrong stage.** D20 requires an active ratified look before `headshot_run`, then claims a casting image imported there already informed the ticket ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:39)); D10 requires the question and import during look drafting, before ratification ([D10](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:56)).  
   Fix: Move `--casting` into `look_run`’s pre-ratification workflow and remove casting-image handling from `headshot_run`.

10. **High — Mode B can delete the user’s original file.** Unlike Mode A, Mode B calls `prepare_reference_import` without `stage_reference_upload`, while `normalize_staged_file` deletes whatever path it receives on every outcome ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:46), [implementation](/Users/ben/Projects/OpenMontage/lib/reference_import.py:197)).  
    Fix: Always copy casting inputs with `stage_reference_upload` and pass only the project-owned staging path to `prepare_reference_import`.

11. **High — gate-stop recovery has no unambiguous durable state.** “Next invocation detects the approved import receipt” does not say which receipt when several exist, and no persisted entity→hash→request-id binding is specified before stopping ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:43)); reference imports have no active per-entity chain.  
    Fix: Persist an atomic per-entity run state in checkpoint metadata binding mode, normalized hash, request ID, expected receipt, and revision, and resume only that exact tuple.

12. **High — reject-all can be simulated by the caller.** D20 accepts `--note` and regenerates but never requires or consumes the corresponding human-declined request, allowing an agent to discard candidates without a human reject-all ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:53)).  
    Fix: Permit regeneration only after consuming a declined request bound to the prior checkpoint digest, and copy its recorded note rather than trusting CLI text.

13. **High — imported receipts are not enforced as character-specific.** `synthetic_import_receipt` resolves solely by pixel hash, and `_enforce_candidate` checks that receipt without comparing its `entity_id` to the pending character ([import lookup](/Users/ben/Projects/OpenMontage/lib/reference_import.py:278), [gate](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:756)); one imported face can therefore be assigned to another entity.  
    Fix: Bind the character ID into the reference-import record and require an exact entity/hash/receipt match at selection and downstream verification.

14. **Medium — generated prompt recipes remain optional at the approval boundary.** The schema permits no `prompt_recipe`, and `_enforce_candidate` validates it only when present, allowing a generated headshot receipt with `prompt_recipe_sha256: null` despite the manifest’s structured-builder requirement ([schema](/Users/ben/Projects/OpenMontage/schemas/artifacts/headshot_packet.schema.json:101), [gate](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:726)).  
    Fix: Require a sealed recipe for model/local candidates and forbid it only for attested imports, enforced both structurally and in the shared verifier.

15. **Medium — duplicate passing outputs can produce a packet the gate refuses.** Reused identical assets consume attempts, but D20 counts “passes” rather than unique asset IDs; the existing gate rejects duplicate candidates ([gate](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:692)).  
    Fix: Count only unique passing asset hashes toward candidate slots while still charging every duplicate generation against the attempt cap.

16. **Medium — the D19 affordability guarantee is omitted.** D20 mentions a governed tracker but not the D19 worst-case preflight for remaining generation plus judge calls, so a run can start despite lacking budget to reach its gate ([D19](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:73), [D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:79)).  
    Fix: Under the cost transaction, reserve-check `remaining_attempts × (generation estimate + judge estimate)` before starting or resuming.

17. **Medium — the proposed shared refactor has incompatible precondition profiles.** `look_run` permits config 1.0/pin ≥1.2/no QC, while `headshot_run` and `sheet_run` require QC config and pin 1.3+, so `open_project(...with every precondition)` cannot safely serve all three and unnecessarily risks the approved D19 runner.  
    Fix: Extract only neutral root/lease/request utilities, keep explicit command-specific validators, and leave `sheet_run` unchanged until characterization tests prove equivalence.

VERDICT: REVISE

### Claude's response (revision 2)
All 17 accepted. #1/#7 authored-film 1.4 + config 1.2 with required max_hero_attempts (no code default); #2 headshot_packet 1.1 with candidates[].qc_receipt_id, run_state, required recipe for generated; #3 lib/headshot_verify used at checkpoint validation, gate candidates/enforce, pre-commit and verify_headshot_ref, qc_receipt_id signed into headshot_record; #4 imported heroes judged (design reversed); #5/#6 one signed cap counted against a stable (project, entity, look_hash) hero budget key across all series; #8 explicit hero branch (null headshot; imported sentinel); #9 --casting moved to look_run, refused after ratification; #10 stage_reference_upload always; #11 durable run_state tuple; #12 reject-all consumes the declined request and copies its note; #13 entity_id bound in import record and checked at selection/downstream; #14 sealed recipe required for generated candidates; #15 unique passing hashes fill slots; #16 affordability preflight; #17 run_common holds neutral utilities only, sheet_run untouched.

## Round 2 — Codex

Revision 2 materially resolves 12 of 17 findings. Prior findings #1, #3, #9, #11, and #12 remain incomplete in the following ways:

1. **Critical — authored-film 1.3 is not actually preserved.** D20 says 1.3 remains untouched, but adding `hero` to the single global policy bundle changes `policy.bundle_sha256()` and blocks every existing 1.3 config; updating the shared `headshots-director` also gives 1.2/1.3 projects instructions for a command that refuses their pin ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:25), [policy](/Users/ben/Projects/OpenMontage/lib/sheet_qc/policy.py:109), [manifest](/Users/ben/Projects/OpenMontage/pipeline_defs/authored-film@1.3.yaml:173)).  
   Fix: Preserve executable policy 1.0 and the legacy director, and give manifest 1.4 an explicitly versioned policy bundle and director path.

2. **Critical — `run_state.checkpoint_digest` is self-referential.** D20 stores the checkpoint digest inside the checkpoint being digested, but `checkpoint_digest()` hashes the complete file bytes; writing the digest changes those bytes, so no stable value exists ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:59), [implementation](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:188)).  
   Fix: Write the checkpoint without its own digest, compute the digest afterward, bind it in the gate request, and keep only request/revision identifiers in run state.

3. **Critical — old headshots cannot both remain valid and satisfy 1.4 QC.** D20 claims Ace’s 1.3 receipt remains valid after migration, but old headshot records contain no QC receipt or record version, `active_headshots()` is not pin-aware, and the new downstream verifier would require QC under the current 1.4 pin ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:114), [headshot record](/Users/ben/Projects/OpenMontage/lib/headshots.py:28)).  
   Fix: Make migration to 1.4 require every active legacy hero to be judged and reapproved, or define an explicit signed grandfather receipt and document the resulting exception.

4. **High — the fourth verifier call is not reachable from its available data.** `verify_headshot_candidate` requires a packet entry and candidate, while `verify_headshot_ref` receives only `{entity_id, asset_id, approval_receipt_id}` and the active receipt record lacks the full candidate/ImageRef and recipe ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:81), [consumer](/Users/ben/Projects/OpenMontage/lib/headshots.py:129)).  
   Fix: Split selection-time and active-record verification, with the latter reconstructing from explicitly signed generation/import/QC receipt IDs and recipe hash.

5. **High — the casting-import workflow never reaches finalized state.** `look_run --casting` stops after `prepare_reference_import`, but the plan specifies no durable casting run state or subsequent `finalize_reference_import`; its statement that no command opens the file again is incompatible with finalization’s required hash verification and move ([D20](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:38), [finalizer](/Users/ben/Projects/OpenMontage/lib/reference_import.py:472)).  
   Fix: Persist the casting operation, resume only its approved receipt, finalize it, and only then permit look ratification; prohibit model/director exposure rather than all subsequent file reads.

VERDICT: REVISE

### Claude's response (revision 3)
All five accepted: #1 versioned frozen policy bundles (1.0 hash-pinned by test) and a separate 1.4 director file; #2 run_state carries no self-digest, the checkpoint digest binds into the gate request after the write; #3 explicit headshot_grandfather receipt (judge + one signature) required before migration to 1.4, or --replace; #4 two verifiers over one rule set (candidate at selection time; active record downstream from signed receipt ids, record 1.1); #5 casting import has run state in the look_lock checkpoint, is finalized on resume, and the prohibition is scoped to model/director/text exposure with integrity reads allowed.

## Round 3 — Codex

1. Critical — Revision 3 still contradicts backward compatibility: [line 80](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:80) says every 1.3 project must re-sign, while [line 103](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:103) modifies the shared legacy director despite promising a separate 1.4 file.
   Fix: Require bundle 1.1 only for migrating/1.4 projects and update only `headshots-director-1.4.md`, with golden tests preserving bundle 1.0 and the legacy director.

2. High — `look_run` excludes config 1.2 at [line 33](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:33), so the required 1.4/config-1.2 workflow cannot start.
   Fix: Make `look_run` accept every verified config version ≥1.0, including 1.2.

3. Critical — Both finish paths are ordered behind fresh-run guards: approving a look creates the active look rejected by [line 36](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:36), and selecting a headshot creates the active headshot rejected by [line 46](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:46).
   Fix: Resume or finish a persisted request before applying `--supersede`/`--replace` checks, which should govern only new runs.

4. Critical — Grandfather migration deadlocks: `headshot_run` requires pin 1.4, but migration to 1.4 requires grandfathering first; moreover, the plan adds the coverage check only to request preparation, while the gate reconstructs migration approvals independently.
   Fix: Permit only `--grandfather` under pin 1.3 and enforce complete, currently valid grandfather coverage in both the migration gate constructor and its transactional pre-commit check.

5. High — Casting/import recovery remains race-prone: the plan publishes the import request before durable casting state, while [reference_import.py](/Users/ben/Projects/OpenMontage/lib/reference_import.py:395) derives request IDs solely from a truncated asset hash, allowing another entity using identical pixels to overwrite the pending request.
   Fix: Persist state first, use an entity/mode/revision-bound request ID, atomically refuse existing requests and cross-entity hash reuse, then publish a checkpoint-digest-bound request.

VERDICT: REVISE

### Claude's response (revision 4)
All five accepted: #1 bundle 1.1 only for 1.4 projects, legacy director byte-identical (golden tests); #2 look_run accepts every verified config version; #3 resume/finish runs before the --supersede/--replace guards in both commands; #4 --grandfather is the one headshot_run mode allowed under pin 1.3 and grandfather coverage is enforced in prepare_migration_request, the migration gate constructor and its pre-commit; #5 run state persisted before the request, entity/mode/revision-bound request ids, no-overwrite and cross-entity hash refusal under lock, request bound to the checkpoint digest.

## Round 4 — Codex

Round-3 fixes are present, but four material issues remain.

1. Critical — Resume checks accept any historical `done/` request for the entity, but done requests persist and look-lock IDs are reused, so old approvals can shadow every future `--supersede` or `--replace` run.
   Fix: Resume only the exact request ID stored in active run state, then atomically clear that state or mark the request consumed after finishing.

2. Critical — “State first” cannot persist the normalized hash required for recovery because [prepare_reference_import](/Users/ben/Projects/OpenMontage/lib/reference_import.py:366) both calculates that hash and publishes the request; updating state afterward also changes the checkpoint digest already bound to the request.
   Fix: Split normalization/staging from request publication, write the complete hash/kind/request tuple, compute its checkpoint digest, then atomically publish the request.

3. Critical — A grandfather receipt superseding the legacy headshot becomes the chain tip, while [verify_headshot_ref](/Users/ben/Projects/OpenMontage/lib/headshots.py:129) requires references to name that tip; every existing sheet’s legacy `approval_receipt_id` therefore becomes stale despite “grandfathering.”
   Fix: Make grandfathering an adjunct attestation keyed to the unchanged legacy tip, or explicitly migrate and reapprove every dependent sheet.

4. High — Switching the single config policy hash from bundle 1.0 to 1.1 invalidates all existing D19 sheet verdicts, because D19 accepts only the currently pinned whole-bundle hash; the migration covers legacy heroes but not legacy sheets.
   Fix: Pin sheet and hero policy hashes independently, or require rejudging and reapproving every existing sheet during 1.4 migration.

VERDICT: REVISE

### Claude's response (revision 5)
All four accepted: #1 resume only the exact request id in run state, state cleared atomically with the outcome write, done/ never scanned; #2 prepare_reference_import split into normalize (hash first) and publish (request bound to the checkpoint digest computed after run state is written); #3 grandfather is an adjunct attestation of the unchanged legacy tip (envelope attests_receipt_id), never a supersession, so existing sheets stay valid; #4 sheet and hero policy bundles pinned independently (config 1.2: policy_bundle_sha256 = sheet, hero_policy_sha256 = hero), so no D19 sheet verdict is invalidated.

## Round 5 — Codex (final round)

Three material blockers remain.

1. Critical — Ordinary `look_lock` requests never persist run state: [line 36](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:36) permits exact-request completion only through run state, but [lines 37–38](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:37) publish and later scan `done/` without creating it; the post-casting look request has the same gap.
   Fix: Persist a digest-bound `look_lock` run-state tuple before every look request, including the post-casting transition, and finish only that tuple.

2. High — The normalize→state→publish sequence still has an unrecoverable crash window: after state is committed but before publication, the next invocation finds neither a request nor receipt, and the specified logic refuses rather than safely publishing.
   Fix: Define exact recovery states: missing request republishes after verifying the staged hash, pending waits, done finalizes, declined consumes, and a mismatched request fails closed.

3. High — The build instructions still contradict the independent-policy design: [line 27](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:27) defines separate sheet/hero hashes, while [lines 64, 97, and 106](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:64) still prescribe a combined/versioned “bundle 1.1,” and config 1.2’s required additions name only the cap.
   Fix: Remove every 1.0/1.1 combined-bundle instruction and make both `qc.hero_policy_sha256` and `qc.max_hero_attempts` required in config 1.2 and its schema.

VERDICT: REVISE

### Claude's response (revision 6)
All three accepted and folded in: #1 look_lock run state before every request (incl. post-casting), finish only that tuple; #2 exhaustive recovery states (missing → republish after hash check, pending → wait, done → finalize, declined → consume, mismatch → fail closed) for look, import and casting; #3 every combined-bundle wording removed; config 1.2 requires both hero_policy_sha256 and max_hero_attempts.

## Resolution
MAX_ROUNDS=5 reached without an APPROVED line. No disagreement remains: every finding across five rounds was accepted; the last three were consistency/recovery items and are in revision 6, unreviewed. Handed to Ben: accept as-is, or run one extra round.

## Round 6 — Codex (extra round, authorised by Ben)

Most round-5 fixes are present, but three material issues remain.

1. Critical — `look_packet.run_state` is schema-invalid: look packet 1.0 permits only `version`, `looks`, `complete`, and `metadata`, with `additionalProperties: false`.
   Fix: Store it consistently as `look_packet.metadata.run_state` or checkpoint metadata, or version the look-packet schema.

2. Critical — The “exhaustive” done-state action always builds the look packet, but a casting run’s `expected_kind` is `reference_import`; applying that action skips or deadlocks the required finalize→look-lock transition.
   Fix: Make recovery mode-specific: `look_lock` done builds the packet; casting done finalizes and atomically transitions to a new look-lock run state/request.

3. High — Generated Mode C still lacks missing-request recovery: [line 60](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:60) refuses when no request exists, so a crash after checkpointing judged candidates but before publishing their selection request is unrecoverable.
   Fix: Apply the same five-state recovery table to headshot selection, republishing only after revalidating the checkpoint digest, candidate hashes, and QC receipts.

VERDICT: REVISE

### Claude's response (revision 7)
All three accepted: #1 run state lives in checkpoint metadata (no artifact schema change); #2 done-state finish is mode-specific, casting done = finalize + atomic transition to a look_lock run state + request; #3 headshot selection has its own run state and the five-state recovery table, republishing only after re-validating digest, candidate hashes and QC receipts.

## Resolution (final)
Six rounds, 40 findings, all accepted, none disputed. Revision 7 unreviewed. Ben decides: build or another round.

## Build record — steps 1–3 (2026-08-29)

Built from revision 7 by Claude; Ben asked for steps 1–3 first, then a checkpoint before the two commands.

- Step 1 `5643fef`: manifest 1.4 (+ `headshots-director-1.4.md`, 1.3 and its director byte-frozen by golden test), separate HERO policy bundle (sheet bundle hash unchanged: `f3709bd4…`), project_config 1.2, headshot_packet 1.1, headshot record 1.1, `normalize_reference_import` / `publish_import_request` split with character binding in the record.
- Step 2 `950e1af`: `lib/run_common.py` (neutral utilities only) + `revision` decision category.
- Step 3 `97d0540`: hero budget in the QC stream, hero series branch, `lib/headshot_verify.py` (both verifiers + `require_hero_verdict` + `hero_migration_blockers`), judge hero branch, `qc_call_context` hero acceptance, enforcement and gate wiring (record 1.1, `headshot_grandfather` constructor, migration coverage at request / construct / pre-commit), downstream verification in `verify_headshot_ref`.
- Tests: `tests/lib/test_d20_versions.py`, `test_run_common.py`, `test_d20_hero_qc.py` (35 new). Full suite 1796 passed, 11 skipped.

Departures from the plan text (all within its intent; flagged for the post-build inspection):
1. D20.0 still mentions `run_state` inside headshot_packet 1.1; revision 7 moved it to checkpoint metadata (R6#1), so the packet schema carries no run_state.
2. The reference_import gate REQUEST keeps its hash-derived `entity_id` key (Slice A #12); the character binding lives only in the signed record (`entity_id`). The gate verifies the binding against the proposal cast.
3. The `headshot_grandfather` request carries `qc_receipt_id` as a request field (like `pipeline_version`), never as a record hint; the gate constructs the whole record.
4. Hero local rules live in `HERO_LOCAL_RULES`, outside `LOCAL_RULES`, so the sheet bundle bytes are untouched.
5. A hero series that does not match the sealed receipt (e.g. a generated-style series over an imported receipt) is refused at judge preflight; the attempt row still counts against the budget (same rule as sheets).
6. Egress: hero judging reuses the `generated_sheet_images` consent class for the judge provider (an imported_synthetic candidate is machine-generated elsewhere). No new consent class was added; if Ben wants imports under their own class, that is a config 1.2 schema change.
7. `hero_migration_blockers` needs a verified 1.2 config only when a cast character already has an active hero; a project with no heroes may pin 1.4 with any config (the command validators demand 1.2 before any hero run).

Not built yet (steps 4–6): `look_run.py`, `headshot_run.py`, the look-lock director pointer, Bloodless signatures, Sebastian.

## Post-build inspection — steps 1–3, round 1 (2026-08-29, fresh read-only Codex session 01a04e76…)

### Codex findings (verbatim)

1. **Critical — Grandfathered heroes cannot complete a 1.4 headshot packet.** [lib/canon_enforcement.py:1731](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1731)  
   Scenario: Ace receives the intended `headshot_grandfather` receipt, Bloodless migrates to 1.4, then Sebastian is approved. Completing the packet requires an Ace entry, but enforcement first treats Ace’s grandfather verdict as a normal attempt and then unconditionally rejects his 1.0 record. The advertised grandfather path is therefore unusable when extending the cast.  
   Fix: For an approved 1.0 entry, bypass candidate-time verification and accept it through `verify_active_headshot`, matching the packet QC ID to the grandfather verdict.

2. **High — Sheets can be approved against a hero that is invalid under the current hero policy or budget.** [scripts/gate_approve.py:557](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:557)  
   Scenario: a hero passes under policy H1 with cap 8; the project then signs config H2 or lowers the cap below that attempt. `verify_active_headshot` would reject it, but both the sheet gate and checkpoint path ([lib/canon_enforcement.py:1855](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1855)) only compare the active asset/tip and sheet verdict, so an existing sheet can still be signed under 1.4.  
   Fix: Make the shared sheet verifier call `verify_active_headshot` under authored-film 1.4 before accepting any character sheet.

3. **High — Unjudged or foreign candidates are shown at the selection gate.** [scripts/gate_approve.py:731](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:731)  
   Scenario: after checkpoint creation, replace a candidate with valid local bytes and any nonempty `qc_receipt_id`, or replace the packet with version 1.0. `headshot_candidates()` checks only schema, uniqueness, and bytes before displaying all candidates; semantic hero verification happens only after the human chooses one. Signing eventually fails, but the explicit “every candidate presented is judged” contract is already violated.  
   Fix: Resolve the pin/config/look and run `verify_headshot_candidate` on every candidate inside `headshot_candidates()` before returning it for display.

4. **High — The transactional headshot recheck retains a stale supersession tip.** [scripts/gate_approve.py:901](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:901)  
   Scenario: two approvals for the same character construct while receipt R0 is active. The approval lock serializes commits, but the second pre-commit callback rechecks only candidate QC; after the first commits R1, the second can still commit R2 claiming to supersede R0, corrupting the unique-tip chain and blocking all later headshot/sheet reads.  
   Fix: Inside the approval lock, reconstruct and compare the complete record and envelope, including current tip, checkpoint digest, candidate bytes, pin, and config.

5. **High — Migration coverage trusts every 1.1 record without verifying it.** [lib/headshot_verify.py:343](/Users/ben/Projects/OpenMontage/lib/headshot_verify.py:343)  
   Scenario: a formerly 1.4 project downgrades to 1.3, changes its hero policy/cap, loses a raw judge response, or otherwise invalidates its active 1.1 hero. Re-pinning to 1.4 succeeds because migration coverage skips that record entirely, despite `verify_active_headshot` rejecting it.  
   Fix: Run `verify_active_headshot` for every active hero; let that verifier distinguish valid 1.1 records from grandfathered 1.0 records.

6. **Medium — A permitted duplicate import can invalidate an existing imported hero.** [lib/reference_import.py:286](/Users/ben/Projects/OpenMontage/lib/reference_import.py:286)  
   Scenario: pixels are imported for character A as receipt R1 and become a generation receipt bound to R1. Re-publishing for the same character is explicitly allowed; once R2 is approved, `synthetic_import_receipt()` returns latest-wins R2, so candidate and active-headshot verification compare R2 against immutable generation provenance R1 and reject the previously valid hero.  
   Fix: Resolve and validate the exact attestation receipt named by the generation receipt instead of selecting the latest receipt for the hash/entity.

Test note: pytest could not create a temporary directory in the enforced read-only environment. No files were modified; the sheet bundle hash remains `f3709bd44037…`.
### Claude's dispositions — all six accepted, fixed in one commit

1. Accepted. `_check_headshots` now verifies a grandfathered (1.0) hero through `verify_active_headshot` and requires the entry's `qc_receipt_id` to be the attested verdict; `verify_headshot_candidate(require_verdict=False)` runs every other check. Test: approved 1.1 packet carrying the grandfathered hero accepted; a foreign verdict id refused.
2. Accepted. `verify_character_sheet` takes `pin` and, under 1.4, calls `verify_active_headshot` before accepting any character sheet (checkpoint write and gate; the gate also demands the 1.2 config under 1.4). Test: unattested legacy hero refused under 1.4, accepted under 1.3.
3. Accepted. `headshot_candidates` (the display path) verifies the packet version and every candidate under 1.4 before returning the list. Test: tampered candidate refused at display, before selection.
4. Accepted. `_construct_headshot`'s pre-commit (all pins) re-derives the supersession tip, the checkpoint digest, entry and candidates, and under 1.4 the hero verdict. Test: a record constructed with no active hero refuses to commit after another approval made one.
5. Accepted. `hero_migration_blockers` verifies every active hero (1.1 included). Test: a 1.1 hero whose raw judge response vanished blocks the 1.4 pin.
6. Accepted. `synthetic_import_receipt(receipt_id=…)`: every consumer (lineage, imported generation, gate enforcement, both hero verifiers) resolves the exact attestation the generation receipt names, never the latest for the hash. Test: a permitted re-import for the same character leaves the first-bound hero valid.

Full suite after fixes: 1799 passed, 11 skipped. Round 2 (re-inspection) pending Ben's call.

## Build record — steps 4–6 (2026-08-29)

- Step 4 `cfd5dd3`: `scripts/look_run.py` — resolved ticket located by its `## Look spec` entity; run state in `checkpoint_look_lock.json` `metadata.run_state[entity]` written BEFORE every request; five-state recovery (missing → republish after hash check; pending; done → mode-specific finish; declined → decision log + clear; mismatch → fail closed); `--supersede`; `--casting` with the casting→look transition as one checkpoint write. `look_lock_request` gained `source_checkpoint_digest`. 9 tests.
- Step 5 `a9c43b5`: `scripts/headshot_run.py` — Mode C (build once, generate under the pre-submit hook with the hero budget, judge every candidate, unique passing assets fill the slots, fewer at the cap still presented, zero → Blocked + override request), Mode A (import → attested → finalized → judged like any other → presented alone), `--grandfather` (legacy hero judged under a `grandfather: true` series, attestation request), run state modes import/select/grandfather with the five recovery states (select republishes only after re-validating digest, candidate hashes and every verdict), reject-all consumed from the declined request (note logged verbatim; rejected candidates excluded from reuse; a note with appearance words refused as a look change), `--replace` prints the downstream cost, finish rewrites the approved packet 1.1 and rebuilds the canon view. 15 tests.
- Step 6 `20ef019`: look-lock director points at `look_run.py` for ingest and casting; `headshots-director-1.4.md` shipped in step 1.
- Full suite after steps 4–6: 1823 passed, 11 skipped.

Departures / judgement calls (for the inspection):
1. Reject-all "look change" detection is a conservative word list (`LOOK_CHANGE_WORDS`), not semantics; the director doc tells the writer where appearance changes go.
2. Approved entries of earlier characters survive the next character's pending phase in `checkpoint_headshots.json` `metadata.approved_entries` (a packet is either pending or approved); the approved packet is rebuilt at finish.
3. Hero candidates are generated at 1024×1280 (portrait) with a plain background hue from `--palette` (default "neutral grey"); the sheet-era palette is not required for a hero.
4. `headshot_run --grandfather` is allowed under pin 1.3 and 1.4 (the judge call context accepts 1.3 only for a grandfather series).

Not done: Bloodless signatures (config 1.2, Ace grandfather, migration 1.4) and Sebastian — those are Ben's gates, after the inspection.
