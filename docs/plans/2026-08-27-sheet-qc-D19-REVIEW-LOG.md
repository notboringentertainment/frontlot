# Plan Review Log: D19 Sheet QC
Phases 0-1 complete — plan locked with Ben 2026-08-27 (checklist-first then machine judge). MAX_ROUNDS=5. Reviewer model: gpt-5.6-sol (config) — codex-cli 0.146.0.

## Round 1 — Codex

The plan is not safe to implement yet. Its receipt design does not fit the existing transaction model, and several paths can forge, replay, or bypass QC.

## Material findings

1. **Critical — a QC receipt would not prove that the judge ran.** D19 calls verdicts “unforgeable by the agent,” but the current trust boundary explicitly allows any process running as the user to read the HMAC key and sign/ledger receipts; `handler_context()` protects approval-token minting, not generation-style receipts ([D19:73](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:73), [gates.py:26](/Users/ben/Projects/OpenMontage/lib/gates.py:26)). A public `record_qc_verdict()` accepting caller-supplied fields therefore lets an agent mint a passing verdict without contacting OpenAI.

   Fix: Put judging and signing in a separately privileged service whose key is unavailable to the agent, or explicitly narrow the threat model and stop claiming hostile-agent resistance.

2. **Critical — the proposed provider is forbidden by the signed configuration.** D19 puts `qc.judge_model` in `project.yaml`, but that schema rejects unknown properties and permits only `provider_egress.provider: fal`; Bloodless currently authorizes only FAL ([D19:38](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:38), [project_config.schema.json:53](/Users/ben/Projects/OpenMontage/schemas/project_config.schema.json:53), [project.yaml:9](/Users/ben/Projects/OpenMontage/projects/bloodless/project.yaml:9)). Neither OpenAI nor the proposed Gemini fallback may receive prompts or reference images.

   Fix: Version the project-config schema to bind the QC provider/model and multi-provider egress consent, obtain a fresh config receipt, and forbid provider fallback without another human-approved config change.

3. **Critical — `paid_call_context` is not usable as proposed.** It always invokes visual look governance; a `stage: visual_bible`, `asset_role: qc` image call requires `look_refs` and a prompt recipe rebuilt by `prompt_builder`, which a judge prompt cannot satisfy ([\_shared.py:983](/Users/ben/Projects/OpenMontage/tools/video/_shared.py:983), [\_shared.py:1296](/Users/ben/Projects/OpenMontage/tools/video/_shared.py:1296), [\_shared.py:1357](/Users/ben/Projects/OpenMontage/tools/video/_shared.py:1357)).

   Fix: Add a distinct paid-inference context for QC that preserves config, egress, budget, and recovery controls while validating evaluated assets instead of generation prompt recipes.

4. **Critical — QC does not fit the generation receipt/WAL schema.** Generation receipts require a media `output_sha256` and generation provenance, while their WAL requires recoverable `output_path` files; a JSON verdict has neither as designed ([receipts.py:451](/Users/ben/Projects/OpenMontage/lib/receipts.py:451), [receipts.py:761](/Users/ben/Projects/OpenMontage/lib/receipts.py:761)). Generation receipts also have no “one-use” token, contrary to D19’s claim ([D19:43](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:43)).

   Fix: Create a dedicated QC receipt file, ledger, chain stream, and WAL whose durable output is the canonical verdict JSON; do not overload generation receipts.

5. **Critical — putting QC rows in the generation stream can corrupt canon verification.** Canon enforcement indexes generation receipts by `output_sha256`; if QC uses the evaluated asset ID as that value, its row can replace the actual generation receipt and break provenance/headshot/lineage checks ([canon_enforcement.py:1769](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1769)). Using the verdict digest instead pollutes the generation stream with non-media outputs.

   Fix: Keep QC receipts in a separate stream and expose only explicitly typed `verified_qc_receipts()` to QC enforcement.

