# Plan Review Log: location_run — the location imagery command (D21)
Started 2026-09-01 (afternoon PDT). MAX_ROUNDS=5. Reviewer: Codex CLI 0.146.0, model gpt-5.6-sol.

## Round 1 — Codex

The plan is not sound enough to implement. Material findings, ordered by severity:

1. **Critical — the location gate is not trust-equivalent to the sheet gate.** The plan only requires QC-receipt presence at signing ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:171)); the current location constructor merely hashes images and constructs a record ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:930)), unlike the sheet constructor’s checkpoint-digest binding, full verifier, and pre-commit recheck ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:649)).

   **Fix:** Make `_construct_location` require the current checkpoint digest and run the same `verify_location_plates` at display, decision reconstruction, and pre-commit under the approval lock.

2. **Critical — replacement authority is replayable and not human-signed.** D7 explicitly omits a location envelope and `supersedes_receipt_id` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:210)); `location` has no envelope contract ([receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:74)), while the fallback `canon_ruling` path trusts decision-log flags rather than a receipt chain ([canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:122)).

   **Fix:** Add a location envelope containing `action`, `entity_kind`, `look_hash`, and `supersedes_receipt_id`, and pre-commit-check the current location receipt tip.

3. **Critical — config 1.3 breaks existing hero and sheet flows.** The adoption order signs config 1.3 before migrating the manifest ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:182)), but `require_hero_qc()` currently accepts exactly version 1.2 ([project_config.py](/Users/ben/Projects/OpenMontage/lib/project_config.py:133)); headshot/sheet verification under 1.5 will therefore reject the new config.

   **Fix:** Replace exact-version checks with capability predicates so hero QC accepts both 1.2 and 1.3, with regression tests for headshot and sheet runs under config 1.3.

4. **Critical — imported location images lack explicit egress consent.** Location imports are sent to the OpenAI judge, but config consent only names `generated_sheet_images` ([project_config.schema.json](/Users/ben/Projects/OpenMontage/schemas/project_config.schema.json:295)), and the judge boundary requires that character-specific class ([\_shared.py](/Users/ben/Projects/OpenMontage/tools/video/_shared.py:1133)).

   **Fix:** Add a signed location-image egress class—including imported-synthetic plates—to config 1.3 and require it before every remote location judgment.

5. **High — authored-film 1.6 silently loses authored-film 1.5 batch behavior.** The plan extends only the look/QC/hero manifest sets ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:95)), while `_is_hero_batch_manifest` is keyed to the single tuple `("authored-film","1.5")` ([canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:65)).

   **Fix:** Introduce `HERO_BATCH_MANIFESTS` containing 1.5 and 1.6 and test every 1.5-specific packet, waiver, and record-version branch under 1.6.

6. **High — the proposed location series key cannot be represented correctly.** `SERIES_FIELDS` requires `headshot_receipt_id` and omits `look_receipt_id` ([qc_receipts.py](/Users/ben/Projects/OpenMontage/lib/qc_receipts.py:32)); the plan’s key omits the former and claims the latter is bound ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:67)), but it would be discarded from the signed/hash subset.

   **Fix:** Define an explicit versioned series schema by entity kind, require `headshot_receipt_id: null` for locations, and sign/verify `look_receipt_id` plus a strict allowed-role matrix.

7. **High — location verdicts fail the existing schema.** `qc_verdict` only permits `turnaround`, `expressions`, `wardrobe`, and `hero` ([qc_verdict.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/qc_verdict.schema.json:54)); the plan never adds `establishing` or `angle`.

   **Fix:** Version or extend the verdict schema for location roles and add schema round-trips for local failures, remote passes, remote failures, and reused verdicts.

8. **High — “angles” have no same-place continuity guarantee.** Every slot is independently text-to-image generated ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:149)), and the proposed checklist evaluates one image at a time; nothing binds an angle to the establishing plate, so three different buildings can all pass.

   **Fix:** Anchor generated angles through image edit/reference generation, bind the establishing asset/receipt into their series and verdicts, and judge each angle alongside the establishing plate for place identity.

