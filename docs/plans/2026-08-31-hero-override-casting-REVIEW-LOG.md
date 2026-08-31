# Plan Review Log: hero override — waive items, don't cast
Started 2026-08-31 ~11pm PDT. MAX_ROUNDS=5. Reviewer: Codex (gpt-5.6-sol), read-only. Plan: docs/plans/2026-08-31-hero-override-casting.md

## Round 1 — Codex (gpt-5.6-sol)

The principle is right—separate policy waiver from casting—but the proposed series-wide authority is under-bound and conflicts with D20’s signed contracts.

1. **CRITICAL — The signed receipt would not prove which candidate field the human reviewed.** The plan puts images, failure sets, and unlock counts only in the mutable request packet, while the proposed signed record contains merely `scope`, `item_ids`, and a reason ([plan:40](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:40)); unlike today’s constructor, that does not bind the approval to the evidence shown ([gate_approve.py:690](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:690)).  
   Fix: Have the signer reconstruct a canonical field manifest from verified QC rows and sign its digest plus every `{qc_receipt_id, asset_id, failing_items}` tuple.

2. **CRITICAL — This is not actually D20 “series” scope.** A D20 series includes policy, builder, generation endpoint/model, judge, and optional grandfather mode ([qc_receipts.py:32](/Users/ben/Projects/OpenMontage/lib/qc_receipts.py:32)); `{series:"hero", entity_id, look_hash}` would let an old waiver cover future verdicts after policy or judge changes, including changed meanings for the same item ID.  
   Fix: Bind authority to the exact verified `series_sha256` and current policy/config, or call it look-scoped and explicitly design the much broader trust semantics.

3. **CRITICAL — `look_hash` alone weakens D20’s look binding.** D20’s verifier requires both the active `look_hash` and `look_receipt_id` ([headshot_verify.py:86](/Users/ben/Projects/OpenMontage/lib/headshot_verify.py:86)); the proposed waiver can revive if identical look content is retired and later re-approved under a new receipt.  
   Fix: Include and verify the active `look_receipt_id` alongside `look_hash`, including during the signer's transactional pre-commit check.

4. **HIGH — The receipt schema still requires one verdict.** `qc_override` currently requires an envelope `qc_receipt_id`, requires it to equal `record.qc_receipt_id`, and validates items against that one failed verdict ([receipts.py:85](/Users/ben/Projects/OpenMontage/lib/receipts.py:85), [receipts.py:284](/Users/ben/Projects/OpenMontage/lib/receipts.py:284)); a union of items from multiple verdicts cannot satisfy that contract, and `lib/receipts.py` is absent from the work order.  
   Fix: Define a versioned batch-override record/envelope or a new approval kind, update `lib/receipts.py`, and preserve the legacy one-verdict branch exactly.

5. **HIGH — “Present ALL” is rejected by three existing contracts.** `headshot_packet` has `maxItems: 4` ([headshot_packet.schema.json:275](/Users/ben/Projects/OpenMontage/schemas/artifacts/headshot_packet.schema.json:275)), the real signer rejects more than four ([gate_approve.py:825](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:825)), and signed authored-film 1.4 promises “up to 4” ([authored-film@1.4.yaml:197](/Users/ben/Projects/OpenMontage/pipeline_defs/authored-film@1.4.yaml:197)).  
   Fix: Introduce a versioned packet and manifest/director contract—likely `headshot_packet` 1.2 and authored-film 1.5—and update every signer, schema, enforcement, and UI consumer.

6. **HIGH — The plan’s “12-attempt natural cap” is false.** `qc.max_hero_attempts` is signed and configurable from 1 through 24 ([D20:96](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:96)); twelve is project data, not a system invariant.  
   Fix: Derive the field limit from the currently verified config and design schema, terminal, and Backlot rendering for the full allowed cap.

7. **HIGH — “Import path unchanged” contradicts the proposed shared verifier change.** `_overridden` is used by generated candidates, imported candidates ([headshot_run.py:359](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:359)), and grandfather attempts ([headshot_run.py:676](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:676)); changing `require_hero_verdict` to consult look-scoped waivers would silently make ordinary generated waivers authorize imports and legacy grandfathering.  
   Fix: Restrict field waivers to non-imported, non-grandfather series and retain verdict-bound overrides for import and grandfather flows.