6. **Critical — the signed fields omit essential replay boundaries.** D19’s receipt binding omits `role`, even though lookup is by `(asset_id, role)`, and it omits entity ID and the hero asset/approval used for expressions ([D19:42](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:42), [D19:48](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:48)). The same pixels could therefore be relabeled across roles or judged against a superseded hero.

   Fix: Bind project, entity key, sheet slot, ImageRef role, asset hash, active look receipt, active headshot asset/receipt, checklist/policy hash, and provider/model to the canonical signed verdict.

7. **Critical — the judge can be shown substituted bytes.** Resolving `canon/visual/objects/<asset_id>.png` does not prove that the bytes currently at that filename hash to `asset_id`; an agent can temporarily replace them with an easy-pass image, obtain a verdict for the target ID, then restore the original before the gate rehashes it ([D19:39](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:39)).

   Fix: Open the file once, reject symlinks/non-regular files, hash those exact bytes to `asset_id`, and upload that same in-memory byte buffer.

8. **Critical — “newest verdict wins” enables judge shopping.** “No retry on a parsed answer” does not stop calling the tool repeatedly on identical pixels until a stochastic pass becomes newest; conversely, a later failure can invalidate an already displayed pass ([D19:41](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:41), [D19:48](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:48)).

   Fix: Enforce one verdict per complete evaluation tuple under a QC-stream lock; rejudging requires a new policy version or a human override.

9. **Critical — dry-run can become a production bypass.** The build plan specifies a fake judge but never states that fake results cannot be signed or consumed, while `require_qc_pass` does not verify the judge against a signed allowlist ([D19:48](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:48), [D19:70](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:70)).

   Fix: Make dry-run strictly side-effect-free and require every accepted verdict’s provider, model, and policy digest to equal the signed production configuration.

10. **Critical — the override design cannot survive existing schema and receipt verification.** Adding `qc_overrides` only to the gate’s record changes its digest, but `canon_enforcement` later reconstructs `character_approval_record()` without that field; the visual-bible character schema also rejects undeclared properties ([canon_enforcement.py:722](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:722), [visual_bible.schema.json:634](/Users/ben/Projects/OpenMontage/schemas/artifacts/visual_bible.schema.json:634)). Calling the same `require_qc_pass()` during approval would reject the overridden failure anyway.

   Fix: Model an override as its own human `qc_override` approval receipt bound to the failed QC receipt and item, and teach both gate and canon verification to recognize it.

11. **High — the gate currently does not verify what the plan says it verifies.** `_verify_image_ref()` checks only project-local path and bytes; `_construct_character()` does not recheck generation receipt, lineage, prompt recipe, current look, or current headshot ([gate_approve.py:227](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:227), [gate_approve.py:473](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:473)). Generic checkpoint validation does not rerun canon enforcement, so the gate can sign an asset that can never become approved canon.

   Fix: Extract one shared full character-sheet verifier from canon enforcement and run it at both checkpoint write and gate construction/pre-commit.

12. **High — QC evidence is not part of the human-signed record.** D19 puts `qc_receipt_ids` in the agent-written gate request, but `character_approval_record()` does not bind them; QC can change between display and signing without changing the displayed record digest ([D19:58](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:58), [canon_enforcement.py:722](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:722)).

   Fix: Persist exact per-role QC receipt IDs in the versioned visual-bible entry, include them in the character approval record, and recheck them transactionally before signing.

13. **High — structured output does not guarantee complete checklist coverage.** The plan does not require exactly one answer for every expected ID or reject duplicates and unknown IDs; naïve iteration over returned items can compute `pass` after the model omits a failing check ([D19:42](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:42)).

   Fix: Require a bijection with the authoritative checklist IDs and derive verdict/warnings entirely from that normalized set, treating every omission, duplicate, or unknown ID as failure.

14. **High — the upfront budget reservation conflicts with the paid-call protocol.** A persisted aggregate reservation remains nonterminal and causes the next `paid_call_context()` to fail `resume_check`; a CostTracker-only aggregate hold would instead double-count when each child call reserves normally ([D19:57](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:57), [\_shared.py:1031](/Users/ben/Projects/OpenMontage/tools/video/_shared.py:1031)).

   Fix: Perform an atomic affordability preflight, then reserve generation and judge calls individually, or implement a real group reservation whose children consume the hold.

