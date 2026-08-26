# Plan Review Log: D10 look locks
Plan drafted 2026-08-26 after recon of story-wayfinder, WriterOS, OpenMontage. MAX_ROUNDS=5.

## Round 1 — Codex (thread 01a03e99-3769-7283-a64d-b0325e0be25d)
All requested paths were readable. No files were modified.

The diagnosis is partly right—appearance fields are absent and the annex is deferred—but the proposed authority, identity, and projection contracts do not match the implementation.

## Concrete flaws

1. **`entity_id` cannot “match the canon_packet id.”** Wayfinder runs before OpenMontage assigns IDs; WriterOS character IDs are unconstrained strings, locations have no IDs, and legacy canon migration generates hashed IDs such as `character-0-<hash>`, not slugs ([documents.ts](/Users/ben/Projects/WriterOS/shared/documents.ts:402), [artifacts.py](/Users/ben/Projects/OpenMontage/lib/migrations/artifacts.py:38)).  
Fix: Establish immutable cross-repo entity IDs before look tickets, or carry a ratified `(kind, source entity ID) → canon_packet ID` mapping.

2. **The ratification diagnosis is wrong.** The Canon Note and shared memory do not ratify tickets; the adapter declares `approval: explicit` solely from file placement, `type`, `mode`, and an `## Answer` ([wayfinder.ts](/Users/ben/Projects/WriterOS/server/projectMemory/adapters/wayfinder.ts:259), [wayfinder.ts](/Users/ben/Projects/WriterOS/server/projectMemory/adapters/wayfinder.ts:357)).  
Fix: Require an approval receipt or WriterOS promotion action tied to the look-spec hash instead of trusting self-asserted Markdown headers.

3. **Direct Wayfinder fallback bypasses WriterOS authority and safety checks.** Current Canon Director mines every resolved ticket, while the plan does not require the direct parser to verify `grill + hitl`, exact Answer state, safety, conflicts, or ratification evidence ([canon-director.md](/Users/ben/Projects/OpenMontage/skills/pipelines/authored-film/canon-director.md:31)).  
Fix: Put authority validation in one shared ingestion routine and apply it identically to WriterOS and direct-ticket sources.

4. **`status: ratified` is self-attested data, not proof.** An agent or hand-edited ticket can write that string and thereby satisfy the proposed OpenMontage gate.  
Fix: Remove ratification from the payload and derive it from an external approval record containing the source hash and human receipt ID.

5. **The stored Story Bible annex is another mutable canon copy.** `projectPackage.ts` serializes whatever is in application state; nothing in the plan updates that state after a memory import or proves that hand-edited annex bytes equal the memory snapshot ([projectPackage.ts](/Users/ben/Projects/WriterOS/client/src/lib/projectPackage.ts:249), [projectPackage.ts](/Users/ben/Projects/WriterOS/client/src/lib/projectPackage.ts:271)).  
Fix: Render the annex dynamically from the current memory snapshot, or treat a stored annex strictly as a cache validated against snapshot revision and content hash.

6. **There is no mechanism that creates or refreshes the projection.** `storyBibleToMarkdown` receives only the document, not project memory, while the CLI publishes records one at a time and can stop partially ([documentMarkdown.ts](/Users/ben/Projects/WriterOS/client/src/lib/documentMarkdown.ts:127), [cli.ts](/Users/ben/Projects/WriterOS/server/projectMemory/cli.ts:511)).  
Fix: Add an explicit post-import projection service using the final durable snapshot, or compute the annex only at read/export time.

7. **All location looks will be “unlinked.”** `StoryBibleDocumentContentSchema` has characters but no locations collection, exactly as the V1 PRD states ([documents.ts](/Users/ben/Projects/WriterOS/shared/documents.ts:453), [story-bible PRD](/Users/ben/Projects/WriterOS/docs/product/story-bible-story-coach-redesign-prd.md:38)).  
Fix: Keep annex entity references independent of Story Bible sections until a real location schema and ID migration exist.

8. **“Annex wins” violates existing precedence.** Canon Director says the project Canon Note has highest authority; a stale materialized projection cannot outrank its own current source merely because it came through WriterOS ([canon-director.md](/Users/ben/Projects/OpenMontage/skills/pipelines/authored-film/canon-director.md:35)).  
Fix: Accept the annex only when its source hash/revision matches current memory; otherwise fail closed and require re-projection.