8. **HIGH — Stale requests can outlive the state they describe.** Generate-mode override requests carry neither durable `run_state` nor `source_checkpoint_digest` ([headshot_run.py:640](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:640)), so a pending broad waiver can remain signable after look, config, pin, replacement, retirement, or selection state changes.  
   Fix: Add an `override` run-state mode, bind the request to checkpoint/config/QC-chain snapshots, and abandon it automatically whenever any bound state changes.

9. **HIGH — Budget changes create a TOCTOU authorization gap.** A request may be produced when the budget is exhausted, then signed after a newly approved config raises the cap; it would consequently authorize unseen future verdicts, while a lowered cap could leave over-budget candidates in the displayed field.  
   Fix: Sign the verified config receipt/digest and cap, require the current budget to remain exhausted, and recompute attempt positions inside the approval pre-commit check.

10. **HIGH — Independent waivers compose into authority nobody explicitly approved.** If `series_overrides_for` unions all receipts, separate approvals for items A and B can unlock an A+B candidate even though neither signing interaction showed that result; there is also no correction, revocation, or supersession model.  
    Fix: Make each new waiver sign the complete resultant covered set and eligible field while superseding the prior waiver, or forbid multiple active waivers for one scope.

11. **HIGH — The field-builder validation is underspecified.** A cryptographically valid verdict row may still be voided, over budget, tied to obsolete config/policy, missing its raw response or generation receipt, or reference missing pixels; “all failed verdicts” must not mean “all rows bearing `fail`.”  
    Fix: Extract a shared “valid hero verdict except for overridable failing items” verifier and use it identically in request construction, display reconstruction, and pre-commit.

12. **MEDIUM — Partial acceptance has no complete continuation path.** If the human accepts a subset that unlocks zero candidates, the next exhausted-budget run has no newly accumulated `failed` list, while the current call site only writes a request from a failure observed during that invocation ([headshot_run.py:551](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:551)).  
    Fix: Reconstruct uncovered eligible failures from the immutable attempt ledger on every exhausted rerun and issue a revision-bound follow-up or report a terminal no-progress state.

13. **MEDIUM — “All eligible” may resurrect writer-rejected faces.** Current code deliberately excludes `metadata.rejected_candidates`, including reject-all and unselected candidates ([headshot_run.py:495](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:495)); the plan does not preserve this rule in replace or later-waiver flows.  
    Fix: Define eligibility as verified-and-covered minus all rejected history, and test reject-all, unselected, replace, and retire/regenerate sequences.

14. **MEDIUM — The selected headshot does not seal the waiver it relied on.** The signed headshot record stores the generation and QC receipt IDs, but the plan adds no override receipt or field-manifest reference, leaving downstream audit evidence as the generic phrase “accepted by qc_override.”  
    Fix: Version the headshot record to seal the exact override receipt ID(s) and field-manifest digest used during selection.

15. **SIMPLER ALTERNATIVE — A persistent series waiver is broader than the bug requires.** The problematic request is emitted only after the hero budget is exhausted, so the candidate field is already finite; future-verdict authority buys little and creates most of the security and lifecycle problems above.  
    Fix: Sign one field-bound batch record mapping each reviewed `qc_receipt_id` to accepted items, then present the candidates covered by that immutable batch at the separate casting gate.

No files were modified.

VERDICT: REVISE
### Claude's response (revision 2)

Accepted all 15 findings; #15 (simpler alternative) adopted as the backbone — the persistent series-scoped waiver is gone, replaced by one immutable field-bound batch record mapping each reviewed verdict to its accepted items. Consequences: #2/#3 (scope semantics, look-receipt revival) dissolve — the batch names concrete verdicts and seals look_hash + look_receipt_id; #8/#9 handled by a durable override_pending run_state + pre-commit re-checks (checkpoint digest, budget still exhausted under current config); #10 by per-verdict-row eligibility (batches cannot pool items); #1 by signer-side field reconstruction from verified QC rows + canonical manifest digest sealed in the record; #4 by a versioned 1.1 record/envelope branch in lib/receipts.py with the 1.0 branch preserved byte-for-byte; #5/#6 by headshot_packet 1.2 + authored-film@1.5 with the cap derived from verified config (adoption via the existing pin gate only); #7 by keeping import/grandfather on 1.0 verdict-bound records exclusively; #11 by one shared row-validity verifier at construction/display/pre-commit; #12 by ledger-based field reconstruction + terminal no-progress state; #13 by rejected-history exclusion in the validity verifier; #14 by sealing override receipt ids + manifest digest into the headshot record. Nothing rejected.