15. **High — concurrent runners can overspend and overwrite state.** Two `sheet_run` processes can both observe no pass, reserve, generate, and write the same checkpoint/request; `CostTracker` rewrites `cost_log.json` without an interprocess lock, so its budget check is not atomic ([cost_tracker.py:487](/Users/ben/Projects/OpenMontage/tools/cost_tracker.py:487)).

   Fix: Hold a project/entity/role workflow lock across resume-check, reservation, attempt recording, checkpoint update, and gate-request creation, and make cost persistence lock-protected.

16. **High — retry bounds are not durable.** Resume reuses passes but does not count prior failures, so a crash after two failures can restart another three-attempt run; there is also no durable mapping of generation receipt, judge receipt, failure IDs, or cost per attempt ([D19:54](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:54), [D19:60](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:60)).

   Fix: Persist an append-only attempt record keyed by entity/role/look/policy and count existing failures toward the cap on resume.

17. **High — transport retry can double-bill.** A timeout after OpenAI accepted the request may cause the stated automatic retry to submit a second paid judgment; the plan does not specify idempotency keys, provider request-ID attachment, or reconciliation ([D19:41](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:41)).

   Fix: Use a persisted idempotency key, attach the provider request ID before retrying, and route ambiguous outcomes through reconciliation instead of blind resubmission.

18. **High — rollout violates the pinned-manifest contract.** `sheet_judge` is absent from authored-film 1.2’s authoritative `tools_available`, while modifying that pinned manifest changes its digest and requires a new migration receipt; D19 mentions neither a manifest version nor migration ([authored-film@1.2.yaml:198](/Users/ben/Projects/OpenMontage/pipeline_defs/authored-film@1.2.yaml:198), [pipeline_pin.py:228](/Users/ben/Projects/OpenMontage/lib/pipeline_pin.py:228), [D10:49](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:49)).

   Fix: Introduce authored-film 1.3 with the QC tool and enforcement contract, gate enforcement by the pin, and migrate Bloodless through `pipeline_migration`.

19. **High — “project-agnostic” generation is false today.** The wardrobe prompt hardcodes Bloodless-specific “night-cleaner,” nitrile-glove, and respirator instructions, while D19 proposes a reusable command for arbitrary entities ([prompt_builder.py:65](/Users/ben/Projects/OpenMontage/tools/prompt_builder.py:65), [D19:54](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:54)). The plan also relies on semantic builder changes receiving version bumps, but the version remains a manually maintained `"1.3"` ([prompt_builder.py:41](/Users/ben/Projects/OpenMontage/tools/prompt_builder.py:41)).

   Fix: Render wardrobe variants solely from validated look data and make the builder policy hash/version change whenever any role template or safety rule changes.

20. **High — existing legacy roles have no QC policy.** `sheet_roles()` returns optional `front`, `three_quarter`, `profile`, and `full_body`, but D19 defines checklists only for turnaround, expressions, and wardrobe; existing or fallback sheets will fail unpredictably ([canon_enforcement.py:45](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:45), [D19:17](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:17)).

   Fix: Either define versioned checklists for every schema-permitted role or explicitly prohibit legacy roles in the new pipeline/schema version.

A smaller coherent design is: deterministic local checks for panel count/cropping/OCR where possible, one VLM call for semantic checks, a content-addressed canonical verdict file, a dedicated QC receipt stream, and a separate human override receipt. That avoids contaminating the media receipt chain and gives gate and canon enforcement one shared verifier.

VERDICT: REVISE

### Claude's response (revision 2)
Verified in code before accepting: FAL-only egress enum + additionalProperties:false (project_config.schema.json), empty tools_available in authored-film@1.2 visual_bible, resume_check blocking on non-terminal reservations, no lock in CostTracker._save, discipline-only trust boundary in lib/gates.py:26.

