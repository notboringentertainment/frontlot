# Plan Review Log: sheet_run finish step (F0)
Started 2026-08-29. MAX_ROUNDS=5.

## Round 1 — Codex (gpt-5.6-sol)
The plan is not sound enough to implement. D3.3’s exact receipt identity is not reconstructible from the current contracts.

### Q1–Q3

- Q1 — Block a second entity. A second sheet rewrites the single visual-bible checkpoint, invalidating A’s checkpoint digest; finishing B also changes the checkpoint to `in_progress`, after which A’s gate refuses it because `_load_pending_checkpoint` requires `awaiting_human`.
- Q2 — `request_id` is not recorded in `approvals.jsonl`. The repository can reconstruct a set of equivalent receipts using `{kind, entity_id, record_sha256, source_checkpoint_digest}`, but not one unique receipt ID.
- Q3 — Do not leave the declined draft reusable. Archive/remove it from the active artifact and persist its revision plus rejected asset/QC IDs; otherwise the next run reuses the exact sheet the human rejected.

### Material findings

1. Critical — D3.3 assumes a nonexistent request→receipt mapping. Approval rows contain `receipt_id`, `record_sha256`, and `source_checkpoint_digest`, but no `request_id`; `_decide` moves the request unchanged without recording the returned receipt ID, and `find_approval` only filters kind/entity/record digest. A crash after receipt commit but before request movement can also create several receipts for one request. See [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:191), [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:401), and [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1199).

   Fix: Make the done request a durable result containing `approval_receipt_id`, backed by a signed `request_id` or crash-idempotent tuple recovery, and finish by exact receipt ID.

2. Critical — `source_checkpoint_digest` is signed but never validated against the checkpoint. `_load_pending_checkpoint` checks schema/status/project only; `_construct_character` does not bind the digest into its record; `_decide` blindly copies the request field. Tampering with the pending request before approval yields a valid receipt for the tampered digest, and the proposed finish accepts because it compares the same request field to the receipt. See [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:207), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:568), and [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1202).

   Fix: At construction and pre-commit, require `req.source_checkpoint_digest == checkpoint_digest(current checkpoint)` and reject missing or changed bindings.

3. Critical — The sheet pre-commit check has a TOCTOU hole. Its closure rechecks the captured `entry`, not the entry reloaded from the current checkpoint; checkpoint writers are not serialized by the receipt lock. Headshots correctly reload and compare digest, entry, and candidates. See [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:589) versus [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:976).

   Fix: Reload the checkpoint and entry inside sheet pre-commit and compare checkpoint digest and reconstructed approval record with the displayed values.

4. Critical — Allowing multiple entity run states is incompatible with both lifecycle and digest binding. B’s write changes A’s bound checkpoint digest; finishing or declining B writes `in_progress`, making A’s still-pending gate fail its `awaiting_human` requirement.

   Fix: Permit exactly one outstanding visual-bible sub-gate until it is finished or declined, unless the design moves to immutable per-entity snapshots.

5. High — Decline immediately reuses the rejected images. `_acceptable_verdict` returns any passing verdict from the same series, so leaving or merely deleting the draft causes the next revision to present identical assets again. See [sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:175) and [sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:299).

   Fix: Persist rejected asset/QC IDs in checkpoint metadata and exclude them from verdict reuse while retaining a separate monotonic revision counter.

6. High — The claimed shared enforcement lookup does not exist. `_require_entry_receipt` returns nothing, ignores `source_checkpoint_digest`, and selects the latest receipt by record digest; it cannot identify the request-bound receipt described by D3.3. See [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1077).

   Fix: Introduce one shared exact-receipt verifier that accepts receipt ID, kind, entity, record digest, and source digest, and use it in finish and canon enforcement.

7. High — Canon enforcement does not provide the claimed backstop for the proposed final write. For `in_progress`, enforcement returns before `_check_visual_bible`, so an approved entry only needs a nonempty receipt ID schema-wise; `_require_entry_receipt` is not called. See [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1902) and [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1920).

   Fix: Verify the named approval receipt for every `status: approved` entry even in partial `in_progress` visual-bible checkpoints.

8. High — D3.5’s `human_approval_required=False` claim conflicts with the manifest. Visual-bible is gated, and `write_checkpoint` forces the field to true regardless of the caller’s false value. See [authored-film@1.4.yaml](/Users/ben/Projects/OpenMontage/pipeline_defs/authored-film@1.4.yaml:221) and [checkpoint.py](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:779).

   Fix: Keep `human_approval_required=True`; the sub-gate receipt approves the entity, not the overall visual-bible stage.