9. **Reopening, deletion, and supersession leave stale active looks.** The adapter’s dedupe key includes the content hash, so an edited ticket creates another record; reopening explicitly does not retract old canon, and the plan defines no tombstone or unique-active-look invariant ([SKILL.md](/Users/ben/.claude/skills/story-wayfinder/SKILL.md:169), [wayfinder.ts](/Users/ben/Projects/WriterOS/server/projectMemory/adapters/wayfinder.ts:346)).  
Fix: Key look records by `(entity_kind, entity_id)` and require explicit `supersedes`/tombstone handling before a replacement becomes active.

10. **Spoiler handling is wired to the wrong field and leaks into projections.** The adapter reads only a top-level ticket header named `spoiler`, while the plan puts it inside YAML; `memory/canon.md` includes all active clear canon and merely labels spoilers ([wayfinder.ts](/Users/ben/Projects/WriterOS/server/projectMemory/adapters/wayfinder.ts:367), [projections.ts](/Users/ben/Projects/WriterOS/server/projectMemory/projections.ts:126)).  
Fix: Propagate spoiler classification from the validated payload and produce audience-filtered projections that omit spoilers by default.

11. **Canon packet 1.2 will immediately fail current enforcement.** `_check_visual_bible` explicitly requires the canon packet version to equal `1.1`, not “1.1 or later” ([canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1128)).  
Fix: Add the 1.2 schema branch, migration/tests, and update every exact-version guard before emitting any 1.2 packet.

12. **The proposed “cannot start” gate is actually a completion gate.** `_check_visual_bible` returns before cast checks unless status is `completed`, and enforcement runs during checkpoint validation ([canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1086), [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1477)).  
Fix: Add a real stage-entry preflight invoked before generation and independently retain completion-time validation.

13. **`approved_prompt_block` cannot store ticket and revision metadata.** It is currently a prompt string copied verbatim into provider requests and sealed by the approval receipt ([visual_bible.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/visual_bible.schema.json:43)).  
Fix: Version the visual-bible schema with a separate `look_spec_ref {kind,id,source_hash,memory_revision,record_id}` included in the approval receipt.

14. **WriterOS memory cannot accept `lookSpec` today.** Both publish inputs and stored records are strict schemas without a payload field, so this change touches event/store/snapshot compatibility—not merely the adapter and projection ([projectMemory.ts](/Users/ben/Projects/WriterOS/shared/projectMemory.ts:105), [projectMemory.ts](/Users/ben/Projects/WriterOS/shared/projectMemory.ts:167)).  
Fix: Define a versioned discriminated payload schema and migrate publish events, records, snapshots, exports, promotion, and round-trip tests together.

15. **The dependency contract uses incompatible identifiers.** Wayfinder `blocked-by` contains ticket titles, while `depends_on` contains slugs; duplicated dependency lists can disagree and neither adapter nor runtime verifies equivalence ([SKILL.md](/Users/ben/.claude/skills/story-wayfinder/SKILL.md:107)).  
Fix: Give tickets immutable IDs and derive `depends_on` from `blocked-by` rather than storing both independently.

16. **The look schema is underspecified.** It lacks conditional required fields by entity kind, array/string limits, normalization, composite uniqueness, duplicate-fence behavior, and rules for `shape` versus `ratified`.  
Fix: Make `look_spec` a strict versioned `oneOf` schema with bounded fields, semantic validation, and a unique `(entity_kind, entity_id)` key.

17. **Character and location namespaces can collide.** Canon IDs are unique only within their respective arrays, while `look-<entity>` filenames and unqualified ticket references collapse the two namespaces.  
Fix: Use `look-character-<id>` and `look-location-<id>` everywhere and always compare composite keys.

18. **The privacy/likeness controls are inadequate.** Omitting a celebrity’s name does not prevent an identifiable likeness; “synthetic-only” proves the image was generated, not that its subject is fictional, and egress consent does not establish likeness rights ([canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:822), [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:936)).  
Fix: Add fictional/real-subject classification, likeness/reference provenance, explicit consent receipts, minor restrictions, and protected-trait minimization before any provider egress.

19. **“Verbatim” prompt propagation is unsafe.** Free-form negative lines and prompt text can contain injection-like instructions, provider-disallowed content, private facts, or formatting that changes prompt semantics; direct Wayfinder ingestion would bypass the existing adapter scan.  
Fix: Preserve meaning through a structured prompt builder with validation and escaping, while separately hashing the ratified source text for fidelity.