9. **High — the claim that reference-import plumbing is location-generic is false.** Import records bind only `entity_id`, not `entity_kind` ([reference_import.py](/Users/ben/Projects/OpenMontage/lib/reference_import.py:346)); the gate validates against the union of character and location IDs ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:434)), and requests are hard-coded to stage `look_lock` with character wording ([reference_import.py](/Users/ben/Projects/OpenMontage/lib/reference_import.py:521)). Same-slug character/location imports can cross-bind.

   **Fix:** Version the import record to bind `(entity_kind, entity_id)`, parameterize stage and summary, and require the generation receipt’s attestation ID to equal that exact location import receipt.

10. **High — QC overrides are ambient authority, not cited authority.** The proposed entry stores verdict IDs but no override citations ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:106)); existing verification discovers any override by scanning the ledger ([verify.py](/Users/ben/Projects/OpenMontage/lib/sheet_qc/verify.py:113)), so the approval record does not identify which waiver and accepted items made a failed plate admissible.

    **Fix:** Store and seal per-slot `{qc_receipt_id, override_receipt_id, override_record_sha256, accepted_item_ids}` and verify each citation with `exact_approval`.

11. **High — visual-bible version negotiation permits breakage or downgrade.** Existing enforcement hard-requires visual bible 1.1 ([canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1868)), while the plan says both runners seed 1.2 and enforcement accepts both ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:111)). Unconditional 1.2 breaks older pins; accepting 1.1 under 1.6 creates a downgrade path.

    **Fix:** Seed and require bible 1.2 only under a signed 1.6 pin, retain 1.1 under older pins, and reject 1.1 outright when location QC is mandatory.

12. **High — the per-image metadata shape is contradictory and schema-incompatible.** The plan calls `prompt_recipe` singular in the schema but “per-image” in the runner ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:109)); current `ImageRef` already has strict provenance and rejects extra fields ([visual_bible.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/visual_bible.schema.json:91)). One recipe cannot describe establishing and multiple detail prompts.

    **Fix:** Define one canonical slot map keyed by `establishing|angle_n`, reuse `ImageRef.provenance`, and seal each slot’s generation receipt, recipe, QC authority, role, and exact palette.

13. **High — migration can strand pending gates.** The proposed migration guard checks approved locations only ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:99)); migrating while a sheet/location request is pending changes the signed pin, after which that request’s checkpoint tuple no longer passes `_checkpoint_pin()` ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:592)).

    **Fix:** Refuse config or pipeline migration while any governed request, `run_state`, `location_run_state`, or prior-pin `awaiting_human` checkpoint remains unresolved.

14. **High — import request IDs are not unique per slot.** The plan repeats `import-<E>-<rev>` for every image ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:143)); multiple imports in one location revision can collide with pending or previously completed request markers.

    **Fix:** Include slot and a durable monotonic sequence or asset prefix in every request ID, and enforce uniqueness across pending, done, declined, and abandoned directories.

15. **High — decline semantics do not prevent rejected assets from returning.** The plan discards the draft but records only burned attempts ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:164)); a cached passing verdict or identical generated pixels can therefore satisfy the next run again, unlike sheet-run’s rejected history ([sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:449)).

    **Fix:** Persist rejected location asset and verdict IDs per entity/look and exclude them from pass reuse, imports, overrides, and future slot selection.

16. **High — the state machine has no escape from partial exhaustion.** With one shared cap, establishing or early angles can consume all attempts; `--abandon` is defined only for a stale signed final request, leaving no governed way to cancel an intermediate import/override or reduce requested angles.

    **Fix:** Specify crash-safe intermediate states and an explicit abort/re-scope transition that clears drafts and requests while retaining spent attempts and rejection history.

17. **Medium — the preflight cost formula multiplies a shared cap twice.** Because attempts are shared across all slots, `images_needed × attempts_remaining` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:152)) can reserve three times the actual worst-case spend.

    **Fix:** Bound worst-case cost by the remaining global location attempts, partitioned between generation and judge costs according to completed/imported slots.

18. **Medium — the instruction layer will contradict the command.** The existing director still mandates four establishing candidates, a separate establishing approval, and 2–3 edit-derived angles ([visual-bible-director.md](/Users/ben/Projects/OpenMontage/skills/pipelines/authored-film/visual-bible-director.md:265)); the plan says no director update is needed.

    **Fix:** Version or update the visual-bible director, workflow, index, and 1.6 review criteria so agents are required to use `location_run` and its exact gate semantics.

19. **Medium — successful sealing can hide a failed canon projection.** Mirroring sheet finish likely copies its blanket suppression of `canon_view.build_view` errors ([sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:573)), yielding “approved” while board-facing links remain stale.

    **Fix:** Record projection as a retryable post-commit state, surface failures explicitly, and clear run state only after the canon view succeeds or a durable repair marker is written.