9. Medium — Gate-request publication remains crash-unsafe. `_write_sheet_request` uses `write_text`; a truncated request is neither “missing” nor recoverable by the proposed republish path. See [sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:338).

   Fix: Publish and republish requests with `atomic_write_json`, then test interruption before and after publication.

10. Medium — Request and path validation are omitted. Raw `sheet-<entity>-<rev>` can exceed the 64-character request contract, and deliberately declining `resolve_project_root` preserves project-slug traversal risk. The repository already provides the correct helpers. See [run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:30) and [run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:72).

   Fix: Use `resolve_project_root`, `require_entity_id`, and `request_id_for` instead of raw path joins and interpolation.

11. Medium — Removing `--resume` unnecessarily breaks the documented D19 override workflow, even though it is currently behaviorally redundant.

   Fix: Keep `--resume` as a deprecated alias while adding `--finish` for the explicit no-state check.

12. Test-plan defects — `canon_view` creates role links such as `turnaround.png` and `expressions.png`, not `sheet.png`; the proposed tampered-done test also misses the exploitable case where the digest is changed before signing. See [canon_view.py](/Users/ben/Projects/OpenMontage/lib/canon_view.py:65).

   Fix: Assert the actual role links and add gate-time digest tampering, pre-commit checkpoint mutation, duplicate-receipt recovery, multi-entity exclusion, and rejected-asset non-reuse tests.

The plan’s statement that a blocked run “regenerates” is also factually wrong: exhausted attempts remain blocked and existing tests explicitly assert no further generation until an override or series change.

VERDICT: REVISE
### Claude's response (round 1)
Every finding verified against the code by an independent read (C1–C10, all TRUE/PARTLY-true). All accepted:
- Q1 → block second entity (D1). Q2 → gate writes approval_receipt_id into the done file + crash-idempotent reuse + shared exact_approval verifier (D3). Q3 → decline restores prev entry, records rejected verdict/asset ids, excludes them from reuse (D2).
- Gate validates source_checkpoint_digest at construct + reloading pre-commit (D3), enforcement backstop for in_progress bibles (D4), human_approval_required stays True, atomic_write_json, run_common helpers, --resume kept as alias, test names corrected (canon_view links per-role, no sheet.png), "blocked regenerates" wording removed.
Nothing rejected.

## Round 2 — Codex
Revision 2 fixes most first-round issues, but the gate transition and recovery protocol still has material correctness and security defects.

### Q1–Q3

- **Q1 — concurrent entity runs:** Correctly resolved. Blocking entity B while entity A has outstanding state is necessary because both share one checkpoint and digest. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:62)
- **Q2 — receipt reconstruction:** Partially resolved. `approvals.jsonl` contains `receipt_id`, `kind`, `entity_id`, `record_sha256`, and `source_checkpoint_digest`, so the tuple is searchable. However, it proves approval of identical content at a checkpoint—not identity of the gate request—because receipts contain no `request_id`. The explicit ID stored in the done request fixes the normal path, but recovery remains unsafe without serialization and is not generally valid for digest-less kinds. [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:191) [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1199)
- **Q3 — decline behavior:** Restoring/removing the draft and remembering rejected QC IDs/assets is correct, but “the next run regenerates” is false when the rejected pass already consumed the final allowed attempt.

### Material findings

1. **Critical — the planned pending-to-done transition is not atomic.** Writing `done/<id>.json` and then deleting `pending/<id>.json` creates a crash window with both files present; `request_state()` deliberately treats that as corruption. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:92) [run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:98)  
   **Fix:** Atomically rewrite the pending file with `approval_receipt_id`, then use the existing single-filesystem `atomic_move` to move that enriched file to done.

2. **Critical — concurrent gate decisions can mint duplicate receipts.** Two processes can both observe no matching receipt, both mint and commit approvals, and only subsequently race over the request file; the receipt lock serializes ledger writes but does not enforce logical-request uniqueness. [gates.py](/Users/ben/Projects/OpenMontage/lib/gates.py:600) [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1195)  
   **Fix:** Hold a per-request interprocess lock across reload, recovery lookup, receipt commit, request enrichment, and atomic move.