20. **The ticket fan-out contradicts both Wayfinder scope and this plan’s trailer limit.** Slice 1 creates looks for every principal and recurring location, while the risk section says only the ≤4 trailer cast needs them; it also forces tickets that may not be sharp or inside the map’s destination ([SKILL.md](/Users/ben/.claude/skills/story-wayfinder/SKILL.md:229)).  
Fix: Create look tickets lazily from an approved production cast, or make visual-development an explicit Wayfinder destination extension.

21. **Legacy projects have no rollout path.** Enabling the hard gate would strand existing v1.0/v1.1 packets, resumed visual-bible runs, and projects explicitly excluded from retrofitting.  
Fix: Ship consumer support first, provide a reviewed backfill/migration path, and enable the hard gate only for newly emitted 1.2 packets until migration completes.

22. **The cited memory contract is not a dependable normative source.** It labels itself “proposed, not applied,” while the installed skill and adapter have since diverged, including split-root behavior ([memory-contract.md](/Users/ben/Projects/WriterOS/integrations/story-wayfinder/memory-contract.md:3)).  
Fix: Reconcile and version the live skill, adapter contract, and integration document before adding another cross-repo format.

## Simpler safe alternative

- First ship a strict `look_spec` schema plus an OpenMontage direct Wayfinder parser that applies the existing canon-ingest human gate; do not persist a Story Bible annex yet.
- Then add typed WriterOS memory payloads and render the annex dynamically from the current snapshot, avoiding `documents/story-bible.json` as a second stored copy.
- Finally establish cross-repo entity IDs, backfill eligible projects, and enable the visual-bible start gate only after source-hash and approval-receipt validation are operational.

VERDICT: REVISE
### Claude's response
Verified the decisive claim: the wayfinder adapter declares `approval: explicit` from placement/type/mode/Answer alone (adapters/wayfinder.ts:259,357) — my "ratifies" language was wrong. Adopted Codex's simpler ordering.

**Accepted (20):** #1 namespaced keys + id_map now, cross-repo ids in Slice C; #2/#4 ratification is an external promotion record bound to the block hash (D12); #3 one shared ingestion routine for all sources; #5/#6 annex rendered dynamically, never stored (D14); #7 annex entries independent of bible sections; #8 annex accepted only on matching revision/hash, else fail closed; #9 unique active look per key, supersedes/tombstone; #10 spoiler from validated payload, audience-filtered projections; #11 canon_packet 1.2 branch + relaxed version guards; #12 real stage-entry preflight plus completion validation; #13 `look_spec_ref` in visual_bible v1.1 and the approval record; #14 versioned discriminated payload across events/records/snapshots; #15 immutable ticket ids, depends_on derived; #16 strict oneOf schema with bounds; #17 namespaced filenames/keys; #19 structured prompt builder, no verbatim propagation; #20 lazy fan-out from an approved production cast (D15); #21 gate only for ≥1.2 packets until backfill; #22 contract reconciliation as a Slice B prerequisite.
**Accepted, narrowed (1):** #18 — fictional-subject attestation, minors refused, heritage writer-only, no real-photo references; proving likeness rights for a generated face beyond that is not achievable and is out of scope (D16).
**Rejected (0).**

## Round 2 — Codex
Round 2 is materially stronger. The dynamic annex, strict schema, unified ingestion, spoiler filtering, prompt construction, lazy fan-out, legacy rollout, and memory-contract prerequisite address most prior findings. Identity, ratification, supersession, preflight enforcement, and privacy remain incomplete.

No files were modified.

## Remaining and new material findings

1. **New blocker: Slice A is cyclic.** Look tickets are created from `proposal_packet.cast`, but the pipeline orders `canon_ingest → proposal → visual_bible`, while those tickets must already exist for canon ingest to emit canon packet 1.2 ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:44), [authored-film.yaml](/Users/ben/Projects/OpenMontage/pipeline_defs/authored-film.yaml:87)).  
Fix: Add a post-proposal `look_lock` stage producing a canon amendment/look packet, or require cast selection before canon ingest.

2. **New blocker: `source_hash` is self-referential.** It is inside the fenced block while defined as the SHA-256 of that same block, so writing the hash changes the bytes being hashed ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:20)).  
Fix: Compute the digest over canonicalized payload with `source_hash` omitted, and store the digest in an external envelope/receipt.

