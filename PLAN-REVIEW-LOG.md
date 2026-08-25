# Plan Review Log: Visual Bible stage for authored-film
Phases 0-1 (recon + interrogation) complete — plan locked with the user 2026-08-25. MAX_ROUNDS=5.

## Round 1 — Codex (gpt-5.6-sol, thread 01a03a6a-3c81-7121-8e72-0f67012d7d6d; first attempt hit the 10-min ceiling at xhigh effort, resumed at high)
The plan is not implementation-ready. It has several contract-breaking assumptions and security gaps.

1. **BLOCKER — `visual_bible` depends on a future stage.** The plan places it before `scene_plan`, then defines completion using the scene plan’s cast and expects storyboards for shots that do not exist yet ([PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:10), [PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:36)).  
Fix: Move it to `proposal → script → scene_plan → visual_bible → assets`, with `scene_plan` as a required input.

2. **BLOCKER — later mutation of `canon_packet` is ignored.** The plan populates `canon_packet.*.reference_assets`, but `_load_canon()` always reloads the immutable `canon_ingest` checkpoint, explicitly ignoring downstream copies ([canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:53)).  
Fix: Keep `canon_packet` immutable and make enforcement load approved references directly from `checkpoint_visual_bible.json`.

3. **BLOCKER — downstream stages are not wired to the new artifact.** The current `assets` and `compose` stages do not require `visual_bible`, and the asset director only declares `scene_plan` and `canon_packet` as inputs ([authored-film.yaml](/Users/ben/Projects/OpenMontage/pipeline_defs/authored-film.yaml:186), [asset-director.md](/Users/ben/Projects/OpenMontage/skills/pipelines/authored-film/asset-director.md:11)).  
Fix: Add `visual_bible` to every consuming stage’s `required_artifacts_in` and update the EP, scene, asset, edit, and compose skills.

4. **BLOCKER — the proposed manifest `limits` block is schema-invalid.** Neither a stage nor the manifest root permits `limits`; both use `additionalProperties: false` ([pipeline_manifest.schema.json](/Users/ben/Projects/OpenMontage/schemas/pipelines/pipeline_manifest.schema.json:41)).  
Fix: Define a typed limits/config property in the manifest schema or store project-specific limits in a separate validated project configuration.

5. **BLOCKER — dynamic sub-gates have no enforceable approval model.** Checkpoints have one stage-level `human_approved` boolean, while early `awaiting_human` checkpoints must already carry a schema-valid declared output; an agent can simply mark entity statuses approved without durable per-entity approval evidence.  
Fix: Add append-only approval receipts keyed by asset hash—or split hero, sheet, location, storyboard, and poster approvals into real checkpoint stages.

6. **BLOCKER — the scene schema cannot identify the cast being validated.** Scenes contain beat/lock `canon_refs`, but no structured character IDs, location IDs, or shots; character/location names are neither unique nor stable ([scene_plan.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/scene_plan.schema.json:13), [canon_packet.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/canon_packet.schema.json:77)).  
Fix: Add unique entity IDs to canon and explicit `character_refs`, `location_ref`, and `shot_id` fields to scene planning.

7. **HIGH — reference hashes conflict with existing schemas.** Both canon `reference_assets` and asset `references_applied` are arrays of strings, so “path + `sha256:` suffix” creates an undocumented grammar and cannot express role, path, and hash safely ([asset_manifest.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/asset_manifest.schema.json:45)).  
Fix: Version the schemas and use reference objects such as `{path, sha256, role, visual_bible_asset_id}`.

8. **HIGH — the new continuity rule is overbroad.** Requiring non-empty approved-sheet references for every image/video rejects title cards, abstract inserts, transitions, diagrams, and scenes depicting no tracked entity.  
Fix: Require sheet references only when structured scene/entity refs indicate that an approved character or location is depicted.

9. **HIGH — “single model per scene” is not modelable correctly.** There are no shots or selected-take statuses, and tool output uses full endpoint names while the plan uses `seedance-2.5`; rejected candidates from another model would also trigger the rule.  
Fix: Add `shot_id`, `take_id`, `usage_status`, and a canonical model identifier, then enforce consistency only across selected video takes.

10. **BLOCKER — Seedance cannot currently combine the inputs the plan assumes.** `image_to_video` accepts a start image but no reference set; `reference_to_video` accepts references but ignores a start frame, and there is no `start_image_path` parameter ([seedance_video.py](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:112), [seedance_video.py](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:214)).  
Fix: Verify the official 2.5 request contract and add a tested combined operation, or drop the storyboard-first-frame requirement.

11. **HIGH — reference packing will exceed provider limits.** A storyboard plus location plus several views/wardrobes for multiple characters can exceed the current nine-image ceiling long before the four-character pilot cap.  
Fix: Define a deterministic per-shot packing policy and fail preflight when required identity references exceed the verified endpoint cap.

12. **HIGH — synthetic provenance is trivially forgeable.** `generated_by_pipeline`, prompt, model, and a claimed tool-run ID are self-attested JSON; BaseTool events have no execution ID or output hash and are deliberately non-fatal ([base_tool.py](/Users/ben/Projects/OpenMontage/tools/base_tool.py:182)).  
Fix: Emit immutable generation receipts containing execution ID, provider request ID, normalized inputs, output hash, and cost, then validate artifacts against those receipts.