3. **High — tuple recovery is not safe “for every kind.”** For requests without an authoritative non-null checkpoint digest, an older approval with the same kind/entity/record can be silently reused for a new request because the receipt does not bind `request_id`. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:91)  
   **Fix:** Restrict tuple recovery to checkpoint-bound kinds whose digest is revalidated, or add signed `request_id` to receipt records and recover by that identity.

4. **High — the proposed `--resume` alias breaks D19 override recovery.** D19 says to run `sheet_run.py --resume` after signing an override, but blocked generation has no finish run-state; revision 2 would now raise “nothing to finish” instead of resuming generation. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:69) [D19 plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:68)  
   **Fix:** Make only `--finish` require run-state; preserve legacy `--resume` with no run-state as normal generation/override continuation.

5. **High — rejection memory does not restore the attempt budget.** `_acceptable_verdict` can ignore the human-rejected pass, but `attempts_started()` still counts it; at cap 1 or after a pass on the final attempt, regeneration is impossible and may produce a useless block with no failed verdict to override. [sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:182)  
   **Fix:** Specify and test cap-exhausted human rejection explicitly—either fail with a dedicated blocker or require a separately authorized budget reset.

6. **Medium — the happy-path role assertion conflicts with the sheet contract.** The test expects optional `wardrobe` and prohibited legacy roles (`front`, `three_quarter`, `profile`, `full_body`) to exist, while authored-film 1.3 permits only turnaround, expressions, and optional wardrobe. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:164) [sheet_verify.py](/Users/ben/Projects/OpenMontage/lib/sheet_verify.py:44)  
   **Fix:** Assert hero, turnaround, and expressions; assert wardrobe only when requested and legacy role links absent.

The explicit receipt ID, `exact_approval`, gate-time digest comparison, pre-commit reload, canon-enforcement backstop, and one-outstanding-sheet rule are sound improvements. The transition race and request-identity gap still prevent safe implementation.

VERDICT: REVISE
### Claude's response (round 2)
All six verified (receipt_lock at gates.py:600 is re-entrant; request_state fails on two files; D19 plan :68 documents --resume after override; sheet_verify.py:44 forbids legacy roles). All accepted: decide under receipt_lock end to end; enrich pending in place then atomic_move; tuple recovery only for digest-bound requests that revalidate, digest-less kinds refuse and name the receipt; --resume keeps D19 semantics, only --finish needs state; cap-exhausted rejection gets a dedicated Blocked; test roles = hero/turnaround/expressions (+wardrobe on request), legacy absent. Nothing rejected.

## Round 3 — Codex
Revision 3 resolves the six round-2 findings, but it introduces or leaves four material lifecycle gaps. It is not yet safe to implement as written.

### Q1–Q3

- **Q1 — one outstanding sheet:** Correct. The shared visual-bible checkpoint makes concurrent sheet sub-gates incompatible. This does not eliminate the need to revalidate the checkpoint during finish.
- **Q2 — receipt reconstruction:** For a sheet request, yes: verified approval rows expose `receipt_id`, `kind`, `entity_id`, `record_sha256`, and `source_checkpoint_digest`, and the proposed approval lock makes a unique tuple recoverable. [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:191) [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:429) The broader “every kind” recovery claim remains false.
- **Q3 — decline:** Restoration, rejection memory, and cap-exhaustion handling are now specified correctly for a full rerun, but partial-role reruns can still retain assets from the rejected sheet.

### Findings

1. **Critical — finish has a post-approval TOCTOU hole.** Gate construction and pre-commit validate the checkpoint at signing time, but `_finish_sheet` never requires the current checkpoint digest to equal the receipt-bound digest; another checkpoint write after approval can change metadata or other entries while leaving `character_approval_record` unchanged, and finish will still promote the draft. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:133)  
   **Fix:** Before reading or promoting the draft, require the current visual-bible checkpoint to remain `awaiting_human` and `checkpoint_digest(current_path) == bound`.

2. **Critical — decline remains non-atomic and races approval.** Current `_decline` writes the declined copy and then unlinks pending without the approval lock; a crash leaves both states, while concurrent approve/decline can commit a receipt and leave both `done` and `declined`, which `request_state()` treats as corruption. [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1134) [run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:98)  
   **Fix:** Put decline under the same approval lock, atomically enrich the pending file with `declined_note`, then `atomic_move` it to declined after reloading and confirming it is still pending.