3. **Identity remains broken.** IDs such as `character:<slug>` violate the existing ID regex in canon, proposal, visual-bible, scene-plan, and asset-manifest schemas; changing hashed canon IDs also invalidates an already-approved proposal cast and downstream references ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:49), [proposal schema](/Users/ben/Projects/OpenMontage/schemas/artifacts/proposal_packet.schema.json:1900), [visual-bible schema](/Users/ben/Projects/OpenMontage/schemas/artifacts/visual_bible.schema.json:95)).  
Fix: Preserve existing bare canon entity IDs and use `(entity_kind, entity_id)` as the look key; defer actual ID replacement to a coordinated all-artifact migration.

4. **WriterOS promotion is not equivalent to an OpenMontage receipt.** WriterOS promotion events contain revision and record IDs but no signature or trusted external ledger, so OpenMontage cannot authenticate a detached export claiming promotion ([projectMemory.ts](/Users/ben/Projects/WriterOS/shared/projectMemory.ts:289), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:55)).  
Fix: Require a signed WriterOS export verifiable by OpenMontage, or require an OpenMontage `look_lock` receipt regardless of WriterOS promotion.

5. **The stage-entry gate is still voluntary.** Calling `preflight_visual_bible` from director prose and selected run scripts is bypassable; it is not a hard runtime invariant ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:47)).  
Fix: Enforce it when writing the visual-bible `in_progress` checkpoint and again at every paid/local generation boundary.

6. **Look supersession still does not invalidate an approved sheet mechanically.** Adding `look_spec_ref` to the receipt proves what was approved, but no specified check compares its hash with current canon; the existing visual supersession model cannot replace the same entity cleanly because IDs are unique and approved IDs must equal the proposal cast ([canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1115), [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1155)).  
Fix: Require current-canon hash equality at visual-bible and downstream gates, and model sheet revisions/history separately from the stable entity ID.

7. **Existing Wayfinder tickets cannot satisfy the new dependency-ID contract.** Bloodless’s existing blocker tickets lack `id:`; retroactively adding IDs changes their content hashes and can re-import them as duplicate canon records ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:44)).  
Fix: Provide a reviewed ticket-ID migration with memory supersession, or identify legacy dependencies by stable source path plus original hash without editing old tickets.

8. **The strict shape omits fields required later by its own rules.** Ratification requires `supersedes: <source_hash>`, but `supersedes` is absent from the declared common shape; dual promotion records are allowed, but `look_spec_ref` contains only one `receipt_id` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:18), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:38), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:81)).  
Fix: Add an external `supersedes_source_hash` and `promotion_refs[]`, or require exactly one authoritative promotion per ingestion.

9. **The narrowed likeness boundary is not enforceable as written.** D16 promises “no real-photo references,” but the schema carries no reference provenance and existing canon `reference_assets` can still cause reference images to be uploaded ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:72), [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:368)).  
Fix: Add reference-asset provenance/classification and reject real-person or unconsented references before visual-bible egress.

10. **Normative text still contradicts the revision.** D10 still says Canon Note/shared memory ratifies, the assumptions incorrectly claim Canon Director explicitly supports `.writeros` documents, and the risk section still claims synthetic-only enforcement rejects real-person descriptions ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:14), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:75), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:79)).  
Fix: Make D12 the sole authority statement, specify the WriterOS export format/path explicitly, and describe synthetic-only enforcement accurately.

VERDICT: REVISE
### Claude's response
**Accepted (10/10):** #1 new `look_lock` stage between proposal and visual_bible producing `look_packet`; canon_packet stays 1.1 (no cycle, no id rewrite). #2 `look_hash` over canonical payload, stored only in the receipt envelope. #3 bare ids kept; key = (kind, id). #4 OpenMontage receipt is the only ratification; WriterOS promotion pre-fills. #5 enforced in write_checkpoint (in_progress onward) and at every generation boundary. #6 `look_ref` + `sheet_revision`; hash equality at every gate. #7 legacy tickets untouched, referenced by path+hash; ids for new tickets only. #8 `supersedes_look_hash` and `promotion_refs[]` in the envelope. #9 non-pipeline reference images refused at egress. #10 text corrected; D12 is the sole authority statement.

## Round 3 — Codex
Round 2’s core corrections are present, but five material problems remain:

1. **Legacy rollout cannot work as described.** Projects store only `pipeline_type: authored-film`; runtime always loads the current manifest, so changing it to 1.2 adds `look_lock` to every existing project despite the claimed opt-in rollout ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:49), [checkpoint.py](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:262), [pipeline_loader.py](/Users/ben/Projects/OpenMontage/lib/pipeline_loader.py:59)).  
Fix: Pin a manifest version/digest per project or publish `authored-film-1.2` as a separate pipeline type with explicit migration.