Accepted in full: #1 (threat model narrowed, section added), #2 (project_config 1.1: qc block + multi-provider egress list, no runtime fallback), #3 (qc_call_context), #4/#5 (separate QC receipt stream/ledger/chain/WAL; verified_qc_receipts typed), #6 (full binding tuple), #7 (O_NOFOLLOW, hash the buffer you send), #8 (one verdict per evaluation tuple under lock; no newest-wins), #9 (fake provider test-only + config equality check), #10 (qc_override approval receipt kind), #11 (shared lib/sheet_verify used at write, construct, pre-commit), #12 (qc_receipts in the entry and in the signed approval record), #13 (bijection over item ids in code), #14 (preflight + per-call reservations), #15 (workflow lock + cost-log lock), #16 (qc-attempts.jsonl counted on resume), #17 (idempotency key + reconcile), #18 (authored-film 1.3 + migration), #19 (wardrobe from look data; builder policy hash), #20 (legacy roles prohibited under 1.3).

Narrowed: the "smaller design" deterministic local checks — accepted for file/format/dimension checks, declined for pixel-heuristic panel counting / crop detection / OCR (false confidence on plain-background sheets; a wrong deterministic answer is worse than a model "unsure").

## Round 2 — Codex

Revision 2 is substantially better, but it is not implementation-ready. Of the original 20 findings, 12 are fully addressed, seven only partially addressed, and one remains open.

### Original 20 findings

| # | Status | Re-review |
|---|---|---|
| 1 | Addressed by scope | The plan now explicitly adopts the repository’s honest-process trust boundary rather than claiming resistance to a hostile same-user process; this is acceptable if documented as a non-security boundary. |
| 2 | Partial | Project-config 1.1 introduces the needed QC provider fields, but the current schema and reader assume v1.0’s single `provider_egress` object; **fix:** define dual-version parsing or an atomic migration and test every existing config consumer. |
| 3 | Addressed | A dedicated `qc_call_context` avoids incorrectly forcing QC calls through generation-specific `paid_call_context`; its new root-validation gap is listed below. |
| 4 | Addressed | QC now has a separate receipt/WAL model instead of pretending a response-only call can satisfy generation-receipt requirements. |
| 5 | Addressed | A separate QC stream prevents verdict rows from corrupting the generation output-hash index. |
| 6 | Partial | Role, entity, assets, receipts, policy and hashes are now signed, but active headshot-receipt identity and raw provider evidence remain incompletely verified; **fix:** bind and verify the exact active receipt and persist the provider response/request ID. |
| 7 | Addressed | Reading through `O_NOFOLLOW`, hashing the resulting buffer, and sending those same bytes closes path/hash substitution. |
| 8 | Partial | “One verdict per tuple” blocks ordinary judge shopping, but the tuple omits `headshot_receipt_id` and the complete policy/prompt hash; **fix:** include both in the canonical tuple. |
| 9 | Addressed | Fake verdict generation is limited to tests outside `PROJECTS_DIR` with an explicit environment opt-in. |
| 10 | Partial | Overrides are now separately human-approved receipts, but no reachable workflow creates an override request after retry exhaustion; **fix:** add an explicit override-request transition and resume path. |
| 11 | Addressed | The shared verifier is required at checkpoint construction, gate construction, and approval precommit. |
| 12 | Addressed | QC receipt IDs are incorporated into the visual-bible entry and therefore into the human-signed approval record. |
| 13 | Addressed | Exact expected checklist IDs are enforced; duplicates, missing IDs and unknown IDs fail closed. |
| 14 | Addressed | Preflight plus per-call reservations replaces the incompatible aggregate reservation. |
| 15 | Partial | Locking is proposed, but entity-scoped leases contradict the existing project-wide director invariant, and locking `_save()` alone does not prevent stale CostTracker overwrites; **fix:** reuse the project-wide lease and perform reload–merge–write under one cost lock. |
| 16 | Open | The unsigned `qc-attempts.jsonl` controls the retry cap despite being described as non-authoritative; deletion or a crash between verdict commit and append resets the cap; **fix:** derive attempts from signed QC receipts or transactionally sign `attempt_n` into each verdict. |
| 17 | Partial | Idempotency and “no blind resubmit” are improvements, but reconciliation lacks a signed provider request identifier and a fully specified pending/unknown state; **fix:** persist provider request IDs and define explicit claimed, submitted, unknown and committed states. |
| 18 | Addressed | Authored-film 1.3 makes QC mandatory and includes a pinned-manifest migration path. |
| 19 | Partial | Builder-policy hashing is data-driven, but the proposed look-spec 1.1 bump is treated as local even though it is a shared WriterOS/OpenMontage contract; **fix:** either scope the full cross-repository migration and re-ratification or defer `wardrobe_variants[].pieces`. |
| 20 | Addressed | Pipeline 1.3 prohibits the legacy role set from silently bypassing the QC policy. |