3. **High — “general for every kind” crash recovery is unreachable for state-mutating approvals.** The CLI constructs the record before calling `_decide`; after a committed `look_lock` receipt, for example, reconstruction raises “already the active look,” so a crash before request enrichment can never reach `_decide`’s recovery lookup. Retire and other chain-tip transitions have the same problem. [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1307) [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1322) [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:296)  
   **Fix:** Scope tuple recovery to `sheet` for this plan, or add signed `request_id` and perform recovery before normal construction/display for every kind.

4. **High — partial reruns can reuse human-rejected roles.** Decline records every draft role’s asset and verdict as rejected, but subset generation copies omitted roles directly from `prev` through `kept`; that path never calls `_acceptable_verdict`, so a rejected expression/wardrobe asset can be inserted into the next draft unchanged. [sheet_run.py](/Users/ben/Projects/OpenMontage/scripts/sheet_run.py:153) [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:78)  
   **Fix:** Reject `kept` roles whose asset or QC receipt is in rejection memory and automatically add those roles to the regeneration set.

5. **Medium — an enriched pending request is trusted without an explicit receipt check.** The plan says a pending file already carrying `approval_receipt_id` “just completes the move,” although the marker is unsigned and may be stale or tampered. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:103)  
   **Fix:** Before completing that move, run `exact_approval` against the reconstructed kind/entity/record/source tuple.

6. **Medium — D4’s claimed backstop is under-specified and under-tested.** The visual bible can contain approved characters, locations, and poster data during `in_progress`, but the plan names only “entries” and tests only a bogus character receipt; the existing partial path also bypasses generic poster receipt enforcement. [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1830) [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1881)  
   **Fix:** Explicitly enforce exact receipts for approved characters, locations, and poster during `in_progress`, with one failing test for each category.

Also replace the proposed two-thread lock test with at least one multiprocessing test and add an approve-versus-decline race test; the contract being claimed is interprocess safety, not merely same-process serialization.

VERDICT: REVISE
### Claude's response (round 3)
All six verified (_decline copy-then-unlink at :1134; construct() precedes _decide at :1307; kept roles bypass _acceptable_verdict at sheet_run:153-160; v12 in_progress path lacks the :1183/:1188/:1224 receipt checks). All accepted: finish checks checkpoint still awaiting_human and digest == bound; decline under the lock with enrich + atomic_move; tuple recovery scoped to sheet only, other kinds refuse-and-name; enriched marker verified by exact_approval; kept roles in rejection memory forced into regeneration; D4 covers characters, locations, poster with a test each; multiprocessing lock tests + approve/decline race test. Nothing rejected.

## Round 4 — Codex
Revision 4 closes all six round-3 findings for the sheet happy path. Q1–Q3 are now substantively resolved, and sheet receipt recovery is reconstructible. Three material integration problems remain.

### Q1–Q3

- **Q1:** Correct—one outstanding sheet request is required by the shared checkpoint.
- **Q2:** Correct for `sheet`: the validated non-null checkpoint digest plus kind, entity, and record digest identifies the committed receipt; the explicit receipt ID handles the normal path.
- **Q3:** Correct—the plan now handles rejected kept roles and exhausted attempt budgets.

### Findings

1. **High — reloading the request under the lock breaks `qc_override`.** The human’s override reason exists only in the in-memory request after `req["reason"] = typed`; the pending file still contains `reason: ""`. A literal reload inside `_decide` therefore loses the reason or disagrees with the displayed `Constructed`, breaking the D19 override workflow. [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:632) [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1296)  
   **Fix:** Reload and compare immutable disk fields under the lock, then overlay the separately passed, trusted human reason before reconstruction and persistence; add a QC-override regression test.

2. **High — a post-signing digest mismatch leaves an unrecoverable done request.** The plan tells the user to “decline and re-run,” but gate decline accepts only pending requests; the request is already in `done`, and every subsequent sheet invocation repeats the digest failure with no way to clear run state or restore the previous entry. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:144) [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:1142)  
   **Fix:** Specify an explicit stale-done abandonment transition that archives the done marker, safely restores/removes the draft, clears run state, records the event, and starts a new monotonic revision.

3. **High — “digest checks apply to every kind” is unsupported by the design.** The explicit changes cover sheet and headshot/hero, but look-lock, reference-import, and grandfather requests also carry checkpoint digests; their constructors do not compare those values with the authoritative checkpoint, while their finish paths largely compare the receipt back to the same unsigned done marker. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:102) [look_ingest.py](/Users/ben/Projects/OpenMontage/lib/look_ingest.py:622) [look_run.py](/Users/ben/Projects/OpenMontage/scripts/look_run.py:281)  
   **Fix:** Either accurately scope the claim to sheet/headshot/hero or define a generic per-kind checkpoint-binding validator, including pre-commit revalidation, for every non-null source digest.