2. **The generation boundary remains bypassable.** Initial `seedream_image` calls require only `prompt` and `project_dir`; they carry no mandatory entity or look reference, so `paid_call_context` cannot determine which active look to verify ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:49), [seedream_image.py](/Users/ben/Projects/OpenMontage/tools/graphics/seedream_image.py:102)). The existing downstream contract also still mandates verbatim `approved_prompt_block` propagation ([visual_bible.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/visual_bible.schema.json:43), [asset-director.md](/Users/ben/Projects/OpenMontage/skills/pipelines/authored-film/asset-director.md:160)).  
Fix: Require signed `look_refs[]`/asset class on governed visual calls, bind them into generation receipts, and explicitly replace the verbatim prompt-block contract with the structured builder.

3. **Reference images can still be laundered into “synthetic-only” canon.** Seedream accepts local paths and arbitrary URLs, uploads them before provenance validation, and its generation receipt does not record `references_applied`; the resulting synthetic output is nevertheless receipted and can pass the proposed rule ([seedream_image.py](/Users/ben/Projects/OpenMontage/tools/graphics/seedream_image.py:228), [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:276)).  
Fix: Before upload, reject remote URLs and require every local reference to have recursively verified pipeline provenance; record the exact reference lineage in the signed receipt.

4. **Supersession does not invalidate completed work.** Enforcement runs when checkpoints are written, while resume logic trusts existing `completed` statuses; approving a replacement or tombstone therefore leaves completed visual-bible and downstream checkpoints usable indefinitely ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:50), [checkpoint.py](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:812), [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1435)). The tombstone receipt shape/action is also undefined.  
Fix: Define signed `activate|retire` receipt actions and make resume/read/delivery validation invalidate all checkpoints derived from a non-current look.

5. **Slice C is not a safe ID migration.** It retires `id_map` while mentioning only packet backfill, but IDs are embedded throughout proposal casts, visual bibles, scene plans, manifests, and signed approval receipts that cannot be rewritten without invalidating signatures ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:60), [canon_packet.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/canon_packet.schema.json:752), [receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:208)).  
Fix: Preserve an immutable historical alias map and specify an atomic all-artifact migration with reapproval wherever signed entity IDs change.

VERDICT: REVISE
### Claude's response
**Accepted (5/5):** #1 per-project pinned manifest version (`project.json.pipeline_manifest_version`, `authored-film@<version>.yaml`), 1.2 opt-in by receipted decision; #2 governed visual calls require `look_refs[]` or explicit `entity_free`, bound into receipts; `approved_prompt_block` replaced by `prompt_recipe` + structured builder; #3 remote reference URLs rejected, recursive receipted lineage required, Seedream records `references_applied`; #4 `activate|retire` receipt actions and `validate_resume` marking derived checkpoints `stale`; #5 ids never rewritten — append-only alias map, atomic receipted migration with re-approval of a new sheet revision.

## Round 4 — Codex
All five Round 3 findings are explicitly addressed in the revised plan. New material blockers:

1. **Manifest pinning is neither self-contained nor trusted.** Checkpoints contain only `pipeline_type`, and stage validation resolves manifests from that value alone; an editable `project.json.pipeline_manifest_version` cannot support standalone validation and can be changed to `1.1` to bypass `look_lock` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:49), [checkpoint.py](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:185)). An `approval_policy` decision is not itself a signed receipt.  
Fix: Bind `{pipeline_name, version, manifest_digest}` in every checkpoint and a dedicated signed migration receipt, then require that tuple throughout loader and checkpoint APIs.

2. **`entity_free: true` is a caller-controlled canon bypass.** A caller can label a character-generating prompt entity-free and avoid providing `look_refs`; the generation boundary cannot infer the lie from arbitrary prompt text ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:49)).  
Fix: Derive entity-free status from an approved scene/asset record server-side, forbid it for visual-bible model generation, and never trust the tool-call boolean alone.

3. **Resume validation risks destroying checkpoint history.** `read_checkpoint` is currently read-only, while the plan makes it rewrite completed checkpoints to a new `stale` status; `stale` is absent from the checkpoint schema and direct mutation would erase the authoritative completed state rather than append an invalidation ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:50), [checkpoint schema](/Users/ben/Projects/OpenMontage/schemas/checkpoints/checkpoint.schema.json:15), [checkpoint.py](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:780)).  
Fix: Keep checkpoints immutable and append signed invalidation records that resume/prerequisite/delivery projections consult.