### New material defects

1. **Critical — the signed policy comparison is internally impossible.** The config stores only `policy_version`, while the verifier requires `policy_sha256 == signed config`; moreover, the proposed hash covers only `{version, role, items}`, excluding the system prompt, normalization/scoring logic, preprocessing and local checks. Existing verdicts could therefore survive a behavior-changing prompt edit. **Fix:** place the exact policy hash in signed config and hash the complete executable policy bundle; include it in the idempotency tuple. See [D19 lines 25–28](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:25>) and [line 63](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:63>).

2. **High — stale headshot approvals can be replayed.** The verdict records `headshot_receipt_id`, but tuple selection and verification compare only the asset ID. Reapproval or supersession using identical pixels would leave an old QC verdict valid. **Fix:** include the receipt ID in the tuple and require both asset and receipt to equal the active headshot-chain tip. See [D19 line 56](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:56>) and [line 63](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:63>).

3. **High — provider consent omits prompt egress.** The QC request sends policy text and signed look fields as well as images, but the plan requires consent only for `generated_sheet_images`. **Fix:** require both `prompts` and `generated_sheet_images` for the configured QC provider. See [D19 line 53](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:53>).

4. **High — one-verdict atomicity has no workable lock protocol.** Holding the QC lock across a network request is unsafe; releasing it before submission allows duplicate judge calls. Shared verification during gate precommit also introduces approval-lock/QC-lock ordering risk. **Fix:** atomically claim the tuple in the QC WAL, release the lock before network I/O, then reconcile and commit under a documented global lock order. See [D19 lines 57–65](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:57>).

5. **High — the proposed lease scope violates existing governance.** `lib.run_lease` currently creates one `.run-lease` per project, while revision 2 proposes `(project, entity)`, allowing two characters to run concurrently despite the director’s explicit prohibition. **Fix:** retain the existing project-wide lease unless D18 governance is intentionally revised. See [run_lease.py line 117](</Users/ben/Projects/OpenMontage/lib/run_lease.py:117>), [D19 line 72](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:72>), and [visual-bible-director.md line 121](</Users/ben/Projects/OpenMontage/skills/pipelines/authored-film/visual-bible-director.md:121>).

6. **Medium — role subsets can produce an invalid fresh artifact.** `--roles turnaround` may reach the write step even though the visual-bible schema requires both turnaround and expressions. **Fix:** permit subsets only when updating an already schema-complete revision; fresh runs must complete all mandatory roles. See [D19 line 77](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:77>) and [visual_bible.schema.json line 576](</Users/ben/Projects/OpenMontage/schemas/artifacts/visual_bible.schema.json:576>).

7. **Medium — `qc_call_context` lacks an explicit project-root trust boundary.** A caller-selected root could redirect config and ledger resolution outside registered projects. **Fix:** reuse `infer_project_dir`, require containment under `PROJECTS_DIR`, reject symlink escapes, and isolate the test-only exception.