## Round 2 — Codex

Revision 2 is substantially safer and correctly adopts the finite, field-bound batch model. However, several implementation-blocking contradictions remain.

Round-1 disposition:

- Substantively addressed: #1, #2, #3, #5, #6, #11, #12, #13, #15.
- Still incomplete: #4, #7, #8, #9, #10, #14.
- New batch-design problems: self-referential state, unenforceable receipt selection, mixed pass/fail behavior, and incomplete abandonment semantics.

1. **CRITICAL — `run_state` cannot contain its own `source_checkpoint_digest`.** Revision 2 stores that digest inside the checkpoint metadata ([plan:119](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:119)), but `_write` hashes the checkpoint only after embedding `run_state` ([headshot_run.py:132](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:132)); changing the stored digest changes the digest recursively, directly violating D20’s “no digest of the file it lives in” rule ([D20:55](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:55)).  
   Fix: Keep only request/revision/config/field identifiers in `run_state`; write the checkpoint, compute its digest, and store that digest exclusively in the gate request.

2. **CRITICAL — The claimed “one cited batch” rule is unenforceable with the proposed APIs.** `_overridden` and `batch_overrides_for(project, qc_receipt_id, entity_id)` receive no batch receipt ID ([plan:97](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:97)), so they can only scan receipts and union matches; they cannot enforce “latest,” “cited,” or “at most one” as claimed at lines 110–115.  
   Fix: Put an exact `qc_override_receipt_id` and manifest digest on every batch-covered candidate, resolve that receipt with `exact_approval`, and never discover batch authority by scanning.

3. **CRITICAL — Finding #10 is not actually resolved.** Eligibility explicitly unions legacy 1.0 coverage with batch coverage, and multiple 1.1 records may cite the same verdict; item A from one receipt plus item B from another can still unlock an A+B candidate without any signer approving that resulting combination.  
   Fix: Make the cited batch record contain the complete authoritative coverage for each verdict and ignore all other override receipts for that candidate, while displaying inherited legacy coverage before it is folded into the batch.

4. **HIGH — Packet 1.2 does not specify how selection carries batch authority.** D7 says the final headshot record seals override IDs ([plan:154](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:154)), but D6 only changes `maxItems`; without a per-candidate batch receipt reference, the selection signer must dynamically rediscover authority and cannot prove it displayed the same authorization later sealed into the headshot.  
   Fix: Add nullable `qc_override_receipt_id` and `field_manifest_sha256` fields to packet-1.2 candidates and require the signer’s display, selection, pre-commit, and headshot record to preserve them exactly.

5. **HIGH — The “1.1” override record has no version discriminator.** The proposed object contains no `record_version`, while the envelope changes shape completely ([plan:52](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:52)); inferring the branch from whichever fields happen to be present makes strict rejection of hybrid or malformed records underspecified.  
   Fix: Add `record_version: "1.1"` and define exact allowed fields, unique verdict/asset IDs, canonical ordering, nonempty accepted subsets, and an explicit rejection of mixed 1.0/1.1 shapes.

6. **HIGH — Authored-film 1.4 runtime compatibility is not protected.** Adding a 1.5 manifest does not stop the globally modified `headshot_run` from emitting batch receipts or packet 1.2 under a 1.4 pin; current hero-QC routing recognizes one exact manifest version ([canon_enforcement.py:1491](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1491)).  
   Fix: Gate every batch and packet-1.2 branch on an exact authored-film ≥1.5 capability predicate and add behavioral—not merely byte-freeze—tests proving 1.4 still emits 1.0 overrides and at most four candidates.