20. **Structural — copying another large runner multiplies trust-critical drift.** The config-version and manifest-profile omissions already demonstrate how separate runners diverge.

    **Fix:** Extract shared checkpoint/request/receipt/recovery/finish transactions into one version-aware visual-entry state machine, leaving character and location runners as small role-specific adapters.

The test list should explicitly cover every negative path above: stale/mutated checkpoints, gate pre-commit changes, old-receipt replay, same-slug cross-kind imports, 1.1 downgrade under 1.6, config-1.3 hero compatibility, 1.6 batch preservation, pending-gate migration, duplicate slots, declined-pass reuse, and crashes at every checkpoint/request/receipt transition. No files were modified.

VERDICT: REVISE
### Claude's response (→ revision 1)

Accepted in full: #1 (constructor rebuilt to sheet parity — Step 8), #2 (location envelope +
supersession, D7 reversed — Step 5), #3 (capability predicates replace exact-version config
checks — Step 3), #4 (`location_images` egress class in config 1.3, judge boundary enforced —
Step 3), #5 (`HERO_BATCH_MANIFESTS` = {1.5, 1.6} + full 1.5 matrix under 1.6 — Step 4),
#6 (versioned entity-kind-aware series schema, explicit `headshot_receipt_id: null`,
`look_receipt_id` in the hash subset, role matrix — Step 2), #7 (qc_verdict role enum extended —
Step 1), #8 (angles anchored via edit/reference generation from the establishing plate, anchor
bound into series + judged for place identity, D5 reversed — Steps 1/2/7), #9 (import record
binds (entity_kind, entity_id), stage/summary parameterized, attestation-id equality — Step 6),
#10 (per-slot sealed override citations verified via exact_approval — Step 5), #11 (bible 1.2
only under a 1.6 pin; 1.1 accepted under 1.6 only while locations empty; no downgrade — Step 5),
#12 (canonical slot map reusing ImageRef provenance, per-slot recipe/receipt/qc/role/palette —
Step 5), #13 (migration + config re-sign refused while any governed request/run-state/
awaiting_human is unresolved — Step 4), #14 (import ids `import-<E>-<slot>-<n>` with durable
monotonic sequence — Step 6), #15 (rejected asset AND verdict history persisted and excluded
from reuse — Step 7), #16 (`--abort` governed escape from any intermediate state + downward
re-scope — Step 7), #17 (cost formula fixed: cap is global, not per-slot — Step 7),
#18 (visual-bible-director-1.6 mandated, 1.5 director byte-frozen — Step 4; round-0 claim "no
director update needed" was wrong), #19 (canon-view projection failure surfaces via a durable
`projection_pending` marker; run state held until projection succeeds — Step 7).

Partially accepted: #20 — new Step 0 extracts the shared checkpoint/request/finish transaction
layer into `lib/visual_entry.py`, consumed by BOTH sheet_run and location_run (they share
checkpoint_visual_bible.json, where drift is fatal). REJECTED the further step of folding
headshot_run in: different stage file, different checkpoint, 25-test suite, in production use —
refactoring it now is risk without a shared-state payoff. Logged as future work.

## Round 2 — Codex

Revision 1 is substantially stronger, but several material trust and state-machine defects remain.

1. **High — the proposed envelope is invalid under the current receipt contract.** The plan uses `action: "supersede"` and describes `ENVELOPE_FIELDS["location"]` as a tuple ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:153)), while the receipt layer accepts only `activate|retire` and expects a required/optional field map ([receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:70)).  
   **Fix:** Use `action: "activate"` with optional `supersedes_receipt_id`, and define the location envelope as the same boolean field map used by headshots.

2. **High — adding a parent receipt does not itself prevent old-location replay.** The plan never specifies a strict active-location chain resolver or requires the bible’s `approval_receipt_id` to equal its unique tip; the existing generic location check accepts an exact historical receipt, while the available strict `_chain_tip` is pipeline-specific ([pipeline_pin.py](/Users/ben/Projects/OpenMontage/lib/pipeline_pin.py:127)).  
   **Fix:** Add `active_location_for()` with root-to-tip completeness/fork/cycle validation and require its tip at gate construction, pre-commit, finish, and canon enforcement.