13. **SECURITY — path validation is underspecified and occurs too late.** Prefix checks do not stop `..`, absolute paths, symlink escapes, cross-project references, or arbitrary local-file upload to FAL before checkpoint enforcement.  
Fix: Resolve every input/output with `strict=True`, reject symlinks and paths outside the active project root, and re-hash immediately before upload.

14. **SECURITY/RELIABILITY — the Seedance network loop is unsafe.** It polls forever, sends the API key to server-supplied status/response URLs, downloads arbitrary URLs fully into memory, and writes output non-atomically ([seedance_video.py](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:258)).  
Fix: Add URL-host allowlists, a total deadline and cancellation path, streamed size/MIME-limited downloads, unique temp files, atomic replacement, and media probing before success.

15. **RACE — repeated sub-gate writes can corrupt audit state.** Checkpoints and decision logs use fixed `.json.tmp` paths and read-modify-write without an interprocess lock, so concurrent candidate workers can clobber checkpoints, decisions, or rollback another writer ([checkpoint.py](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:487), [checkpoint.py](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:702)).  
Fix: Serialize per-project checkpoint/decision/cost commits with a file lock, unique temporary files, and optimistic revision checks.

16. **HIGH — supersession does not preserve old files.** The fixed names such as `hero.png` and `front.png` will overwrite prior approved binaries, contradicting the promise that superseded files remain on disk.  
Fix: Store immutable, content-addressed versions and update a small approved-pointer record rather than overwriting canonical filenames.

17. **BLOCKER — poster ownership is contradictory.** `visual_bible.poster` supposedly records `poster-final.png`, but the plan says final compositing happens later in `compose`; adding the PNG to `render_report.outputs` would also hit canon enforcement that expects every output to contain video.  
Fix: Produce and hash the standalone poster entirely during `visual_bible`, then let compose consume it only as a video title-card asset.

18. **HIGH — the typography decision contradicts itself.** Ideogram-generated title lettering is still in-model typography, despite D6 claiming typography stays outside image models; transparency and exact glyphs are also not guaranteed.  
Fix: Render exact text locally as SVG/HTML/Remotion/FFmpeg using a licensed font and composite it deterministically.

19. **HIGH — budget and wall-time governance do not match the pilot.** The manifest allows $5 and 20 minutes, while the plan expects $300–$1,000 and at least 12–20 two-minute video calls; no persisted project budget override is defined, and CostTracker is not automatically wired to tool calls.  
Fix: Persist a writer-approved project budget in CAP mode, reserve/reconcile each paid call, provide a line-item worst-case estimate, and replace the 20-minute limit with a realistic staged deadline.

20. **HIGH — the existing regression suite will break.** The authored-film test helper hardcodes the old stage order, so adding a prerequisite stage invalidates many later-stage tests; the proposed new test file does not address those fixtures.  
Fix: Update all authored-film fixtures and add tests for partial approvals, resume, concurrency, path/symlink attacks, reference packing, 2.0 compatibility, 2.5 payloads, and poster outputs.

21. **HIGH — the provider contract is explicitly unverified.** The research confirms endpoint listings, not 2.5 parameter names, limits, pricing, or the assumed start-frame/reference combination; the plan also acknowledges missing `requests`/PIL dependencies ([research brief](/Users/ben/Projects/OpenMontage/docs/research/2026-08-25-visual-bible-claudex-research.md:9), [PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:88)).  
Fix: Make official-schema verification, dependency preflight, recorded contract fixtures, and a minimal paid smoke test prerequisites—not post-build mitigations.

22. **SECURITY/PRIVACY — external data egress is ungoverned.** Proprietary story prompts and reference images will be uploaded from the project/iCloud workspace to FAL without an explicit consent, retention, or redaction decision.  
Fix: Add a mandatory provider-egress approval recording what content leaves the machine, its retention terms, and the approved provider.

VERDICT: REVISE
### Claude's response
Verified against code before arbitrating: #2 (`_load_canon` always re-reads the ingest checkpoint — true), #4 (manifest schema `additionalProperties: false` — true), #6 (scene_plan has no character/location/shot ids — true), #10 (image_to_video and reference_to_video are separate ops; no combined call — true), #20 (test helper hardcodes stage order — true).

**Accepted (20):** #1 partial — sheets stay after `proposal` (they need only canon + treatment), storyboards move into `assets` as a gated first sub-step; completion criterion now uses `proposal_packet.cast`. #2 canon_packet immutable; references live in the visual_bible artifact. #3 wired into all consuming stages. #4 project-level `project.yaml` with its own schema instead of a manifest `limits` key. #5 append-only approval receipts written only by the human-gate path. #6 entity ids on canon; `character_refs`/`location_ref`/`shots[]` on scenes. #7 reference objects, schemas versioned. #8 continuity rule narrowed to shots with entity refs. #9 shot/take/usage_status + canonical endpoint; rule applies to selected takes only. #10 2.5 contract verification is a §0 prerequisite; storyboard-as-start-frame is conditional on it. #11 deterministic packing policy + preflight failure. #12 generation receipts from the tool wrapper, fatal on failure. #13 strict path resolution, symlink rejection, project-root confinement, re-hash before upload. #14 Seedance loop hardening (deadline, host allowlist, streamed bounded download, atomic write, ffprobe). #16 content-addressed objects + pointer files. #17 poster finished inside visual_bible; compose consumes it as a video card. #18 Ideogram dropped; titles rendered locally from a font — the plan did contradict D6. #19 project budget cap with reserve/reconcile, staged wall time. #20 fixtures and stage-order helper updated; expanded test list. #21 official contract fixture + dependency preflight + paid smoke as prerequisites. #22 one-time provider-egress ruling in project.yaml gates visual_bible.