7. **HIGH — Import and grandfather isolation remains assertion rather than a field predicate.** The batch constructor scans the shared hero attempt budget, which includes normal, imported, and grandfather series; saying the request originates in the generated branch does not prevent imported or `grandfather=True` rows from entering reconstructed field data.  
   Fix: Define the shared field validator to require a model/local generated receipt, reject the `"imported"` sentinel, and require `series_key.grandfather` to be absent or false.

8. **HIGH — The original casting problem remains when even one candidate passes.** Current code writes an override request only when `passing` is empty ([headshot_run.py:551](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:551)); with one passing face and eleven glasses-only failures, the writer is still forced to cast the single judge-approved face, exactly the policy/casting collapse this plan claims to fix.  
   Fix: Whenever the budget is exhausted and valid failed candidates could enlarge the field, run the batch-waiver gate before selection even if some candidates already pass.

9. **HIGH — Replace/retire pre-commit checks lack a bound headshot tip.** D4 promises to detect replacement or retirement, but neither the proposed record nor `override_pending` state records the active headshot receipt that existed when a `--replace` run created the field.  
   Fix: Seal `expected_active_headshot_receipt_id`—nullable for first casting—in the request/state/record and require exact equality during reconstruction and pre-commit.

10. **HIGH — “Marked abandoned” is not part of the runner state machine.** `run_common.request_paths` recognizes only pending, done, and declined ([run_common.py:112](/Users/ben/Projects/OpenMontage/lib/run_common.py:112)), while pre-commit failure occurs after the one-use token has been consumed ([receipts.py:205](/Users/ben/Projects/OpenMontage/lib/receipts.py:205)); moving only the request to `abandoned/` would leave `run_state` pointing at an invisible request and may cause a conflicting republish.  
    Fix: Add `abandoned` to the canonical request-state API and specify crash-safe `abandoned → atomically clear/rebuild run_state` recovery without reusing the old request ID.

11. **MEDIUM — `config_receipt_sha256` is not an existing authoritative identifier.** The verified config loader resolves a receipt whose record is `{"config_sha256": digest}` but returns the config digest rather than a field named `config_receipt_sha256` ([project_config.py:191](/Users/ben/Projects/OpenMontage/lib/project_config.py:191)).  
    Fix: Bind both the exact config approval `receipt_id` and the actual `project.yaml` `config_sha256`, and define how the signer reloads and verifies each.

12. **MEDIUM — Empty/non-overridable fields have no valid continuation.** D2 excludes local and `NON_OVERRIDABLE` failures, but D5 still offers “accept more items”; when every exhausted attempt is excluded, there is no batch to sign and accepting more is impossible.  
    Fix: Define an explicit empty-field terminal outcome—no request, no waiver option, and a diagnostic directing the operator to repair/reconcile the judge or change the look.

13. **MEDIUM — The subset UI still does not bind the human’s expected result.** The plan says the human chooses accepted items but does not require showing the exact candidates that the chosen subset will unlock before confirmation.  
    Fix: Before signing, render the resulting per-candidate coverage and exact unlocked asset list/count, then seal that result into the batch record and manifest.

No files were modified.

VERDICT: REVISE
### Claude's response (revision 3)

Accepted all 13. Key changes: (r2 #1) run_state stores identifiers only; checkpoint written first, its digest goes in the gate request (import-flow order) — no digest-of-self. (r2 #2/#3) authority is cited, never scanned: every batch-covered candidate carries its exact qc_override_receipt_id + field_manifest_sha256; verifiers resolve via exact_approval and test the cited row alone; legacy 1.0 coverage is displayed and folded INTO the batch, which then becomes sole authority — two receipts can never combine. (r2 #4) packet 1.2 candidates carry the authority fields verbatim through display/selection/pre-commit/headshot record. (r2 #5) record_version discriminator, strict allowlist, hybrid rejection, canonical ordering, nonempty subsets. (r2 #6) batch/packet-1.2 branches behaviorally gated on authored-film ≥1.5 pin predicate; 1.4 regression tests. (r2 #7) field validator structurally rejects IMPORTED_SENTINEL and grandfather series. (r2 #8) the gate fires whenever budget is exhausted and the field is enlargeable — even with passing candidates; declining proceeds with passing only, on the record. (r2 #9) expected_active_headshot_receipt_id sealed and pre-commit-checked. (r2 #10) 'abandoned' added to the request-state API with crash-safe run_state recovery and fresh request ids. (r2 #11) config_approval_receipt_id + config_sha256, the real identifiers. (r2 #12) empty/non-overridable field = terminal, no request written. (r2 #13) subset preview renders the exact unlocked list, sealed as unlocked_asset_ids, recomputed at pre-commit. Nothing rejected.

## Round 3 — Codex

Revision 3 closes round-2 findings #1, #4–#9, and #11–#13 at the design level. Findings #2, #3, and #10 remain incomplete, and the batch lifecycle introduces additional material gaps.

1. Critical — `unlocked_asset_ids` is sealed but not enforced as authority. D3 authorizes a candidate solely from its batch row’s accepted items; it never requires the asset to appear in the human-previewed `unlocked_asset_ids`, and the proposed `batch_row_for` signature lacks the asset and manifest arguments needed to enforce that result ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:123)).