3. **High — `exact_approval` cannot verify the expected envelope as the finish sequence claims.** The plan passes “envelope” conceptually at finish ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:216)), but the API checks only receipt ID, kind, entity, record digest, and source digest ([receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:588)).  
   **Fix:** Add an `expected_envelope` argument with exact canonical equality, or explicitly compare the returned signed envelope before any checkpoint mutation.

4. **High — angle verdicts remain replayable across establishing plates.** `establishing_asset_id` is added only to the attempt series, while verdict reuse is keyed by `TUPLE_FIELDS`, which currently omits the anchor ([qc_receipts.py](/Users/ben/Projects/OpenMontage/lib/qc_receipts.py:42)); therefore an angle judged beside plate A can be reused beside plate B.  
   **Fix:** Version the QC verdict/tuple contract and conditionally require and hash `establishing_asset_id` for location angles.

5. **High — generated-angle anchoring is asserted, not proven.** `_verify_location_series` only compares the series anchor with the entry ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:97)); it does not require the signed generation receipt’s `references_applied` to contain that establishing asset, even though receipts already record that field ([receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:757)).  
   **Fix:** Require a generated angle’s verified receipt to contain exactly the expected establishing reference, entity, and role before accepting its series or verdict.

6. **High — `SlotRef = ImageRef + fields` conflicts with the closed existing schema.** `image_ref` already has `additionalProperties: false` ([visual_bible.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/visual_bible.schema.json:91)), so extending it with QC, palette, override, and recipe fields through `$ref`/`allOf` will reject valid slots.  
   **Fix:** Define `SlotRef` as `{image: ImageRef, qc_receipt_id, …}` or refactor an open `image_ref_core` and close only the final composed object.

7. **High — the slot-map migration omits a silent-success consumer.** Step 8 updates Backlot, but not `canon_view.plan_view`, which still reads `location.establishing` and `location.angles` ([canon_view.py](/Users/ben/Projects/OpenMontage/lib/canon_view.py:70)); `build_view()` will succeed with no location links, causing `projection_pending` to clear incorrectly.  
   **Fix:** Inventory and update every old-shape consumer, including `canon_view`, `_iter_image_refs`, enforcement backstops, renderers, and their projection contract tests.

8. **High — the mutual single-flight predicate blocks its own resumptions as written.** Step 0 says any non-empty run state or pending import/location request blocks both runners ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:53)), but every legitimate resume has exactly such owned state.  
   **Fix:** Define an owner identity `(runner_kind, entity_id, run_id, request_id)` and exempt only the matching resumable transaction while treating every foreign owner as a blocker.

9. **High — `--abort` conflates human decline, failed-commit abandonment, and caller withdrawal.** Moving pending gates to `declined` fabricates a human decision, while `abandoned` currently means a consumed/stale signed request; doing either without the approval lock can race gate commitment ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1502)).  
   **Fix:** Introduce a distinct `withdrawn` terminal state, linearize it under the approval lock, and refuse abort once the request is done.

10. **High — preflight remains vulnerable to pin/config TOCTOU.** The proposed order reads config and pipeline pin before acquiring the project lease ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:183)), so a migration or config gate can commit while the runner waits and the runner then proceeds with stale objects.  
    **Fix:** Acquire the lease before authoritative preflight reads, or reload and revalidate config, pin, look, and checkpoint immediately after lease acquisition.

11. **Medium — the verifier is told to key policy behavior off a record version that the record does not contain.** The enumerated location approval record fields omit `record_version`, yet the next paragraph requires record-version-based verification ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:149)).  
    **Fix:** Add an explicit, strictly validated `record_version` and make both gate construction and enforcement dispatch solely from it.

12. **Medium — nothing prevents one image and one verdict from filling multiple angle slots.** The slot map constrains keys but specifies no cross-slot asset uniqueness or exact slot-key/role relation, allowing duplicate plates to satisfy `--angles N`.  
    **Fix:** Enforce distinct asset IDs across all slots and require `establishing.role == "establishing"` and every `angle_N.role == "angle"` in schema-adjacent validation.

13. **Medium — replacement storage semantics conflict with stable location IDs.** “The old entry is superseded” ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:221)) is ambiguous because retaining old and new entries under the same canon location ID violates uniqueness, while assigning a new ID breaks cast identity.  
    **Fix:** Specify in-place replacement of the single stable-ID entry, with history represented exclusively by the signed receipt chain and rejected-history metadata.