4. **Migrated visual work can still launder unknown reference ancestry.** Existing Seedream receipts omit `references_applied`; Slice C proposes attaching look refs and reapproving those existing images, which cannot prove whether their original generation used an imported photo ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:62), [seedream_image.py](/Users/ben/Projects/OpenMontage/tools/graphics/seedream_image.py:307)).  
Fix: Migration must reject and regenerate any reference-capable historical output whose signed receipt lacks complete input lineage; reapproval alone is insufficient.

5. **The alias map becomes an unsigned canon-routing surface.** Anyone able to append to `canon/entity-aliases.jsonl` can associate a WriterOS look with the wrong canon entity while all original receipts remain individually valid ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:61)).  
Fix: Require signed, ledgered alias approvals with uniqueness/no-remap enforcement and verify them during every alias resolution.

VERDICT: REVISE
### Claude's response
**Accepted (5/5):** #1 pipeline tuple `{name, version, manifest_digest}` in every checkpoint, pinned by a signed `pipeline_migration` receipt; #2 entity-free derived from the approved scene-plan record server-side, never from the call, and never for visual_bible generation; #3 checkpoints immutable — signed invalidation records consulted by resume/prerequisite/delivery; #4 migration refuses and regenerates any historical image lacking complete receipted lineage; #5 aliases are signed, ledgered, unique, no-remap receipts verified at every resolution.

## Round 5 — Codex
All five Round 4 findings are represented in the revised plan. Four new blockers remain:

1. **An old manifest receipt can authorize a downgrade.** After migration, both valid 1.1 and 1.2 `pipeline_migration` receipts remain ledgered; changing the cache back to 1.1 can match the old receipt and bypass `look_lock` unless there is a unique active receipt chain ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:49)).  
Fix: Make migration receipts a monotonic supersession chain and accept only its unique ledger tip; downgrades require a new human-approved receipt.

2. **Immutable legacy checkpoints cannot satisfy the new required tuple.** Existing checkpoints lack `pipeline {name, version, manifest_digest}`, while the plan keeps checkpoints immutable and makes standalone validation depend on that tuple; requiring it in the current checkpoint schema would make existing history unreadable ([checkpoint schema](/Users/ben/Projects/OpenMontage/schemas/checkpoints/checkpoint.schema.json:7), [plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:49)).  
Fix: Add a backward-compatible checkpoint schema branch and externally bind each legacy checkpoint digest to the signed 1.1 manifest without rewriting it.

3. **Deleting the invalidation projection restores retired canon.** Signed rows prevent forgery but not deletion; if `invalidations.jsonl` is missing or truncated, resume and delivery can again accept completed checkpoints even though the authoritative retire receipt remains ledgered ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:50)).  
Fix: Derive invalidations from the approval ledger on every validation, treating the project JSONL as a rebuildable cache whose completeness is verified fail-closed.

4. **The invalidation rule does not identify downstream descendants.** Only visual-bible entries gain `look_ref`; assets/edit/compose checkpoints do not bind predecessor checkpoint digests, so “checkpoints whose `look_ref` matches” cannot reliably determine which downstream checkpoints consumed the retired sheet ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:50)).  
Fix: Invalidate every transitive downstream stage conservatively, or bind exact predecessor checkpoint digests into each checkpoint and traverse that dependency graph.

VERDICT: REVISE
### Claude's response
**Accepted (4/4), applied unreviewed:** #1 monotonic migration-receipt chain, unique ledger tip, downgrade needs a new receipt; #2 backward-compatible checkpoint schema branch + external digest binding for legacy checkpoints; #3 invalidations derived from the ledger on every validation, JSONL is a fail-closed cache; #4 conservative transitive downstream invalidation now, `predecessors[]` bound in 1.2 checkpoints for exact traversal later.

### Resolution: MAX_ROUNDS=5 reached without VERDICT: APPROVED
Convergence 22 → 10 → 5 → 5 → 4; every finding across five rounds accepted (one narrowed: R1#18 likeness). Rounds 3–5 were governance mechanics (pins, invalidation, aliases) rather than the workflow design, which has been stable since round 2. Handed to Ben: extend, or sign off with the last four fixes unreviewed.

Ben approved 2026-08-26 with one caveat (headshots stage + reference-image import, JPEG/HEIC/PNG, both generated-elsewhere and real-person casting inspiration). Slice A′ added; extension round 6 requested on it.

## Round 6 — Codex (extension, Slice A′)
Round 5’s four findings are addressed in the revised plan: unique migration-ledger tip, legacy checkpoint compatibility, ledger-derived invalidation, and conservative downstream invalidation with predecessor binding ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:49), [review log](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10-REVIEW-LOG.md:176)).