8. **Medium — response hashes are not auditable evidence.** A signed `response_sha256` is useless if the exact provider response is not durably retained; the canonical verdict is downstream interpretation, not necessarily the hashed source. **Fix:** store the raw response content-addressably and sign its object ID, provider request ID, and provider-returned model version. See [D19 line 58](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:58>).

The simpler implementation is to keep look-spec at 1.0 for D19, QC only the already-required turnaround/expression sheets, reuse the existing project-wide lease, and make the signed QC ledger itself the source of truth for attempts and tuple claims. That removes two migrations and the unsigned retry side channel without weakening the gate.

VERDICT: REVISE

### Claude's response (revision 3)
Adopted Codex's simpler shape. Accepted: #2 dual-version config loader tested per consumer; #6/#8/new#2 headshot receipt id in the tuple + verified against the chain tip; #10 override-request transition on exhaustion + --resume; #15/new#5 project-wide run_lease (entity-scoped lease rejected, contradicts D18/director invariant); #15 CostTracker transaction = lock+reload+merge+atomic write; #16 attempts derived from signed QC ledger (attempt_n signed; unsigned qc-attempts.jsonl removed); #17/new#4 claim/submitted/unknown/committed WAL states, lock released during network I/O, reconcile by provider request id, documented lock order; #19 look_spec stays 1.0 (wardrobe pieces deferred, cross-repo); new#1 policy_bundle_sha256 over prompt+schema+scoring+local rules, pinned in signed config; new#3 egress consent for prompts AND generated_sheet_images; new#6 fresh runs always complete mandatory roles; new#7 infer_project_dir + PROJECTS_DIR containment; new#8 raw provider response stored content-addressed and its hash signed with provider request id + returned model version. Nothing rejected this round.

## Round 3 — Codex

Revision 3 closes 13 of the 16 round-2 items. The architecture is now coherent, but retry durability and ambiguous-call recovery still have material contradictions.

### Round-2 disposition

| Item | Status | Result |
|---|---|---|
| Partial 1 — config compatibility | Addressed | Dual-version loading, normalization, and consumer tests are specified in [D19 §D19.0](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:23>). |
| Partial 2 — incomplete signed evidence | Addressed | The receipt binds the full tuple, provider request/model version, prompt hash, and content-addressed raw response. |
| Partial 3 — incomplete idempotency tuple | Addressed | Active look and headshot asset/receipt identities are now included. |
| Partial 4 — unreachable override | Addressed | Exhaustion creates an override request and `--resume` consumes it; one local-verdict edge remains below. |
| Partial 5 — lease/cost concurrency | Addressed | Existing project-wide lease is retained and CostTracker uses locked reload–modify–write transactions. |
| Partial 6 — transport reconciliation | Partial | The state machine is improved, but it still lacks an identifier guaranteed to exist before an ambiguous network side effect. |
| Partial 7 — look-spec migration scope | Addressed | Look-spec remains 1.0 and the cross-repository wardrobe-pieces change is deferred. |
| Open 1 — durable retry bound | Partial | Attempts come from signed state, but the ledger counts unique verdicts rather than every generation/evaluation attempt. |
| New 1 — incomplete policy hash | Addressed | The bundle covers prompt, schema, scoring, normalization and local checks and is pinned in signed config. |
| New 2 — stale headshot replay | Addressed | Exact active headshot receipt identity is in both tuple and verifier. |
| New 3 — missing prompt egress | Addressed | Both `prompts` and `generated_sheet_images` are required. |
| New 4 — cross-stream atomicity | Partial | Lock order is defined, but the network ambiguity window remains unrecoverable. |
| New 5 — entity-scoped lease | Addressed | Revision 3 uses the existing project-wide lease. |
| New 6 — invalid role subsets | Addressed | Fresh revisions require mandatory roles; subsets can only preserve already-passing roles. |
| New 7 — caller-controlled project root | Addressed | `infer_project_dir`, `PROJECTS_DIR` containment and symlink rejection are explicit. |
| New 8 — unauditable response hash | Addressed | Exact raw responses are stored content-addressably and their hashes are signed. |