VERDICT: REVISE
### Claude's response (→ revision 2)

All 13 accepted. #1 envelope now uses the existing activate|retire vocabulary with optional
supersedes_receipt_id, boolean field-map shape (headshot pattern). #2 new active_location_for()
chain resolver (completeness/fork/cycle), tip equality required at construction, pre-commit,
finish, enforcement. #3 exact_approval gains expected_envelope with canonical comparison before
any checkpoint mutation. #4 verdict TUPLE_FIELDS versioned; angle tuples require+hash
establishing_asset_id. #5 generated-angle verification requires references_applied to contain
exactly the expected establishing asset (proven from the signed receipt). #6 SlotRef is
composition ({image: ImageRef, ...}), never extension of the closed image_ref. #7 slot-map
consumer inventory added (canon_view.plan_view named explicitly — the silent projection_pending
bug). #8 single-flight keyed by owner identity (runner_kind, entity_id, run_id, request_id);
own transaction resumable, foreign owners block. #9 --abort creates a distinct `withdrawn`
terminal state, linearized under the approval lock, refused once done. #10 lease acquired before
all authoritative preflight reads. #11 explicit record_version in the location approval record;
dispatch solely from it. #12 cross-slot asset uniqueness + slot-key/role equality validation.
#13 --replace is in-place replacement of the single stable-ID entry; history only in the receipt
chain + rejected metadata. Test matrix extended accordingly.

## Round 3 — Codex

Revision 2 resolves the prior findings, but several material defects remain:

1. **High — generated angles cannot pass prompt construction.** The plan calls `build_prompt(..., role="angle")` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:233)), but `LOCATION_ROLES` excludes `angle` and `build_prompt` rejects unknown roles ([prompt_builder.py](/Users/ben/Projects/OpenMontage/tools/prompt_builder.py:45), [prompt_builder.py](/Users/ben/Projects/OpenMontage/tools/prompt_builder.py:325)).  
   **Fix:** Add a governed `angle` role and framing—or explicitly map it to an existing role—while versioning the builder policy and regression-testing existing receipt hashes.

2. **High — `SlotRef` creates conflicting provenance authorities.** Its outer `role` and `import_receipt_id` duplicate `ImageRef.role` and `ImageRef.provenance.import_receipt_id` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:149)); only the outer role-to-slot-key relationship is specified, allowing different consumers to trust different values.  
   **Fix:** Remove the duplicated fields and derive them from `ImageRef`, or require exact equality in schema validation, gate reconstruction, finish, and canon enforcement.

3. **High — the stored prompt recipe is not bound to the generation receipt.** The proposed verifier checks receipt resolution, look, QC, and anchor agreement, but never requires `SlotRef.prompt_recipe` to equal the signed generation receipt’s recipe ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:184)); existing enforcement only validates the recipe inside the receipt ([canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1633)).  
   **Fix:** Require canonical equality between the slot recipe, its rendered hash, the approval record, and the signed generation receipt; forbid a generated slot without that binding.

4. **High — chain-tip validation conflates pre-sign and post-sign states.** Requiring the bible entry’s `approval_receipt_id` to equal the unique tip during gate construction and precommit ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:173)) is impossible for the proposed entry because its new receipt does not yet exist.  
   **Fix:** Pre-sign, validate the current entry against the old tip and the proposal’s parent against that tip; post-sign, install the new receipt ID and require it to be the new unique tip.

5. **High — “lease first” is not implementable through the current helper, and `sheet_run` remains vulnerable.** `hold_lease` requires an already loaded config ([run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:58)), while the plan requires acquiring it before loading config; meanwhile `sheet_run` still reads config and pipeline state before acquiring its lease ([sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:232)).  
   **Fix:** Introduce config-independent lease acquisition and move authoritative config, manifest, look, and resume reads under that lease in both location and sheet runners.

6. **High — withdrawn requests are omitted from request-ID uniqueness.** The plan introduces `withdrawn` as a terminal state but its uniqueness scan covers only pending, done, declined, and abandoned ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:203), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:242)), permitting ID reuse after recovery.  
   **Fix:** Include `withdrawn` in every request-path resolver, collision scan, recovery rule, terminal-state validator, and observability counter.