New material blockers in Slice A′:

1. **Casting inspiration can mutate canon after ratification.** `headshots` follows `look_lock`, yet the director may use a casting image to change age band, build, or era signals in the already-approved look specification; that either bypasses `look_hash` or leaves the headshot inconsistent with canon ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:53)).  
   Fix: Collect casting inspiration before look approval, or route every derived look-field change back through a new `look_lock` receipt and invalidation.

2. **“Read by the director” contradicts “never uploaded to any provider.”** Supplying the real-person image to a remote/multimodal director uploads it, while derived facial descriptions can reproduce likeness even without the person’s name ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:58)).  
   Fix: Never place those pixels in agent/model context; permit only Ben-entered allowlisted type fields or an explicitly local-only vision process, excluding biometric or facial descriptors.

3. **The `awaiting_human` checkpoint cannot contain the specified artifact.** `headshot_packet` requires a selected `hero` and approval receipt, but the checkpoint must be written before selection; current checkpoint enforcement requires every manifest-produced artifact even at `awaiting_human` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:55), [checkpoint.py](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:355)).  
   Fix: Define a pending packet variant with candidates and no hero, or emit a separate `headshot_candidates` artifact before producing the final packet.

4. **Rejected candidates remain valid receipted lineage.** A rejected candidate still has a generation receipt and can satisfy generic lineage validation; only director prose says sheets use the approved hero ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:55)).  
   Fix: Require every character-sheet generation call to provide an approved `headshot_ref`, verify its asset and receipt against the current packet, and bind that reference into the generation receipt.

5. **The new imported provenance class does not fit existing contracts.** Receipts and `ImageRef` validation accept only `model` or `local`, so `generator_kind: imported` cannot currently be created or validated ([receipts.py](/Users/ben/Projects/OpenMontage/lib/receipts.py:48), [visual_bible.schema.json](/Users/ben/Projects/OpenMontage/schemas/artifacts/visual_bible.schema.json:135), [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:959)).  
   Fix: Explicitly add and validate the imported branch across receipt creation, artifact schemas, provenance comparison, recursive lineage, and local-composition enforcement.

6. **Origin laundering is possible for identical pixels.** Because classification is receipt-selected and casting images are protected chiefly by their directory, the same normalized image can be copied and re-imported as synthetic; the headshot approval record also omits its origin and import receipt ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:56)).  
   Fix: Bind normalized pixel hash, origin, import receipt, and provenance into headshot approval; prohibit conflicting origins for one hash and make `casting_inspiration` taint permanently dominant project-wide.

7. **Existing invalidation rules skip the newly inserted stage.** Retiring a look currently invalidates `visual_bible` onward, leaving a stale completed `headshots` checkpoint eligible for reuse; replacing an approved headshot likewise has no defined downstream invalidation ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:50)).  
   Fix: Derive invalidation from look and headshot activate/retire receipts, invalidating `headshots` onward for look changes and `visual_bible` onward for headshot changes.

8. **“Every cast entity” includes locations.** The proposal contract separates `character_ids` and `location_ids`, but Slice A′ requires one headshot hero per cast entity, making completion demand headshots for locations ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:59), [canon_enforcement.py](/Users/ben/Projects/OpenMontage/lib/canon_enforcement.py:1095)).  
   Fix: Scope headshot completeness strictly to `proposal.cast.character_ids`; handle location imports only within `visual_bible`.

9. **HEIC normalization is underspecified and can change the approved image.** Stripping EXIF without applying orientation and dropping ICC without converting color space can make the stored PNG visually differ; multi-frame HEIF inputs are also ambiguous ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:56)).  
   Fix: Specify orientation normalization, conversion to a fixed sRGB pixel format, deterministic alpha handling, primary-frame-only or multi-frame rejection, decode limits, and cleanup of temporary source bytes on every failure path.