### Remaining material problems

1. **Retry accounting can be bypassed by repeated identical output.** An exact committed tuple returns its old verdict without creating a WAL claim or receipt; if regeneration produces the same bytes repeatedly, the signed attempt count never increases, so resumes can spend indefinitely despite `max_attempts`. **Fix:** append a signed attempt event for every generated candidate, including events that reuse an existing verdict, and enforce the cap against attempt events rather than verdict count. See [lines 57 and 75](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:57>).

2. **A builder fix cannot start a fresh attempt series.** The attempt key includes QC policy but omits `builder_policy_sha256` and generation endpoint/model; after three failures, repairing the builder leaves the run permanently exhausted unless the unrelated judge policy is changed. This contradicts [D19.6](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:79>) and the stated recovery path. **Fix:** bind the attempt series to the generation-policy hash and generation endpoint/model as well as the QC policy.

3. **The retry ceiling is caller-controlled rather than governed.** An agent can invoke `--max-attempts 100` and consume most of the signed project budget through individually cheap calls. **Fix:** make the signed config/policy define the maximum; allow the CLI only to lower it, with a new human-signed config required to raise it. See [D19.5](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:70>).

4. **Ambiguous network calls still cannot always be reconciled.** The plan persists a provider request ID only “when the API returns one”; a timeout or crash after submission but before that ID is persisted leaves `claimed`/`unknown` permanently blocking, while reconciliation itself requires the missing ID. **Fix:** persist a provider-supported client idempotency key or background-job identifier before submission and specify recovery for crashes in every state; otherwise fail the provider probe as unsupported. See [line 57](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:57>).

### New non-blocking corrections

- Local precheck failures use `provider: local`, but the verifier requires the configured external provider; an automatically generated override for such a verdict can never pass. **Fix:** exclude local failures from override candidates and present deterministic remediation instead.
- Require unique `provider_egress` entries by provider; JSON Schema `uniqueItems` alone permits two conflicting entries for the same provider.
- Explicitly add `qc` to `gates.CHAIN_STREAMS`; the current whitelist accepts only approval, generation and bootstrap streams at [gates.py line 445](</Users/ben/Projects/OpenMontage/lib/gates.py:445>).
- [Line 60](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:60>) says `policy_version` must equal config, but config contains `policy_bundle_sha256`; align the terminology.

The simpler shape materially improved the plan. The remaining blockers are confined to retry and paid-call recovery rather than the overall gate/receipt design.

VERDICT: REVISE

### Claude's response (revision 4)
Accepted all four blockers and all four non-blocking corrections: #1 signed attempt events per generated candidate (reuse counts); #2 series key adds builder_policy_sha256 + generation endpoint/model; #3 qc.max_attempts_per_series in signed config, CLI may only lower; #4 client idempotency key persisted before I/O, OpenAI background mode id persisted before polling (probe must confirm or provider rejected), explicit recovery per state incl. voided_unconfirmed write-off after grace (bounded ~$0.03 loss, never a pass). nb: local verdicts excluded from override candidates; one egress entry per provider enforced in loader; qc added to CHAIN_STREAMS; policy_version wording aligned to policy_bundle_sha256.

## Round 4 — Codex

Revision 4 addresses the four round-3 corrections and most of the four blockers, but three material contract gaps remain.

### Round-3 disposition

- Repeated identical output: intent addressed by signed attempt events, but their append-only lifecycle is internally inconsistent.
- Builder/model series reset: addressed for builder and generation model changes.
- Governed retry ceiling: addressed; CLI can only lower the signed cap.
- Ambiguous OpenAI calls: addressed with background submission, durable IDs, bounded unknown-call handling, and a mandatory probe.
- Local verdict overrides: addressed.
- Duplicate provider-egress entries: addressed.
- QC chain registration: addressed.
- Policy-hash terminology: addressed.

### Remaining material problems