7. **Medium — abort’s “done means refuse” rule is overbroad for multi-gate runs.** A location run accumulates completed import gates before its final gate, so refusing abort whenever any owned request is already done can make abort unusable after the first approved import ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:245)).  
   **Fix:** Refuse only when the currently pending request loses the withdrawal race to `done`; preserve earlier consumed receipts without treating them as an abort blocker.

8. **High — the documented adoption flow cannot finish.** `--angles` defaults to two ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:211)), but the adoption sequence imports only `angle_0` while D3 says Sebastian ships with one angle ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:285), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:299)).  
   **Fix:** Start the example with `--angles 1` and persist an immutable requested-slot set so later resume/import invocations cannot silently change completion criteria.

VERDICT: REVISE
### Claude's response (→ revision 3)

All 8 accepted. #1 governed `angle` role added to LOCATION_ROLES with its own framing; builder
version bump; golden tests protect existing hashes (era-keyed). #2 SlotRef drops the duplicated
role/import_receipt_id — ImageRef/provenance is the single authority; slot-key↔ImageRef.role
equality validated. #3 generated slot's prompt_recipe must equal the signed generation receipt's
recipe canonically, and its rendered hash must match the approval record. #4 chain-tip check
split pre-sign (current entry = old tip; proposal cites that tip) vs post-sign (entry receipt =
new unique tip). #5 config-independent lease acquisition; sheet_run moved to the same lease-first
ordering via Step 0. #6 `withdrawn` included in every uniqueness/recovery/resolver/counter
surface. #7 abort refusal scoped to the currently pending request losing the race; consumed
import receipts are history, not blockers. #8 requested-slot set persisted immutably at run
start; adoption example corrected to `--angles 1`.

## Round 4 — Codex

Revision 3 closes the previous eight findings, but material defects remain:

1. **Critical — config re-signing can deadlock an active run.** The gate refuses config signing while run state exists ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:145)), yet changing live `project.yaml` invalidates the config digest required even to resume or abort; the current constructor reads that live file directly ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:523)).  
   **Fix:** Sign a staged `project.yaml.pending` and atomically promote it only after approval, leaving the previously signed config usable for transaction cleanup.

2. **High — the new orientation policy would fail open.** The plan specifies `orientation: landscape_or_square` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:75)), but `check_image` only implements `square_or_portrait`; unknown orientation values are silently ignored ([local_checks.py](/Users/ben/Projects/OpenMontage/lib/sheet_qc/local_checks.py:87)).  
   **Fix:** Implement `landscape_or_square`, reject every unknown orientation token, and test that portrait location images fail locally.

3. **High — adding `angle` to the prompt builder does not make the generation tool accept it.** `SeedreamImage.asset_role` remains a closed enum without `angle` ([seedream_image.py](/Users/ben/Projects/OpenMontage/tools/graphics/seedream_image.py:142)), and the generation boundary does not require an angle call to use edit mode with an establishing reference.  
   **Fix:** Extend the tool contract and enforce `asset_role=angle ⇒ operation=edit` with exactly one same-entity establishing reference before upload or reservation.

4. **Critical — replacement destroys the evidence needed for its own pre-sign check.** The plan writes the proposed draft into the stable-ID entry before signing ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:273)), while requiring comparison with the current approved entry ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:185)); unlike `sheet_run`, no `location_prev_entry` restoration contract is specified ([sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:404), [sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:458)).  
   **Fix:** Persist the exact previous approved entry and tip in transaction state, use them in gate/precommit checks, and restore them atomically on decline, abort, or abandonment.

5. **High — reused `qc_override` remains entity-kind confused.** Its constructor verifies only `entity_id`, not the request scope or verdict `entity_kind`, and the signed record omits kind ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:887)); a same-slug location request can therefore solicit authority over a character verdict.  
   **Fix:** Version location overrides to bind `entity_kind`, require request scope to equal the verdict’s `(entity_kind, entity_id)`, and make read-side lookup require the expected kind.

6. **Critical — `accepted_item_ids` is not explicitly bound to the override receipt.** `SlotRef` carries that caller-controlled set, while the actual single-verdict receipt records `item_ids`; `exact_approval` alone authenticates the receipt record hash but does not establish equality with the slot field ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:156), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:925)).  
   **Fix:** Require exact equality with `receipt.record.item_ids` plus matching QC receipt, asset, role, and entity kind, then prove every failing overridable item—and no other item—is covered.