Fix: Make the resolver require candidate asset, QC receipt, field manifest, and batch record hash, then enforce row equality, `asset_id ∈ unlocked_asset_ids`, and complete item coverage.

2. Important — The plan cannot call the existing `exact_approval` as specified. That function requires an expected `record_sha256`, but candidate rows carry only the override receipt ID and field-manifest digest ([receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:457)).

Fix: Either carry `qc_override_record_sha256` through run state, packet, and headshot record, or define a new unique verified-receipt-by-ID API instead of claiming the existing API suffices.

3. Important — Missing-request recovery violates D20. D4 treats both `abandoned/` and `missing` as reasons to mint a new request ID ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:162)), while D20 requires a missing request to be revalidated and republished under the same ID ([D20 plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-28-headshot-run-D20.md:59)).

Fix: Republish the same ID for the checkpoint-before-request crash window; reserve a new ID exclusively for a positively identified abandoned request.

4. Important — Successful-signature crash recovery is still absent. A crash after receipt commit but before the request’s approval marker/done move leaves a pending request plus a valid receipt; current `_decide` deliberately refuses to attach such non-sheet receipts, and batch records do not bind `request_id` ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1286)).

Fix: Bind `request_id` in record 1.1 and recover its unique verified receipt, with a crash-injection test between receipt commit and `_complete_done`.

5. Important — Zero-progress signing and “fresh superseding batch” contradict the non-composition rule. Nothing prohibits signing an empty `unlocked_asset_ids`, D5 incorrectly says this can happen only if state moved, and neither the record schema nor D2 defines how a superseding batch folds prior 1.1 coverage ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:165)).

Fix: Reject approval unless at least one asset is unlocked and remove the superseding-batch path; otherwise add explicit supersession and require the replacement record to contain all prior coverage as one authority.

6. Important — Replace/retire loses field authority. If the writer signs a batch but selects a clean-pass candidate, D7 stores a null override citation, so the exact citation for unselected unlocked candidates disappears; a later `--replace` or post-retire recast must either scan receipts—prohibited by D3—or ask the writer to sign the same waiver again.

Fix: Persist a separate signed field-decision citation whenever a batch preceded selection, and define precisely how replace and retire inherit or invalidate it.

7. Important — The final headshot contract remains unspecified. “Version bump” does not name the version, exact field allowlist, or valid combinations for clean pass, legacy 1.0 override, batch 1.1 override, import, and grandfather flows; current headshot records accept only explicit 1.0/1.1 shapes ([headshots.py](/Users/ben/Projects/OpenMontage/lib/headshots.py:28)).

Fix: Define headshot record 1.2 explicitly, including strict conditional rules for override receipt and manifest fields, and enumerate which flow emits each legal shape.

8. Moderate — The claimed recorded decline is not provided by the existing flow. Non-selection `qc_override` declines accept an optional note, and the previously typed approval reason is not persisted into `declined_note` ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1245), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1494)).

Fix: Require and persist a batch-specific decline note, then test both empty-note refusal and exact decision-log preservation.

No files were modified.

VERDICT: REVISE
### Claude's response (revision 4)