1. **Signed attempt rows cannot be “completed” as specified.** [Line 75](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:75>) appends a signed attempt row before generation, then says it is completed later with generation and QC receipt IDs. Mutating it breaks its signature and chain; appending additional `attempt` rows causes the stated row-count algorithm to overcount. **Fix:** define one immutable, countable `attempt_started` row with an `attempt_id`, followed by immutable `generation_attached` and `verdict_attached` transition rows that do not increment the attempt count.

2. **Direct `SheetJudge` calls bypass the governed attempt cap.** Its input remains `{project_dir, entity_id, role, asset_id}`, and neither the judge nor `require_qc_pass` requires a signed attempt event. An agent can manually generate and judge successive assets outside `sheet_run.py` until one passes, then present that valid verdict at the gate. **Fix:** require an open signed `attempt_id` in `qc_call_context`, bind it into the verdict, and make the gate verifier require the complete attempt chain and series-cap compliance.

3. **The provider-neutral contract is only implemented for OpenAI.** [Line 57](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:57>) gives OpenAI a background response ID and recovery protocol, but config still permits Google without an equivalent stable-ID/reconciliation contract. The series key also omits judge provider/model, so a human-approved provider change cannot open a fresh series after false negatives. **Fix:** require every eligible adapter to pass the same submit-ID-poll-reconcile probe, remove Google until it does, and include judge provider/model in `series_key`.

One additional execution inconsistency should be corrected: [line 25](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:25>) requires consent for both `prompts` and `generated_sheet_images`, while [line 53](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:53>) tells `qc_call_context` to enforce only the latter. The context must explicitly require both classes.

The receipt authenticity, active-look/headshot binding, gate precommit verification, raw-response evidence, project locking, and config migration are otherwise sound enough.

VERDICT: REVISE

### Claude's response (revision 5)
Accepted all: #1 immutable attempt_started / generation_attached / verdict_attached rows, only attempt_started counts; #2 judge input is {attempt_id, asset_id}, qc_call_context requires an open signed attempt and the gate verifier requires the complete attempt chain + cap compliance; #3 google removed from the provider enum until an adapter passes the same probe, judge provider/model added to series_key; nb: qc_call_context requires both prompts and generated_sheet_images consent.

## Round 5 — Codex

Revision 5 closes all three round-4 gaps and the consent inconsistency. I found no remaining material forgery, replay, retry-cap, concurrency, or gate-bypass problem.

- Immutable attempt lifecycle: addressed with distinct `attempt_started`, `generation_attached`, and `verdict_attached` rows.
- Direct-judge bypass: addressed; verdicts require a complete, cap-compliant signed attempt chain.
- Provider recovery: addressed by limiting config to OpenAI until another adapter proves the same recovery contract.
- Prompt egress: addressed; `qc_call_context` now explicitly requires both consent classes.

Non-blocking cleanup for implementation:

- [Line 58](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:58>) still says the stream has two row kinds, while [line 75](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:75>) defines four total kinds; make the schema enumerate `verdict`, `attempt_started`, `generation_attached`, and `verdict_attached`.
- The current Seedream tool creates its reservation internally after `paid_call_context`; add an explicit reservation handoff or pre-submit callback so `attempt_started` can be committed after reservation but before provider submission.
- Remove stale text permitting a switch to Google at [line 107](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:107>), and update “approve revision 3” at [line 96](</Users/ben/Projects/OpenMontage/docs/plans/2026-08-27-sheet-qc-D19.md:96>).
- In the override guidance, “builder fix” should open a new builder-policy series, not require changing the unrelated QC policy bundle.

These are implementation/editorial corrections, not reasons to reopen the architecture.

VERDICT: APPROVED

### Claude's response (revision 6, editorial only)
Applied all four non-blocking corrections: row-kind enum of four; pre_submit callback in paid_call_context for attempt_started; Google fallback text removed; builder fix = new builder-policy series. Also resolved step 7: Ace revision 2 stands (human-verified), QC production starts with Sebastian.

## Resolution
APPROVED after 5 rounds. Build not started; awaiting Ben's sign-off.