**Rejected with reason (1):** #15 interprocess locking — the pipeline is deliberately single-threaded (one entity, one gate at a time; supervised-creation doctrine). Adding locks for a concurrency the design forbids is scope without a caller. Recorded in Risks as the precondition if parallel generation is ever introduced.

**Narrowed (1):** #14 — accepted the hardening items that touch the code path we're already changing; a full rewrite of the polling client is out of scope.

## Round 2 — Codex
The revision addresses most prior findings, including immutable canon, downstream wiring, scoped artifact coverage, generation receipts, path controls, poster ownership, project budgets, provider hardening, and expanded tests. The plan is substantially improved, but several implementation-blocking contradictions remain.

- **Schema migration remains undefined:** The proposed v1.1 canon/proposal/scene/manifest schemas are globally shared, while the current loader selects one schema per artifact and existing artifacts are v1.0; changing `const` versions will break legacy checkpoints and other pipelines. **Fix:** Support v1.0/v1.1 via explicit dispatch or `oneOf`, add migrations, and regression-test every existing pipeline.
- **Human approval receipts do not fit the checkpoint API:** [`write_checkpoint()`](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:547) only records a stage-wide boolean, so it cannot represent per-entity or storyboard sub-gates without prematurely completing the stage. **Fix:** Add a separate `record_human_approval(checkpoint_digest, scope, asset_ids, approver)` operation that does not advance the stage.
- **Receipts do not bind the approved state:** Recording only asset IDs allows prompt blocks, roles, pointers, or entity metadata to change after approval while retaining the same receipt. **Fix:** Sign/hash the complete canonical approval record, including prompt, role mapping, assets, and source checkpoint digest.
- **The output path rule is impossible as written:** `resolve(strict=True)` fails for new output files, and content-addressed destinations are unknown until generation finishes. **Fix:** Strict-resolve inputs, validate the output parent, write to a unique project-local staging file, hash/probe it, then atomically move it into `objects/<hash>`.
- **Paid-call recovery is still non-idempotent:** A provider can complete successfully before receipt append or budget reconciliation fails, causing retries and duplicate charges; optional provider request IDs make recovery impossible. **Fix:** Persist the reservation and request identity before submission, require request IDs for paid async calls, reconcile in `finally`, and resume the existing request after crashes.
- **Local poster production conflicts with the proposed provenance schema:** PIL/SVG/ffmpeg outputs have no model or generation prompt, yet `ImageRef` requires them and every canon asset supposedly requires a generation receipt. **Fix:** Introduce local derivation provenance (`generator_kind`, tool/version, parameters hash, input hashes) and wrap compositor operations so they emit receipts.
- **Storyboard cardinality is contradictory:** [`PLAN.md`](/Users/ben/Projects/OpenMontage/PLAN.md:18) requires one storyboard per scene, but later generation, model selection, and take validation are shot-level; multi-shot scenes cannot map unambiguously. **Fix:** Require one storyboard per `shot_id`, or formally make each scene exactly one shot and remove nested shots.
- **Model enforcement has the wrong scope:** Overrides are stored per shot, while enforcement rejects multiple selected model endpoints within a scene, making legitimate shot-specific overrides invalid. **Fix:** Either move model selection to the scene level or validate endpoint consistency independently per shot.
- **The rejected locking finding is not resolved:** “Single-threaded” describes intended execution, not an enforced single writer; two CLI sessions can still corrupt checkpoints, budgets, or JSONL receipts, and current cost persistence is not crash-atomic. **Fix:** Add a lightweight exclusive per-project run lease plus atomic/fsynced state writes; full parallel-worker locking is unnecessary.
- **Provider-contract coverage is incomplete:** The prerequisite verifies Seedance 2.5, but Seedream and Flux Kontext payloads, limits, pricing, and fallback parity remain assumptions, and the corresponding provider skills/runtime declarations are unspecified. **Fix:** Add official contract fixtures and registered provider skills for every new endpoint before implementation.
- **`runtime_shape` defaults will not appear at runtime:** JSON Schema `default` is annotation-only under the current validation path, so omitted proposal values will remain absent. **Fix:** Make runtime-shape fields required and emitted by the proposal director, or add an explicit normalization/defaulting step.
- **Project approval policy is self-attestable:** An editable `project.yaml` can raise budget or authorize provider egress without a trusted human-gate record. **Fix:** Bind the approved config digest to the decision log and reject budget/egress changes lacking a new human approval receipt.
- **The test plan misses the failure boundaries above:** It does not explicitly cover legacy schemas, approval-state tampering, paid-call crash/resume, staging-path behavior, local derivation receipts, config tampering, or competing processes. **Fix:** Add targeted tests for each boundary before treating the governance controls as enforced.

