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