4. **Medium — `exact_approval(..., source_checkpoint_digest=None)` cannot express both “ignore this field” and “require signed null.”** Canon enforcement needs the former for legacy entries, while digest-less enriched-marker validation needs the latter; a normal `None` default cannot distinguish them.  
   **Fix:** Use an `_UNSET` sentinel so explicitly supplied `None` is compared against the receipt while an omitted argument skips the check.

5. **Low — the proposed lock argument is not an available variable.** `_decide` has no `stream`, and `receipt_lock` accepts the chain stream—not an approval kind. [gates.py](/Users/ben/Projects/OpenMontage/lib/gates.py:600)  
   **Fix:** Specify `gates.receipt_lock(project_id, "approval")` for both approval and decline.

The first two issues break reachable workflows rather than merely weakening tests, so revision is still required.

VERDICT: REVISE
### Claude's response (round 4)
All five accepted: reload-under-lock compares immutable fields and overlays the trusted in-memory reason/note/selection (qc_override regression test); new `sheet_run --abandon` transition for a stale done request (marker archived to abandoned/, decision logged, prev entry restored, state cleared, next revision N+1); digest-vs-checkpoint claim scoped to sheet/headshot/hero with look-lock/import/grandfather named as a separate follow-up; `exact_approval` digest argument uses an _UNSET sentinel; lock is `receipt_lock(project_id, "approval")`. Nothing rejected.

## Round 5 — Codex
Revision 5 resolves the five round-4 findings, and D3.3 is now sound for sheets: the receipt is reconstructible, request-bound through the validated checkpoint and record digests, serialized under the approval lock, and identified explicitly in the done marker.

Q1–Q3 are also adequately resolved. Two crash-consistency defects remain in the newly added terminal transitions.

### Findings

1. **Critical — decline is file-atomic but not decision-idempotent.** A crash after writing `declined_note` into pending but before moving it leaves a pending request containing a durable human decline; a fresh gate invocation can load that file and approve it because `load_request` permits extra fields and the plan defines recovery only for `approval_receipt_id`. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:120) [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:136)  
   **Fix:** Treat a pending `declined_note` as a committed decline marker: complete its move under the lock before display/approval and test that post-rewrite approval produces no receipt.

2. **Critical — `--abandon` is not one atomic transition.** Moving the done marker, appending the decision, and rewriting the checkpoint are three commits; a crash after the marker move leaves run state intact while `request_state()` reports missing, so normal resume follows the republish path and resurrects the request that was supposedly abandoned. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:159) [run_common.py](/Users/ben/Projects/OpenMontage/lib/run_common.py:98)  
   **Fix:** Make the checkpoint authoritative with an idempotent `abandoning` state, commit restoration/state clearing first, then archive the marker; retries must complete cleanup rather than enter missing-request republish.

3. **High — abandonment may overwrite the very concurrent change that caused staleness.** Because the checkpoint digest is known to differ, blindly restoring `sheet_prev_entry` can destroy a newer edit to the same character.  
   **Fix:** Restore only if the current entity still exactly matches the expected draft revision and approval record; otherwise preserve it and require explicit reconciliation.

4. **Low — finish uses `bound` before the plan validates/extracts it.** Step 0 compares the checkpoint against `bound`, while step 1 establishes that the done request contains a valid bound digest. [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-29-sheet-run-finish.md:152)  
   **Fix:** Parse and validate the done request and bound digest before performing the checkpoint comparison.

The core finish path is ready, but the two newly specified recovery transitions still violate the plan’s own crash-safety contract.

VERDICT: REVISE
### Claude's response (round 5) — MAX_ROUNDS reached
Codex: "core finish path is ready"; four remaining findings are crash-consistency in the recovery transitions added in rounds 4–5. All four accepted into revision 6: pending file carrying declined_note is a committed decline that load_request completes under the lock; --abandon is checkpoint-authoritative and idempotent (reconcile → restore+`abandoning` state in one write → decision → archive marker → clear state; resume completes cleanup, never republishes); restore only when the current entry still matches the published draft; finish parses the done request before comparing digests. No formal APPROVED verdict was obtained within 5 rounds; the outstanding disagreement set is empty (every finding accepted). Proceeding to build per Ben's instruction ("plan, Codex review, then build").