VERDICT: REVISE
### Claude's response
Verified: all touched artifact schemas pin `version` with `const: "1.0"` (true); `write_checkpoint` has only a stage-wide `human_approved` bool (true); checkpoint writes use a fixed `.json.tmp` path (true).

**Accepted (13/13):** schema `oneOf` 1.0/1.1 with loader dispatch + migrations + cross-pipeline regression; `record_human_approval` as a separate non-advancing operation; receipts hash the full canonical approval record and source checkpoint digest; output path rule rewritten as staging → hash/probe → atomic move; paid-call reservations with required provider request ids, reconcile in `finally`, resume-by-request-id; local-derivation provenance variant with receipts for title/poster; storyboard one-per-`shot_id`; model consistency enforced per shot (override reason required vs scene default); per-project run lease + fsync/atomic writes (accepting the narrowed form of R1 #15 — my rejection was of parallel-worker locking, which is still out; two CLI sessions on one project is a real case); contract fixtures + provider skills for every new endpoint as §0 prerequisites; `runtime_shape` fields required, defaults via explicit normalization only; project.yaml digest bound to a human-approved decision-log entry; failure-boundary tests added for each item.

## Round 3 — Codex
Round 3 incorporates all 13 dispositions listed in [PLAN-REVIEW-LOG.md](/Users/ben/Projects/OpenMontage/PLAN-REVIEW-LOG.md:104), but several mechanisms still fail at important boundaries.

- **The migration fails open:** [PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:23) gives migrated scenes empty entity refs—thereby exempting character-bearing legacy shots from continuity enforcement—and does not define how a v1.0 proposal acquires its required cast IDs. **Fix:** Require explicit human-reviewed cast/ref migration or regeneration; never treat empty synthesized refs as production-valid.
- **Paid-call idempotency still has an unavoidable crash window:** [PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:13) persists the provider request ID only after submission, so a crash after provider acceptance but before persistence can still cause a duplicate charge. **Fix:** Use a provider-supported client idempotency key; otherwise mark the reservation indeterminate and require reconciliation rather than resubmitting.
- **Approval receipts remain forgeable by runtime code:** `record_human_approval(..., approver)` accepts caller-supplied identity, so merely designating it the “human-gate path” does not prevent a director or buggy caller from self-approving assets or config. **Fix:** Require a one-use orchestrator capability bound to the actual user response, record digest, project, and scope; never trust a free-form `approver`.
- **Model policy directly contradicts itself:** [schema/enforcement](/Users/ben/Projects/OpenMontage/PLAN.md:25) allows different per-shot endpoints with reasons, while D4 says overrides are per-scene and models are never mixed within a scene. **Fix:** Choose one invariant and make D4, schema fields, enforcement, and tests identical.
- **Lease recovery can create the concurrency it intends to prevent:** [PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:64) declares a lease stale solely after `wall_time_minutes`; a slow but live process can then overlap a second writer. **Fix:** Use heartbeat plus PID/process-start identity and reclaim automatically only when the owner is demonstrably dead.
- **`asset_manifest` v1.1 remains under-specified:** `storyboard_frame` is absent from the current type enum, while proposed shot/take/model fields cannot sensibly be required for music, fonts, subtitles, and other non-shot assets. **Fix:** Define a discriminated v1.1 union with explicit `storyboard_frame`, shot-bound visual, and non-shot asset variants.
- **Content-addressed image naming assumes unperformed transcoding:** [PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:45) stores every provider image as `.png`, although responses may contain JPEG/WebP bytes; probing does not convert them. **Fix:** Decode and deterministically re-encode as PNG before hashing, or preserve a verified MIME-derived extension.
- **“Canonical JSON” is not a complete hashing contract:** Different Unicode normalization, number serialization, or dictionary ordering can invalidate otherwise identical approvals. **Fix:** Specify one canonicalization algorithm, preferably RFC 8785/JCS, and test Unicode, numeric, and key-order equivalence.
- **The new failure boundaries are not tested:** Current additions cover crash-before-reconcile but not crash-between-submit-and-request-ID, lossy migration bypass, forged approvals, or lease expiry while the owner remains alive. **Fix:** Add explicit adversarial tests for those four cases.

VERDICT: REVISE
### Claude's response
**Accepted (9/9):** migration fails closed — no synthesized refs/cast; migrated scene plans need human-reviewed regeneration, `entity_free` must be explicit. Client idempotency key persisted before submission; `indeterminate` reservations halt for human reconciliation, never resubmit. `record_human_approval` takes a one-use gate token minted at the user's answer, no caller-supplied approver. Model policy restored to the D4 invariant: scene-level endpoint, shots inherit, override per scene with reason — my round 2 wording was the contradiction. Lease uses heartbeat + pid/start-time identity; reclaim only on demonstrably dead owner. `asset_manifest` v1.1 discriminated union (`storyboard_frame`, `shot_visual`, `non_shot`). Deterministic PNG re-encode before hashing. RFC 8785 canonicalization with equivalence tests. Four adversarial tests added.

## Round 4 — Codex
Round 4 addresses paid-call ambiguity, lease liveness, image normalization, JCS hashing, and the requested adversarial tests. The following material gaps remain:

- **Model ownership still contradicts itself:** [PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:25) defines `model_endpoint` and `model_override` inside each shot, then says shots carry no model fields and selection is scene-level. **Fix:** Remove both fields from the `shots[]` definition and define them exclusively on the scene.
- **Migration approval cannot use the specified receipt schema:** Migrated proposals and scene plans require approval receipts at [PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:23), but the receipt kinds at [PLAN.md](/Users/ben/Projects/OpenMontage/PLAN.md:50) contain no proposal, scene-plan, or migration-review kind. **Fix:** Add a generic `artifact_review` receipt bound to artifact type, version, digest, and migration status.
- **The gate token lacks a real trust boundary:** Storing capabilities in project-local `.gate-tokens/` lets code with ordinary project access potentially read or fabricate the same files the verifier trusts. **Fix:** Keep only a token hash in orchestrator-owned state outside agent-writable project storage, use an opaque random/HMAC capability, and consume it atomically.
- **The proposed manifest discriminator destroys existing asset semantics:** Using `type: shot_visual|non_shot` replaces the current `image|video|music|font|subtitle|…` contract in [asset_manifest.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/asset_manifest.schema.json:17), leaving downstream consumers unable to distinguish video from image or music from fonts. **Fix:** Preserve `type` and add a separate `asset_class: storyboard_frame|shot_visual|non_shot`, with conditional required fields.
- **Deterministic migrated IDs can collide:** “name + role slug” is not unique because character roles are optional, locations have no role, and duplicate names are valid. **Fix:** Generate IDs from entity type plus immutable source index/content digest and fail migration if uniqueness validation still finds a collision.

VERDICT: REVISE
### Claude's response
**Accepted (5/5):** model fields removed from `shots[]` (leftover from round 2 text), scene-only. `artifact_review` receipt kind for migrated proposals/scene plans. Gate tokens: opaque random value, HMAC held in orchestrator-owned `~/.openmontage/gates/` outside the project tree, atomic consume. `type` enum preserved; new `asset_class` carries the conditional fields. Migrated ids from entity type + source index + content digest, uniqueness-checked.

## Round 5 — Codex
All five round-4 findings are addressed in the revised plan.

One new material security gap remains:

- **Approval capability can still be bypassed after issuance:** Tokens are protected outside the project, but enforcement trusts unsigned receipts in project-writable `approvals.jsonl`; an agent can fabricate a receipt matching the artifact hash without calling `record_human_approval`. **Fix:** HMAC-sign each receipt with the orchestrator-held key—or cross-check it against an immutable consumed-token ledger outside the project—and test that a forged matching receipt is rejected.

VERDICT: REVISE
### Claude's response
**Accepted (1/1):** receipts HMAC-signed with the orchestrator key and cross-checked against the consumed-token ledger outside the project; forged-matching-receipt test added.

### Resolution: MAX_ROUNDS=5 reached without VERDICT: APPROVED
Convergence: 22 → 13 → 9 → 5 → 1 findings. Every finding across five rounds was accepted except R1 #15 (parallel-worker locking), which was later accepted in its narrowed form (run lease). The last finding is fixed in PLAN.md but has not been re-reviewed by Codex. Handed to Ben: extend rounds, or sign off with the fix applied unreviewed.

Ben extended the loop by one round (MAX_ROUNDS=6).

## Round 6 — Codex
The round-5 finding is addressed: receipts are HMAC-signed, cross-checked against the orchestrator-owned consumed-token ledger, and forged matching receipts are explicitly tested. I found no new material correctness, data-loss, security, or buildability problems.

VERDICT: APPROVED
### Resolution: VERDICT: APPROVED after 6 rounds (5 + 1 extension). Awaiting Ben's sign-off before any code.

## Act 3 — Build (Claude builds; Codex cross-inspects at the end)
### §0 prerequisites — contract findings that deviate from the reviewed plan
- Seedance 2.5 reference-to-video has NO start-frame parameter; images are prompt-addressable references (@ImageN), up to 30. Storyboards are reference-only (plan's conditional branch). Pricing ~$0.47/s at 720p.
- FAL supports no client idempotency key. Applying the plan's fallback branch: reservation persisted before submit, `X-Fal-No-Retry: 1` on every paid call (FAL otherwise auto-retries up to 10x on 503/504 — a real duplicate-charge path), crash between submit and request-id → `indeterminate`, human reconciles.
- Flux Kontext takes ONE reference image; it is a weaker fallback than assumed. Seedream 5 Pro edit (10 refs) is the sheet generator.
- Baseline before changes: 1017 passed, 11 skipped (venv Python 3.10; the earlier 'missing modules' note was the system 3.14 interpreter).

### Build record (2026-08-25, Claude builds, five slices)
- S1 schema versioning (`oneOf` 1.0/1.1, version dispatch, `visual_bible` + `project_config` schemas, migrations that fail closed) — 116 tests.
- S2 state safety (atomic fsync writes, path safety + content-addressed PNG re-encode, RFC 8785 hashing, run lease with liveness, HMAC gate tokens + signed receipts + ledger, paid-call reservations) — 73 tests.
- S3 pipeline wiring (`visual_bible` stage, enforcement for config binding/egress/cast/receipts/hashes/scene 1.1/asset 1.1/model policy/storyboard gate, indeterminate-call halt) — 28 new + helper updates.
- S4 tools (execution ids + fatal generation receipts in the tool wrapper; FAL queue helpers with no-retry, host allowlist, bounded streamed download, deadline+cancel; Seedance 2.5; Seedream 5 Pro; local title_card) — 36 tests.
- S5 skills (visual-bible-director, director updates, WORKFLOW, INDEX, two Layer-3 provider skills).
- Gate handler `scripts/gate_approve.py` (TTY-only; agents cannot mint) — 4 tests.
- Suite: 1017 → 1274 passed, 11 skipped. Nothing committed; awaiting human diff approval after Codex cross-inspection.

## Post-build inspection — round 1 (fresh Codex thread 01a03aa9-fa10-70e3-87f7-b1ab07ede017, read-only)
Static review only; I did not rerun the supplied 1,274-test suite.

- **Blocker — [scripts/gate_approve.py:57](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:57), [lib/gates.py:127](/Users/ben/Projects/OpenMontage/lib/gates.py:127):** `isatty()` is not authorization, `decide()` bypasses it, and pipeline-accessible code can directly mint, consume, sign, and ledger approvals. Every supposedly human-only gate can therefore be self-approved. **Fix:** isolate mint/sign/ledger operations behind an authenticated human-controlled service or OS privilege boundary unavailable to pipeline code.

- **Blocker — [tools/video/_shared.py:889](/Users/ben/Projects/OpenMontage/tools/video/_shared.py:889), [tools/graphics/seedream_image.py:221](/Users/ben/Projects/OpenMontage/tools/graphics/seedream_image.py:221):** paid tools accept any existing directory as `project_dir`, trust caller-injected trackers/caps, and upload before verifying the receipt-bound config or egress ruling. This permits unapproved egress, arbitrary local-image upload, and budget-cap bypass. **Fix:** resolve a registered project root from trusted runtime state and derive egress permission and budget exclusively from its verified config before any network operation.

- **Blocker — [tools/video/seedance_video.py:311](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:311), [tools/graphics/seedream_image.py:251](/Users/ben/Projects/OpenMontage/tools/graphics/seedream_image.py:251):** `request_id` is assigned before `attach_request_id()` persists it; if persistence fails, `finally` reconciles the accepted request as failed instead of leaving it indeterminate. A charged request can then be submitted again. **Fix:** track `request_id_persisted` separately and leave the reservation `submitting` unless persistence succeeded.

- **Blocker — [tools/cost_tracker.py:647](/Users/ben/Projects/OpenMontage/tools/cost_tracker.py:647):** recovery only detects `submitting` reservations without a request ID. A crash after the ID is stored is ignored, and there is no resume-by-request-ID implementation, contrary to PLAN.md §12. **Fix:** resume/poll every nonterminal reservation with a stored request ID before allowing another matching submission.

- **High — [tools/video/seedance_video.py:306](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:306), [tools/graphics/seedream_image.py:245](/Users/ben/Projects/OpenMontage/tools/graphics/seedream_image.py:245):** polling, deadline, download, or verification failures after submission are reconciled as `failed` with `$0`, refunding budget even though FAL may have completed and billed the job. Repeated failures can exceed the approved cap. **Fix:** retain the reserved charge or mark the call pending billing reconciliation whenever submission was accepted.

- **Blocker — [lib/canon_enforcement.py:745](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:745), [lib/canon_enforcement.py:1181](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1181):** storyboard approval is checked only after selected takes exist, so video credits can be spent before approval. The receipt also hashes an unordered set of frame hashes, allowing frames to be swapped between shots. **Fix:** require a signed `shot_id → frame_hash` receipt in Seedance preflight before submission.

- **Blocker — [schemas/artifacts/asset_manifest.schema.json:359](/Users/ben/Projects/OpenMontage/schemas/artifacts/asset_manifest.schema.json:359), [skills/pipelines/authored-film/asset-director.md:69](/Users/ben/Projects/OpenMontage/skills/pipelines/authored-film/asset-director.md:69):** the director requires the storyboard as a packed reference without an entity ID, but the schema requires every reference to have `visual_bible_entity_id`, and enforcement rejects non-visual-bible owners. A compliant storyboard-conditioned take cannot validate. **Fix:** add a discriminated storyboard-reference variant keyed by `shot_id` and manifest asset ID.

- **Blocker — [lib/receipts.py:131](/Users/ben/Projects/OpenMontage/lib/receipts.py:131), [lib/canon_enforcement.py:857](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:857):** generation receipts are unsigned project-local JSON, while enforcement trusts receipt-ID/output-hash presence. An imported or real-person image can be admitted as synthetic by appending a fabricated row, defeating D3/D8. **Fix:** sign and protected-ledger generation receipts, then verify project, execution, provider request, inputs, and output hash.

- **High — [lib/receipts.py:104](/Users/ben/Projects/OpenMontage/lib/receipts.py:104), [lib/gates.py:232](/Users/ben/Projects/OpenMontage/lib/gates.py:232):** approval lookup does not compare the signed `project_id` with the active project, and ledger verification ignores project ID/token HMAC. Copying a valid receipt into another project can approve an identical record there. **Fix:** require the expected project ID and match the complete ledger tuple.

- **High — [lib/checkpoint.py:620](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:620):** generic gated stages still accept caller-supplied `human_approved=True`; `artifact_review` receipts and `source_checkpoint_digest` are never required. Migrated proposal/scene artifacts therefore lack the receipt-bound human review promised in PLAN.md §2. **Fix:** require a verified artifact-review receipt bound to the current artifact and source-checkpoint digest before completing every gated stage.

- **Blocker — [schemas/artifacts/visual_bible.schema.json:7](/Users/ben/Projects/OpenMontage/schemas/artifacts/visual_bible.schema.json:7), [lib/canon_enforcement.py:1026](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1026), [pipeline_defs/authored-film.yaml:145](/Users/ben/Projects/OpenMontage/pipeline_defs/authored-film.yaml:145):** `poster` is optional and conditionally enforced; the manifest omits `title_card`, and no receipted poster-compositor tool exists. The stage can finish without the promised poster, while a compliant `poster_final` has no declared implementation path. **Fix:** require the poster at completion and register both `title_card` and a local receipted compositor.

- **High — [lib/canon_enforcement.py:970](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:970):** completion checks only for missing cast members, not extra or unknown approved entities. Visual canon can contain arbitrary entries outside `proposal_packet.cast`, contrary to the manifest and cast-cap intent. **Fix:** require exact character/location set equality with the proposal cast and verify every ID against canon_packet v1.1.

- **High — [schemas/project_config.schema.json:7](/Users/ben/Projects/OpenMontage/schemas/project_config.schema.json:7), [lib/canon_enforcement.py:1053](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1053):** D4’s approved Seedance 2.5 project default is not represented. Enforcement infers the “default” from whatever endpoints scenes use, so every scene may silently use another endpoint without an override reason. **Fix:** store the default endpoint in receipt-bound config—or enforce the fixed 2.5 endpoint—and compare each scene against it.

- **High — [schemas/project_config.schema.json:27](/Users/ben/Projects/OpenMontage/schemas/project_config.schema.json:27), [lib/canon_enforcement.py:797](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:797):** egress consent may list only `prompts`, yet enforcement merely checks that the object exists before tools upload reference images. **Fix:** require both planned content classes or authorize each class independently at the network boundary.

- **High — [tools/video/seedance_video.py:190](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:190), [tools/video/seedance_video.py:460](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:460):** the class advertises two retries while defaulting to paid 2.5, and the legacy paid path omits `X-Fal-No-Retry`. This violates the Act 3 no-retry decision and risks duplicate charges. **Fix:** expose zero retries for paid Seedance and route every FAL submission through the hardened queue helper.

- **High — [tools/video/seedance_video.py:329](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:329):** video validation fails open when `ffprobe` is missing or errors because `video_codec` is then absent. MIME-labelled arbitrary bytes are accepted as a successful MP4. **Fix:** require successful ffprobe output with at least one valid video stream before moving the file.

- **High — [lib/checkpoint.py:764](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:764):** the run lease is only an optional helper with no production caller or persistent owner; an in-memory lease cannot span this multi-turn gated workflow. Concurrent sessions can still mutate checkpoints, costs, and JSONL state. **Fix:** make a persistent orchestrator own and heartbeat the lease, and require its capability on governed writes and paid calls.

- **High — [lib/receipts.py:78](/Users/ben/Projects/OpenMontage/lib/receipts.py:78):** approval recording consumes the one-use token before separately appending the receipt and protected ledger. A crash between those operations permanently loses an otherwise valid approval. **Fix:** use an idempotent WAL/transaction keyed by token HMAC so receipt and ledger completion can be recovered.
### Claude's dispositions
**Accepted (15):** #2 tools derive a registered project root, drop injected trackers/caps, verify config receipt + egress classes before any upload; #3 request-id-persisted flag, indeterminate on persistence failure; #4/#5 nonterminal reservations block resume, post-accept failures retain the charge as `pending_billing`, new `scripts/reconcile_paid_calls.py` (never resubmits); #6 storyboard receipt is a `shot_id → hash` map and is required for any non-rejected take (spend, not selection) and in Seedance preflight; #7 storyboard reference variant in `references_applied`; #8 generation receipts signed + ledgered; #9 project_id bound in lookups and ledger tuple; #11 poster required at completion, `poster_composite` local receipted tool, tools listed on the stage; #12 exact cast-set equality + canon id existence; #13 `default_video_endpoint` in receipt-bound config, no inferred default; #14 egress classes must cover prompts + reference_images; #15 zero retries on paid paths, legacy 2.0 routed through the hardened helper; #16 ffprobe fails closed; #18 WAL around token consume + receipt append.

**Accepted, narrowed (2):** #1 — `decide()` now also requires a TTY; but an OS privilege boundary is out of scope for a single-user local tool. The gate stops the agent from approving through the pipeline API by process separation and discipline; a malicious local process running as Ben could read the HMAC key. Stated in PLAN risks and the gates module docstring. #10 — migrated (`needs_review`) artifacts now require a signed `artifact_review` receipt to complete; the generic `human_approved=True` stage flag stays, as PLAN §5 specifies.

**Rejected with reason (1):** #17 — a persistent orchestrator process owning a pid-liveness lease across a multi-turn agent session does not exist in this agent-first runtime (every command is a fresh process; Codex's own round 3 forbade heartbeat-only reclaim). The lease guards the long-running script orchestrators; multi-turn sessions rely on atomic writes, receipts, and one human at the gate. Logged as a known limitation.

### Fix round applied (tracks A+B): suite 1274 → 1332 passed, 11 skipped. Reinspection requested.

## Post-build inspection — round 2 (same thread)
Resolved as scoped: #1 and #10 (narrowed), #3, #5, #7, #9, #11–#13, #15–#16, and #18. #8’s signature/ledger trust fix is resolved. #17 was not reopened. #2, #4, #6, and part of #14 remain incomplete.

Remaining/new findings:

- **Blocker — [tools/video/seedance_video.py:445](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:445):** Seedance 2.0 still uploads arbitrary local paths and submits paid calls without registered-project validation, approved egress, budget reservation, storyboard approval, or request-ID recovery. **Fix:** route every 2.0 operation through the governed `paid_call_context`/reserve/attach/reconcile path used by 2.5.

- **Blocker — [tools/cost_tracker.py:502](/Users/ben/Projects/OpenMontage/tools/cost_tracker.py:502):** loading `cost_log.json` overwrites the verified config’s current budget cap with the historical stored cap, so lowering and reapproving the cap has no effect. **Fix:** keep the constructor’s receipt-bound cap authoritative and load only entries/spend state.

- **Blocker — [tools/cost_tracker.py:595](/Users/ben/Projects/OpenMontage/tools/cost_tracker.py:595), [tools/video/_shared.py:936](/Users/ben/Projects/OpenMontage/tools/video/_shared.py:936):** neither paid-call entry point checks existing nonterminal reservations, so a resumed invocation can submit another charged job before checkpoint-time `resume_check()` runs. **Fix:** fail on `resume_check(project_root)` at the paid-call boundary before uploads or reservation/submission.

- **Blocker — [tools/video/seedance_video.py:291](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:291), [lib/canon_enforcement.py:1291](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1291):** storyboard protection is caller-opt-in and validates only a supplied hash; it never proves that the approved frame was uploaded, while artifact enforcement permits zero storyboard references. **Fix:** require governed shot context and exactly one approved, hash-verified storyboard file inserted into the payload and later cited by the take.

- **High — [tools/video/seedance_video.py:382](/Users/ben/Projects/OpenMontage/tools/video/seedance_video.py:382), [tools/base_tool.py:341](/Users/ben/Projects/OpenMontage/tools/base_tool.py:341), [lib/receipts.py:297](/Users/ben/Projects/OpenMontage/lib/receipts.py:297), [scripts/reconcile_paid_calls.py:97](/Users/ben/Projects/OpenMontage/scripts/reconcile_paid_calls.py:97):** reservations become terminal before the signed receipt and protected ledger are durable; recovery skips an existing video without receipting it. A crash leaves a paid output permanently inadmissible and may force duplicate spend. **Fix:** use a recoverable WAL for output/receipt/ledger/terminal-state completion and verify plus receipt existing recovered outputs.

- **High — [scripts/gate_approve.py:100](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:100):** an agent-written request can supply an absolute or `../` `request_id`; after human approval/decline, `decide()` can move or unlink a file outside `.gate-requests`. **Fix:** require a strict request-ID grammar, equality with the selected filename stem, and resolved-path confinement for every move/unlink.

- **High — [tools/graphics/poster_composite.py:116](/Users/ben/Projects/OpenMontage/tools/graphics/poster_composite.py:116), [lib/canon_enforcement.py:915](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:915):** the compositor accepts unreceipted input images, and enforcement compares only receipt ID/output hash—not signed tool/model/input provenance—allowing imported imagery to be laundered into synthetic canon. **Fix:** require verified receipts for local-derivation inputs and bind/compare every provenance field against the signed generation receipt.

- **Medium — [schemas/artifacts/__init__.py:136](/Users/ben/Projects/OpenMontage/schemas/artifacts/__init__.py:136), [lib/canon_enforcement.py:1221](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1221):** asset IDs and `(shot_id, take_id)` pairs need not be unique, and an asset’s `scene_id` need not own its `shot_id`; ambiguous or misgrouped takes can validate. **Fix:** enforce global asset-ID uniqueness, per-shot take-ID uniqueness, and exact `scene_id`/shot ownership.
### Claude's dispositions (inspection cap reached — no third Codex round; fixes below are Claude-arbitrated)
**Fixing now (4):** cost_tracker loading must not override the receipt-bound cap; `resume_check` at the paid-call boundary before any upload; `gate_approve.py` strict request-id grammar + path confinement; Seedance 2.0 blocked for authored-canon projects (any project with project.yaml) and routed through the same governed context otherwise; plus the cheap uniqueness checks (asset ids global, take ids per shot, scene owns shot).
**Deferred to the next slice, before the Bloodless pilot (3):** storyboard frame proven in the payload and cited by the take (needs asset-director + tool + enforcement change together); recoverable WAL for output→receipt→ledger→terminal ordering and receipting recovered outputs; compositor input provenance + full provenance-field comparison against the signed receipt.
### Final fixes applied: suite 1350 passed, 11 skipped. Presented to Ben for diff approval; nothing committed.

### Deferred slice applied (storyboard-in-payload proof, generation WAL, compositor/provenance comparison): 1350 → 1371 passed. Smoke scaffold added (projects/smoke-visual-bible, scripts/smoke_visual_bible.py).
