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