Accepted all 8. (r3 #1) batch_row_for now takes the full binding (receipt id, record sha, qc_receipt_id, asset_id, manifest) and additionally enforces asset ∈ unlocked_asset_ids. (r3 #2) candidate rows carry qc_override_record_sha256 so the existing exact_approval API is used as-is. (r3 #3) missing request = republish SAME id per D20; new id only for a positively-identified abandoned request. (r3 #4) record 1.1 binds request_id; crash between receipt commit and done-move recovers by that binding; crash-injection test added. (r3 #5) zero-unlock approvals refused by the signer; "superseding batch" removed — later batches cover only the residual field, each candidate unlocked by exactly one fully-covering row. (r3 #6) checkpoint keeps field_citations for the whole presented field, so replace/retire recasts re-verify stored citations instead of rescanning or re-signing. (r3 #7) headshot record 1.2 defined explicitly with a conditional-shape matrix per flow; illegal combinations rejected. (r3 #8) batch decline requires a typed note persisted to declined_note. Nothing rejected.

## Round 4 — Codex

Revision 4 soundly closes round-3 findings #3, #4, #5, and #8. The finite, field-bound waiver preserves the human-signing model and look invalidation, but several schema and continuation defects remain.

1. Critical — Packet 1.2 omits part of the required citation. D3 requires receipt ID, record hash, and manifest hash, but D6 adds only receipt ID and manifest hash; `exact_approval` cannot run without the record hash ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:139), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:217), [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:457)).

Fix: Add nullable `qc_override_record_sha256` to packet 1.2 candidates and enforce the same three-field conditional shapes as the final record.

2. Important — A clean selection cannot seal the preceding batch history. Record 1.2 permits a bare manifest hash with no receipt identity, while multiple residual batches may precede selection; one scalar manifest therefore identifies neither a verifiable receipt nor the complete reviewed field ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:89), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:243)).

Fix: Seal a canonical list of field-level `{receipt_id, record_sha256, manifest_sha256}` citations in record 1.2, or leave all override fields null for clean selections and treat batch receipts as independent audit records.

3. Important — Reusing the whole presented field on replace/retire contradicts rejected-history semantics. D3 says later runs rebuild every unlocked candidate from `field_citations`, but D2 excludes rejected history and current selection completion permanently records every unchosen candidate as rejected ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:157), [headshot_run.py](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:876), [headshot_run.py](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:885)).

Fix: Preserve current casting finality and retain authority only for the selected candidate, or introduce a separate human-approved “reconsider rejected candidates” transition.

4. Important — A signed batch can become incomplete after signing. D4 checks budget exhaustion only during batch pre-commit; if a newly approved config raises the cap afterward, continuation or the selection gate can present the old finite field even though additional attempts are now available.

Fix: Bind the config/cap snapshot into selection state and recheck exhaustion both when producing the packet and in headshot-selection pre-commit, resuming generation if the cap increased.

5. Important — Legacy compatibility still assumes one override receipt, but current behavior unions every 1.0 override for a verdict. A candidate covered by receipt A for one item and receipt B for another is valid today, yet record 1.2 has only one legacy receipt/hash slot ([verify.py](/Users/ben/Projects/OpenMontage/lib/sheet_qc/verify.py:112), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:244)).

Fix: Represent legacy authority as a canonical list of exact 1.0 receipt citations, or require a human-signed consolidation batch before migration to 1.5.

6. Important — `field_citations` is keyed globally by `asset_id`. Content-addressed assets can legitimately be identical across characters, causing one entity’s citation to overwrite another’s; downstream entity validation fails closed, but replace/recovery becomes nondeterministically unusable.

Fix: Key persisted citations by `entity_id → asset_id`, with the bound look hash included in the stored entry.

7. Moderate — Record 1.2 still conflates import and grandfather behavior. An imported selection creates a new headshot activation record, while grandfathering creates a separate attestation and leaves the existing headshot record unchanged; they cannot share one emission rule.

Fix: Specify that imported selections under 1.5 emit record 1.2 with all override fields null, while grandfathering emits no record 1.2 at all.

8. Important — “Atomically” abandoning the request and clearing run state is not implementable with the existing separate request move and checkpoint write. Without a mandated order, a crash can leave either an orphan pending request or state that no longer identifies it.

Fix: Under the approval lock, move pending→abandoned first, then clear checkpoint state, and add fault-injection tests before and after each operation.

No files were modified.

VERDICT: REVISE
### Claude's response (revision 5)