7. **High — palette authority is duplicated again.** The bible already has a global palette, existing locations support `palette_override`, the new `SlotRef` adds another palette, and gate construction still calls `location_approval_record(entry, palette)` separately ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:156), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:275), [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:760)).  
   **Fix:** Choose one canonical palette source—prefer the existing bible palette or one entry-level override—and rebuild each generated prompt from that value during verification.

8. **High — the multi-import run state is still unspecified.** The adoption flow supplies a second import after the first gate and resume ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:306)), but a direct mirror of `sheet_run` ignores new operation flags whenever run state exists ([sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:248)); contradictory combinations such as `--abort --finish` are also undefined.  
   **Fix:** Define an explicit phase transition table with `awaiting_slot_input`, import gate, override gate, final gate, and projection states, and reject every flag combination not permitted in the current phase.

9. **High — `retire` is authorized without an implementation path.** The location envelope explicitly permits `retire` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:178)), but the CLI and state machine define no retirement request, record, finish, restoration, or post-retire chain behavior.  
   **Fix:** Restrict location envelopes to `activate` in 1.6, or specify and test retirement end-to-end before permitting that signed action.

VERDICT: REVISE
### Claude's response (→ revision 4)

All 9 accepted. #1 config gates sign a staged project.yaml.pending, atomically promoted after
approval; the live signed config stays valid for cleanup. #2 landscape_or_square implemented;
unknown orientation tokens REJECTED (fail-closed); portrait-fails-locally test. #3 SeedreamImage
asset_role enum extended; angle ⇒ edit-with-exactly-one-same-entity-establishing-reference
enforced before upload/reservation. #4 location_prev_entry contract (persist previous approved
entry + tip; compare against the persisted copy; atomic restore on decline/abort/abandon).
#5 location overrides versioned to bind entity_kind; scope must equal the verdict's
(entity_kind, entity_id); read-side lookup kind-checked. #6 slot accepted_item_ids must equal
receipt.record.item_ids with full receipt/asset/role/kind match + complete-coverage proof.
#7 SlotRef palette dropped; entry-level palette_override (else bible palette) is the single
authority, sealed once, prompts rebuilt from it at verification. #8 explicit phase table with
per-flag legality; contradictory flags rejected. #9 envelope narrowed to activate-only in 1.6;
retirement deferred wholesale rather than half-authorized.

## Round 5 — Codex (MAX_ROUNDS reached)

Revision 4 resolves the prior findings, but it is still not implementation-ready.

1. **Critical — signed abandonment breaks the location receipt chain.** A signed replacement becomes the unique receipt-chain tip, yet `--abandon` restores the previous entry ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:201), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:302)); the restored bible can never equal the new active tip, and unsigned `abandoned/` state cannot revoke signed authority.  
   **Fix:** After signing, make projection irrevocably recoverable from an immutable request snapshot, or require another signed superseding receipt—never restore the old entry.

2. **Critical — staged-config signing is not a crash-safe transaction.** “Promote after approval” leaves an uncovered receipt-commit→rename window, and the current config constructor has no pre-commit rehash while unbound config receipts lack tuple recovery ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:134), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:523), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1637)).  
   **Fix:** Bind a content-addressed pending file to the request, rehash it under the approval lock, and add an idempotent WAL/marker that recovers receipt commit plus exact-byte promotion before marking the request done.

3. **High — the proposed versioned location override conflicts with existing dispatch.** Any `qc_override` containing `record_version` currently routes to the batch validator, which accepts only version 1.1 ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:268), [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:394), [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:132)); a kind-bound single-verdict record will be rejected.  
   **Fix:** Specify an exact new record version and strict schema, then dispatch explicitly as absent→legacy single, 1.1→batch, new version→kind-bound single.

4. **High — the claimed builder-era isolation is impossible with the current global builder.** Adding `angle` and bumping `BUILDER_VERSION` changes the hash of the entire role map, while the paid-call boundary accepts only the current global version/hash ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:113), [prompt_builder.py](/Users/ben/Projects/OpenMontage/tools/prompt_builder.py:41), [prompt_builder.py](/Users/ben/Projects/OpenMontage/tools/prompt_builder.py:101), [_shared.py](/Users/ben/Projects/OpenMontage/tools/video/_shared.py:1551)); 1.5 character behavior and pass reuse will not remain frozen.  
   **Fix:** Introduce manifest/role-family builder dispatch with a byte-frozen 1.5 character builder and a separate 1.6 location builder/hash.