VERDICT: REVISE
### Claude's response
**Accepted (9/9):** #1 reference question moves into `look_lock` before the hash is sealed; #2 casting images are Ben-only — no model or director ever receives pixels or facial descriptors; Ben types allowlisted type fields; #3 `headshot_candidates` artifact at awaiting_human, `headshot_packet` at completion; #4 `headshot_ref` required and receipt-bound on every sheet generation; #5 `imported` provenance branch added across receipts/schemas/enforcement; #6 headshot approval binds pixel hash + origin + import receipt; casting taint is permanent and hash-based; #7 look changes invalidate `headshots` onward, headshot changes invalidate `visual_bible` onward; #8 headshots scoped to `cast.character_ids`; #9 full normalization spec (orientation, sRGB, alpha, single frame, limits, cleanup).

### Resolution after extension: 6 rounds used, Slice A′ reviewed once, 9 fixes applied unreviewed. Handed to Ben.

Ben extended: round 7 on Slice A′.

## Round 7 — Codex (extension)
Round 6 disposition check: findings #2, #4, #5, #8, and #9 are addressed. Findings #1, #3, #6, and #7 remain incomplete at the contract level.

1. **Reference-derived fields still bypass Wayfinder ownership.** Ben now enters type fields before ratification, but the authoritative look is a resolved Wayfinder ticket; the plan never routes those fields back through the writer/ticket before `look_ingest` hashes it ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:44), [Slice A′](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:56)).  
   Fix: Ask the reference question while drafting the Wayfinder ticket and require the writer to resolve the resulting complete look block; OpenMontage must never patch it at approval time.

2. **The two-artifact solution still violates the checkpoint contract.** A manifest stage declaring both artifacts must include both at `awaiting_human` and `completed`; the proposed awaiting checkpoint has only `headshot_candidates` ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:59), [checkpoint.py](/Users/ben/Projects/OpenMontage/lib/checkpoint.py:344)).  
   Fix: Add status-specific manifest outputs, or use one discriminated headshot artifact whose pending and completed variants satisfy the same declared output.

3. **The existing gate cannot perform or securely bind candidate selection.** `gate_approve.py` supports only approve/decline, displays an agent-authored summary and paths, and signs an opaque agent-authored record; it neither accepts a candidate choice nor verifies that the chosen asset belongs to the reviewed candidate artifact ([gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:112), [gate_approve.py](/Users/ben/Projects/OpenMontage/scripts/gate_approve.py:179)).  
   Fix: Make the trusted gate handler enumerate candidates, accept the selection/reject-all decision, verify preview bytes against asset hashes and candidate membership, and construct the signed record itself with the candidates-checkpoint digest.

4. **“Permanent taint” has no authoritative persistence contract.** The plan says the object carries taint but does not require the `reference_import` approval record to bind origin and normalized hash or require enforcement to derive taint from the signed ledger; deleting local metadata could therefore enable synthetic re-import ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:57)).  
   Fix: Bind origin, normalized hash, and attestation in the signed import receipt and derive the project-wide taint set from the approval ledger on every validation, with any cache treated as fail-closed and rebuildable.

5. **Headshot canon has no unique active receipt.** `activate|retire` defines invalidation but not supersession or unique-tip resolution, so two approved headshots for one character can remain active and different consumers can select different “current” packets ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:61)).  
   Fix: Give headshot receipts a per-entity monotonic supersession chain and accept only its unique ledger tip; replacement must retire/supersede the previous receipt atomically.

6. **Existing-project migration bypasses the new mandatory stage.** Slice C migrates directly from `look_lock` to regenerated or reapproved sheet revisions without creating a `headshots` checkpoint or obtaining headshot approvals, despite manifest 1.2 requiring that stage ([plan](/Users/ben/Projects/OpenMontage/docs/plans/2026-08-26-look-lock-D10.md:75)).  
   Fix: Migration must create and approve one current headshot per character before any migrated or regenerated visual-bible sheet can be accepted.

VERDICT: REVISE
### Claude's response
**Accepted (6/6):** #1 reference question asked while drafting the wayfinder ticket; writer resolves the complete block; OpenMontage never patches at approval; #2 single discriminated `headshot_packet` (pending|approved); #3 selection gate — the handler enumerates candidates, verifies preview bytes, accepts a choice or reject-all, and constructs the signed record itself; #4 import receipt binds origin + normalized hash + attestation; taint set derived from the ledger, cache fail-closed; #5 per-entity monotonic headshot supersession chain, unique tip; #6 migration creates and approves headshots before any sheet.

### Resolution: 7 rounds (5 + 2 extensions). Design stable since round 2; Slice A′ reviewed twice; six fixes applied unreviewed. Handed to Ben.

### Ben signed off 2026-08-26 after round 7 (design consensus since round 2; last six fixes applied unreviewed). Build order: Slice A + A′ first.