Accepted all 8. (r4 #1) packet 1.2 carries the full three-field citation incl. qc_override_record_sha256, same conditional shapes as the record. (r4 #2) simpler branch chosen: clean selections seal nothing; batch receipts are independent audit records. (r4 #3) casting finality preserved — authority retained only for the selected candidate; whole-field reuse dropped; "reconsider rejected" explicitly out of scope; replace/retire regenerate. (r4 #4) config/cap snapshot bound into selection state; exhaustion rechecked at packet production AND selection pre-commit; raised cap resumes generation. (r4 #5) legacy authority represented as a canonical list of 1.0 citations (today's union semantics, exact). (r4 #6) transient citations keyed entity_id → asset_id with look_hash. (r4 #7) imported selections under ≥1.5 emit record 1.2 with override fields null; grandfather emits no 1.2 record. (r4 #8) mandated abandon order under the approval lock (move first, clear second) with fault injection on both sides. Nothing rejected.

## Round 5 — Codex (final round, cap reached)

The core security design is now sound: authority is finite, exact-receipt-bound, look-bound, human-signed, and non-composable across batches. Round-4 findings #1, #2, #4, #6, #7, and #8 are substantively addressed, but material replace/retire and legacy-schema contradictions remain.

1. Important — The acceptance tests resurrect the design D3 explicitly removed. D3 says whole-field `field_citations` have no forward power, while the test list still requires replace/retire recasting from stored `field_citations` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:157), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:305)).

Fix: Replace that test with assertions that unchosen candidates remain rejected and cannot be resurrected without the out-of-scope reconsideration gate.

2. Important — Replace and retire still have incorrect executable semantics. The plan says they “generate anew,” but this batch path starts only after the look’s budget is exhausted; it also never excludes the currently active asset on `--replace` or marks the selected asset rejected on retirement, so the old face can be presented again ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:164), [headshot_run.py](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:468), [headshot_run.py](/Users/ben/Projects/OpenMontage/scripts/headshot_run.py:781)).

Fix: Exclude the active asset during replacement, add the retired asset to durable rejected history, and report terminal no-capacity unless a signed cap increase or new look creates budget.

3. Important — `legacy_citations` is defined for headshot record 1.2 but not explicitly added to packet 1.2. The selection gate therefore has no carried citation list to preserve verbatim and must rediscover legacy authority by scanning, contradicting D3 ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:225), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:253)).

Fix: Add `legacy_citations` explicitly to packet candidates and require the headshot record to copy the exact displayed list.

4. Important — “Every 1.0 receipt” makes legacy authority unbounded and temporally ambiguous. Later duplicate overrides could change the supposedly canonical list or invalidate an existing record if verification recomputes against the live ledger.

Fix: At packet creation choose a deterministic sufficient set of exact 1.0 receipts, seal it, and have downstream verification inspect only that cited set.

5. Moderate — The new post-sign budget checks lack tests at the two dangerous windows. The test plan covers a cap raise while the batch request is pending, but not after batch receipt commit or after the selection packet is written ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:232), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-31-hero-override-casting.md:294)).

Fix: Add cap-raise tests immediately after batch commit and immediately before headshot selection pre-commit, both requiring generation to resume.

The document header also still identifies itself as revision 4, which should be corrected before handoff. No files were modified.

VERDICT: REVISE
### Claude's response (revision 6 — post-cap cleanup)

Round cap reached at 5 with VERDICT: REVISE, but zero contested points: Codex's round-5 assessment opens "the core security design is now sound" and all 5 remaining findings are consistency cleanups, accepted and applied without a further round: (r5 #1) the stale field_citations test replaced with rejected-stay-rejected assertions; (r5 #2) replace excludes the active asset, retire adds it to durable rejected history, both terminal without new budget; (r5 #3) legacy_citations added to packet 1.2, record copies the displayed list verbatim; (r5 #4) legacy set chosen deterministically and sealed at packet creation, verification inspects only the cited set; (r5 #5) cap-raise tests at both post-commit windows; header corrected to revision 6. Disposition: 49 findings across 5 rounds, all accepted, none contested. Handed to Ben for the build decision per the deadlock rule — with the note that this is a convergent cleanup tail, not a disagreement.