5. **High — final location request revisions have no durable allocator.** The plan publishes `location-<E>-<rev>` but never defines or persists `rev`; after decline or withdrawal, a location without an approved entry can reuse the terminal request ID ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:307)), unlike sheets’ durable revision metadata ([sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:171)).  
   **Fix:** Persist a monotonic `location_revisions[entity_id]` across every terminal outcome and bind the revision into run state, entry, approval record, and request ID.

6. **High — location judgments still have a character-specific system prompt.** The plan adds location checklists and bundle routing but never replaces the judge’s hard-coded `policy.SYSTEM_PROMPT`, which explicitly describes character sheets ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:67), [sheet_judge.py](/Users/ben/Projects/OpenMontage/tools/qa/sheet_judge.py:360), [policy.py](/Users/ben/Projects/OpenMontage/lib/sheet_qc/policy.py:95)).  
   **Fix:** Add a hashed `LOCATION_SYSTEM_PROMPT`, route through `system_prompt_for(role)`, and test the resulting prompt digest for both location roles.

7. **High — the palette contract remains internally contradictory.** The declared `SlotRef` still contains `palette`, the next paragraph says it carries none, and the approval record simultaneously describes per-slot palette fields and sealing it once ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:166), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:177), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:188)).  
   **Fix:** Remove palette from `SlotRef` and per-slot records, define one resolved entry-level palette field, and require CLI, prompt reconstruction, gate display, and approval hashing to use exactly it.

8. **Medium — the caller-lowered attempt cap is not persisted.** Only the requested slot set is explicitly immutable, so a multi-import resume can lose `--max-attempts N` and revert to the larger signed cap ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:243), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:247)).  
   **Fix:** Persist `effective_max_attempts` at run creation and reject any later invocation that changes or exceeds it.

9. **Medium — the pre-spend angle-anchor guarantee lacks trusted evidence.** Existing `reference_manifest` fields are caller-authored and hash-bind a file but do not prove it is this run’s QC-accepted establishing slot ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-09-01-location-run.md:119), [_shared.py](/Users/ben/Projects/OpenMontage/tools/video/_shared.py:1232)); the later receipt verifier cannot satisfy the promised pre-reservation protection.  
   **Fix:** Add a `location_anchor_ref` binding asset, entity, run/checkpoint, and passing QC receipt, and verify it against current run state before upload or reservation.

VERDICT: REVISE
### Claude's response (→ revision 5, applied POST-CAP)

The cap was reached with VERDICT: REVISE. There is NO unresolved disagreement: all 9 round-5
findings are accepted and were applied to the plan as revision 5 WITHOUT Codex re-review
(hero-override precedent, 2026-08-31). Dispositions: #1 post-signature restoration forbidden —
a signed replacement is the chain tip; recovery is projection-from-immutable-snapshot or a
further signed receipt; --abandon on a signed location request parks, never restores.
#2 staged config is content-addressed, request-bound, rehashed under the approval lock, with an
idempotent WAL/marker covering receipt-commit→exact-byte promotion. #3 explicit qc_override
dispatch: absent→legacy single, 1.1→batch, 1.2 (new, strict)→kind-bound single. #4 builder split
by role family: byte-frozen 1.5 character builder + separate 1.6 location builder/hash; boundary
validates per family. #5 durable monotonic location_revisions[entity] across every terminal
outcome, bound into state/entry/record/request-id. #6 hashed LOCATION_SYSTEM_PROMPT via
system_prompt_for(role), digest-tested. #7 palette contradiction removed: no palette in SlotRef
or per-slot records; one entry-level resolved palette used by CLI, prompts, gate display, and
approval hashing. #8 effective_max_attempts persisted at run creation; later invocations cannot
raise it. #9 location_anchor_ref (asset, entity, run digest, passing QC receipt) verified against
run state pre-upload/pre-reservation; references_applied stays the post-hoc proof.

**Closing note:** 5 rounds, 59 findings total (R1:20, R2:13, R3:8, R4:9, R5:9), zero rejected on
substance (R1#20 scoped down with logged rationale). Round-over-round the findings moved from
core trust-model gaps (R1) to transactional edge cases (R5), and round 5 raised no objection to
the architecture itself — the shape is settled; the remaining risk is implementation fidelity.
Handed to Ben with the options: build now, buy a confirmation round, or park.
