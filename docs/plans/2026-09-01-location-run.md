# Plan: location_run — the location imagery command (D21)
_Revision 6 — final. 5 capped rounds + 1 confirmation round authorized by Ben (R1: 20, R2: 13, R3: 8, R4: 9, R5: 9, C: 3 — 62 findings, all accepted; R1#20 partial with logged rationale). Confirmation round verified the R5 fixes: 6 confirmed, 3 gaps (C1–C3) applied here._

## Goal

Give locations the same command-driven, receipt-sealed imagery flow characters have. A new
`scripts/location_run.py` takes a ratified location look (a signed `look_lock` receipt) through
EITHER import of writer-supplied images OR paid generation, judges every image against a signed
location QC policy, presents the result at a human-signed gate, and — on `--finish` — seals the
location entry (establishing view + optional angles, with sealed QC and override citations) into
the visual bible. Project-agnostic workflow first; Bloodless's four locations (starting with
`sebastians-home`, whose two Midjourney images are the first real input) are the pilot, not the
deliverable.

## Current state (verified by recon, file:line)

Already in place, reuse as-is:
- `lib/look_ingest.py:502 active_look_for(root, "location", id)` works today; `sebastians-home`
  is ratified (receipt `ca5aca79`, look_hash `b5ff5764c968…`).
- `"location"` is an `APPROVAL_KIND` (`lib/receipts.py:54`) with a gate constructor
  (`scripts/gate_approve.py:930 _construct_location`) and a board renderer
  (`backlot/state.py:1323 _render_location`) — the Front Lot board shows location images with
  zero new UI code (`tests/backlot/test_gates_state.py:1091`).
- `tools/prompt_builder.py` has `LOCATION_ROLES = ("establishing","detail","time_variant")`,
  `_location_sections`, and `builder_policy_sha256()` already hashes the location role templates.
- `lib/canon_enforcement.py` iterates location refs (`:1036`), enforces `cast_cap.locations`
  (`:1158`), requires a `("location",)` receipt per approved entry (`:1201`).
- `lib/canon_view.py:70` projects approved locations to `by-entity/<loc>/establishing.png`, `angle_*.png`.

Known gaps this plan closes (Codex round 1 confirmed and extended the recon's three blockers to
a full list — see the REVIEW-LOG for the findings referenced as R1#n below):
- QC series verifier rejects location series (no `headshot_ref`) — R1#6 additionally showed the
  series schema itself cannot carry a location key today.
- No location judge checklist, local size rules, or verdict-schema roles (R1#7).
- Bible schema forbids sealing QC on location entries; `location_approval_record` seals no QC,
  no override citations, no per-slot recipes (R1#10, R1#12).
- The existing `_construct_location` is NOT trust-equivalent to the sheet constructor (R1#1).
- Import plumbing is not kind-safe: records bind `entity_id` only, requests are hard-coded to
  `look_lock` stage with character wording (R1#9).
- `require_hero_qc()` accepts exactly config 1.2, so a 1.3 config would break the character
  flows (R1#3). `_is_hero_batch_manifest` is keyed to the single tuple 1.5, so a 1.6 pin would
  silently lose batch behavior (R1#5).
- Egress consent has no class covering location images sent to the judge (R1#4).
- `skills/pipelines/authored-film/visual-bible-director.md:265` still instructs agents to make
  four establishing candidates with a separate approval — contradicts this command (R1#18).

## Approach

Numbered steps are the build order. Every behavior change that alters what a signature means gets
its own version bump and its own signed gate.

### Step 0 — Shared visual-entry transaction layer (R1#20, partial)
Extract from `sheet_run.py` into `lib/visual_entry.py` the transaction discipline both runners
must share on `checkpoint_visual_bible.json`: single checkpoint writer (write → digest → request,
never reversed), request publication bound to the digest, the `--finish` verification sequence
(bound digest current → `exact_approval` → verify → flip → single write), `--abandon`, decline
handling, rejected-history persistence, and mutual single-flight across BOTH run-state keys
(`run_state` for sheets, `location_run_state` for locations). Blocking is by **owner identity**
(R2#8): every transaction records `(runner_kind, entity_id, run_id, request_id)`; a runner may
resume exactly its own matching transaction, and any FOREIGN owner — the other runner, another
entity, or a pending sheet/location/import request it does not own — blocks. A blanket
"any non-empty state blocks" rule would deadlock legitimate resumes. `sheet_run.py` becomes a
thin caller; behavior-identical, proven by the existing sheet suite passing unchanged.
Scope limit (the "partial"): `headshot_run.py` is NOT refactored — different stage, different
checkpoint, working and heavily tested; folding it in is gratuitous risk. Logged as future work.

### Step 1 — Location QC policy bundle (`lib/sheet_qc/policy.py`)
- **`LOCATION_SYSTEM_PROMPT`** (R5#6): the judge's system prompt is currently hard-coded to
  character-sheet language; add a location system prompt, route via a new
  `system_prompt_for(role)`, include it in the location bundle hash, and test the prompt
  digest for both location roles.
- New `LOCATION_CHECKLIST` for two judge roles: `establishing` and `angle` (~8–10 items):
  matches `architecture_or_terrain`; matches `establishing_view` (establishing role only);
  time of day matches `time_of_day_default` unless the recipe says otherwise; palette anchors
  present; no people, figures, or legible text; no watermarks/borders; single coherent place
  (no collage); render quality. The `angle` role additionally carries a **place-identity item**:
  the angle is judged WITH the approved establishing plate attached as evidence, and must depict
  the same place (R1#8).
- `LOCATION_LOCAL_RULES`: `size.establishing = size.angle = {orientation: landscape_or_square,
  min_long_edge: 1024}`. `lib/sheet_qc/local_checks.py` (R4#2): implement
  `landscape_or_square` AND make `check_image` REJECT every unknown orientation token — today
  it silently ignores them (fail-open). Test: a portrait location image fails locally.
- `location_bundle()` / `location_bundle_sha256()`; third branch in `bundle_sha256_for_role`,
  `local_rules_for`, `checklist`, `judge_prompt`, `response_schema`.
- Evidence kinds from the signed location look payload only: `look_architecture`,
  `look_time_of_day`, `look_palette`, `look_establishing`; plus `establishing_image` (the
  approved establishing plate, for `angle` place-identity). Long free-text fields
  (`architecture_or_terrain`, `weather_or_light_rules`) get the 600-char cap treatment at both
  ends (look_spec validation + builder/evidence), the desc-cap precedent.
- `schemas/artifacts/qc_verdict.schema.json`: extend the role enum with `establishing`, `angle`
  (R1#7), with round-trip tests for local fail / remote pass / remote fail / reused verdict.
- Golden hash test in `tests/lib/test_d20_versions.py` (`LOCATION_BUNDLE_SHA256`).

### Step 2 — QC series + budget support for locations
- `lib/qc_receipts.py`: versioned, entity-kind-aware series schema (R1#6). Location series:
  `{entity_kind: "location", entity_id, role ∈ {establishing, angle}, look_hash,
  headshot_receipt_id: null (explicit), look_receipt_id (signed, in the hash subset),
  establishing_asset_id (angle series only — binds the anchor, R1#8),
  policy_bundle_sha256: qc.location_policy_sha256, builder_policy_sha256,
  generation_endpoint/model or IMPORTED sentinel, judge_provider, judge_model}`.
  Strict allowed-role matrix per entity_kind; character series unchanged byte-for-byte
  (golden test).
- **Verdict reuse tuple versioned** (R2#4): the QC verdict/tuple contract (`TUPLE_FIELDS`) gains
  a version; for location `angle` verdicts the tuple REQUIRES and hashes
  `establishing_asset_id` — an angle judged beside plate A can never be reused beside plate B.
  Character tuples unchanged byte-for-byte (golden test).
- `location_budget_key(project_id, entity_id, look_hash)` + `location_attempts_started`;
  `start_attempt` accepts budget keys on location series (today forbidden for non-hero) and
  hard-validates the key shape, mirroring the hero validation.
- `lib/sheet_qc/verify.py`: `_verify_location_series` — look-receipt-bound, no headshot_ref;
  for `angle`, verify `establishing_asset_id` matches the entry's establishing slot AND
  (generated angles, R2#5) that the verified generation receipt's `references_applied` contains
  exactly the expected establishing asset for this entity and role — anchoring is proven from
  the signed receipt, not asserted from the series.
- `tools/qa/sheet_judge.py`: location branch — location bundle, location evidence, attach the
  establishing plate for `angle` judgments.
- `tools/prompt_builder.py` (R3#1): a governed **`angle` role** added to `LOCATION_ROLES` with
  its own framing line ("a different view of the same place, no people" + reference-anchored
  wording) — `build_prompt` rejects unknown roles, so mapping is explicit, never implied.
  **Builder dispatch by role family** (R5#4 — a global version bump would NOT keep 1.5
  character behavior frozen, because the policy hash covers the whole role map and the
  paid-call boundary accepts only the current global hash): the builder splits into a
  byte-frozen 1.5 CHARACTER builder (existing hash preserved; character pass-reuse and
  receipts untouched) and a new 1.6 LOCATION builder with its own
  `location_builder_policy_sha256`; the paid-call boundary and QC series select the builder —
  and validate the hash — by role family. Golden tests freeze both hashes independently.
- **Generation tool contract** (R4#3, evidence hardened by R5#9): `SeedreamImage.asset_role`
  enum gains `establishing` / `angle`; the tool boundary ENFORCES `asset_role == "angle" ⇒
  operation == "edit"` with exactly one reference image. Because caller-authored
  `reference_manifest` fields prove only file identity, the runner passes a
  **`location_anchor_ref`** binding `{asset_id, entity_id, run/checkpoint digest, passing
  qc_receipt_id}` of the establishing slot; the boundary verifies it against current run state
  BEFORE upload or cost reservation, so a mis-anchored call can never spend. The receipt-side
  `references_applied` check (R2#5) remains the post-hoc proof.

### Step 3 — Project config 1.3
- `schemas/project_config.schema.json` `v1_3` = v1_2 + `qc.location_policy_sha256` (64-hex) +
  `qc.max_location_attempts` (int ≥ 1) + a new egress content class **`location_images`**
  covering generated AND imported location plates sent to the judge (R1#4).
- `tools/video/_shared.py` judge boundary: location judgments require the `location_images`
  class for the judge provider; character judgments keep requiring `generated_sheet_images`.
- `lib/project_config.py`: `SUPPORTED_VERSIONS += ("1.3",)`; **replace exact-version checks with
  capability predicates** (R1#3): `require_hero_qc()` accepts any version ≥ 1.2 that carries the
  hero fields; `require_location_qc()` requires ≥ 1.3 with the location fields. Regression
  tests: headshot_run and sheet_run full flows under a 1.3 config.
- **Staged config signing** (R4#1, hardened by R5#2): the config gate signs a
  **content-addressed** staged file bound into the request (its sha256 in the request body),
  rehashed under the approval lock at pre-commit; an idempotent WAL/marker covers the
  receipt-commit → exact-byte promotion window so a crash between the two recovers to a
  consistent state before the request is marked done. Never a live-file edit — that would
  invalidate the digest an active run needs even to resume or abort (deadlock). The previously
  signed config stays usable for transaction cleanup throughout.
- Adoption is a signed `config` gate. Bloodless: staged 1.3 with `max_location_attempts: 12`
  and the `location_images` egress line under openai — Ben signs, with the egress change called
  out explicitly in the gate summary.

### Step 4 — Manifest 1.6 + migration
- `pipeline_defs/authored-film@1.6.yaml`: 1.5 + the `visual_bible` stage promise extended
  (every location plate carries a signed location verdict or a sealed, cited override; location
  entries seal QC + override citations in the approval record; angles are anchored to the
  establishing plate). Fix the 1.5 `required_skills` inconsistency.
- **Director update** (R1#18): new `visual-bible-director-1.6.md` — locations section rewritten
  to mandate `location_run` and its exact gate semantics (no four-candidate flow, no separate
  establishing approval); 1.6 stage `skill:` pointer updated; 1.5 director byte-frozen by golden
  test like the headshots directors.
- `lib/canon_enforcement.py`: add 1.6 to `LOOK_LOCK_MANIFESTS` / `QC_MANIFESTS` /
  `HERO_QC_MANIFESTS`; **`HERO_BATCH_MANIFESTS = {1.5, 1.6}`** replacing the single-tuple
  predicate, with the full 1.5 batch/waiver/record-1.2 test matrix re-run under a 1.6 pin
  (R1#5); `LOCATION_QC_MANIFESTS = {("authored-film","1.6")}` + `_is_location_qc_manifest`.
- `lib/pipeline_pin.py require_migration_coverage` 1.6 branch (R1#13): refuse migration while
  ANY governed request is pending/unresolved (sheet, location, import, override, headshot),
  any `run_state`/`location_run_state` is non-empty, or any prior-pin checkpoint sits at
  `awaiting_human` — a migration must never strand a signed-against digest. Same rule added to
  config re-signing (gate constructor refuses while governed requests are in flight).
- `tests/lib/test_pipeline_pin.py:33` updated. Adoption: `pipeline-migration-1-6`, Ben signs.

### Step 5 — Visual bible schema v1_2 + sealed location record
- `schemas/artifacts/visual_bible.schema.json` `$defs.v1_2`: characters unchanged. Location
  entries become a **canonical slot map** (R1#12): `slots: {establishing: SlotRef,
  angle_0..angle_2?: SlotRef}`. `SlotRef` is **composition, not extension** (R2#6 — `image_ref`
  is closed with `additionalProperties: false`): `SlotRef = {image: ImageRef, prompt_recipe?,
  qc_receipt_id, override?: {override_receipt_id, override_record_sha256, accepted_item_ids}}`
  (R1#10), itself closed — no palette field anywhere in a slot (R4#7/R5#7). **No duplicated provenance authority** (R3#2): role and
  import receipt id live ONLY inside `ImageRef`/its provenance — the slot never re-states them;
  validation requires `slots.establishing.image.role == "establishing"` and every
  `angle_N.image.role == "angle"` directly against the single source. `prompt_recipe` on a
  generated slot must equal the signed generation receipt's recipe by canonical comparison, and
  its rendered hash must match the approval record (R3#3) — a generated slot without that
  binding is invalid. **Override citation binding** (R4#6): the slot's `accepted_item_ids` must
  EQUAL the override receipt's `record.item_ids` exactly, and the receipt must match the slot's
  qc_receipt_id, asset, role, and entity_kind; verification then proves every failing
  overridable item — and no other item — is covered. **One palette authority** (R4#7): SlotRef
  carries NO palette; the canonical source is the entry-level `palette_override` (else the
  bible palette), and verification rebuilds each generated prompt from that single value —
  `location_approval_record` seals it once. Validation adjacent to the schema (R2#12): asset
  ids are distinct across ALL slots of an entry — one plate and one verdict can never fill two
  slots. `angles` requirement drops to 0–3 (establishing-only is legal).
- **Version negotiation** (R1#11): bible 1.2 is seeded/required ONLY under a signed 1.6 pin;
  under older pins, 1.1 remains required (no early upgrade, no downgrade). Under a 1.6 pin:
  a 1.1 bible is accepted only while its `locations` is empty (grandfathered characters); the
  first location write upgrades to 1.2 (refused while any sheet/location request is in flight);
  any location entry in a 1.1 bible under 1.6 is a violation.
- `lib/canon_enforcement.py location_approval_record` v1_2: an explicit, strictly validated
  **`record_version` field** (R2#11) — gate construction AND enforcement dispatch solely from
  it (the 1.4→1.5 era lesson); seals per-slot `{asset_id, role, origin,
  generation_or_import_receipt_id, qc_receipt_id, override citation, prompt_recipe_sha}` plus
  ONE entry-level resolved palette (R4#7/R5#7 — the single `palette_override`-or-bible value;
  CLI `--palette`, prompt reconstruction, gate display, and approval hashing all use exactly
  it) — cited authority, never ledger-scan authority (R1#10). Override verification goes
  through `exact_approval` per citation.
- **Location envelope** (R1#2, corrected by R2#1, narrowed by R4#9): the same boolean
  required/optional field map shape headshots use — `action` restricted to **`activate` only**
  in 1.6 (retire has no CLI, record, finish, or chain semantics yet; authorizing an
  unimplemented signed action is a hole — deferred with the rest of retirement), plus
  `entity_kind`, `look_hash`, and OPTIONAL `supersedes_receipt_id`. Replacement is
  `action: "activate"` with `supersedes_receipt_id` citing the current tip (the headshot
  supersession pattern, no new verb).
- **Active-location chain resolver** (R2#2): new `active_location_for(root, entity_id)` with
  root-to-tip completeness, fork, and cycle validation over the location receipt chain (the
  `pipeline_pin._chain_tip` discipline, generalized). Pre-sign vs post-sign split (R3#4):
  BEFORE signing (gate construction, pre-commit) the check is that the CURRENT approved entry
  (if any) equals the old tip and the proposal's `supersedes_receipt_id` cites exactly that
  tip — the new receipt cannot be required to exist yet. AFTER signing (`--finish`, canon
  enforcement) the entry's `approval_receipt_id` must equal the NEW unique tip. A merely-exact
  historical receipt is never sufficient; replay of a superseded location approval is
  structurally impossible.
- **`exact_approval` envelope verification** (R2#3): the API gains an `expected_envelope`
  argument compared by canonical equality (or the callers compare the returned signed envelope
  explicitly) BEFORE any checkpoint mutation — today it checks only id/kind/entity/digests.
  The old `canon_ruling visual:<id>` route stays only as the read-side compatibility check for
  characters.
- `lib/sheet_verify.py verify_location_plates(root, entry, look, qc, pin)`: per-slot content
  hash, receipt resolution by cited id, look binding, QC verdict passing or sealed override
  citation (verified via `exact_approval`), angle→establishing anchor match, policy-era rules
  keyed off the RECORD version (the 1.4→1.5 migration lesson). Wired into
  `_check_visual_bible_v12`'s location branch and the partial-bible backstop.
- **Slot-map consumer inventory** (R2#7): every reader of the old
  `location.establishing`/`location.angles` shape is found and updated in the same step —
  `lib/canon_view.py:70 plan_view` (else `build_view` silently succeeds with no location links
  and the `projection_pending` marker clears wrongly), `canon_enforcement._iter_image_refs`,
  `approved_image_owners`, the partial-bible backstops, `gate_approve._construct_location`,
  `backlot/state.py _render_location` — each with a projection/renderer contract test asserting
  location links actually appear.

### Step 6 — Import plumbing made kind-safe (R1#9)
- `lib/reference_import.py`: import record version bump binding `(entity_kind, entity_id)`;
  request stage/summary parameterized (location imports say so, stage `visual_bible`);
  the judge/finalize path requires the generation receipt's attestation id to equal that exact
  location import receipt. Character import records keep their current version; verification is
  era-keyed by record version (no retro-invalidation of Ace's/Ivy's imports).
- Request ids (R1#14): `import-<E>-<slot>-<n>` with a durable monotonic per-entity sequence
  (the `override_request_seq` pattern); uniqueness enforced across pending/done/declined/
  abandoned/**withdrawn** (R3#6) — every request-path resolver, collision scan, recovery rule,
  terminal-state validator, and board counter includes the new state.

### Step 7 — `scripts/location_run.py` (the command)

```
location_run.py --project P --entity E                      # generate establishing + angles
                [--angles N]            # 0–3, default 2; the requested-slot set is persisted
                                        # immutably in run state at start (R3#8) — later
                                        # invocations cannot silently change completion criteria
                [--import FILE --slot establishing|angle_0..2 --origin-tool NAME]
                [--palette ...]         # sets the ONE entry-level resolved palette
                [--max-attempts N]      # may only LOWER the signed cap; persisted at run
                                        # creation as effective_max_attempts (R5#8) — later
                                        # invocations may not change or exceed it
                [--resume] [--finish] [--abandon] [--abort] [--replace] [--open]
```

- **Preflight** (R2#10 — no read-then-lease TOCTOU; R3#5): a new **config-independent lease
  acquisition** (today's `hold_lease` needs a loaded config, a circularity — the lease helper
  learns to read only `wall_time_minutes`-safe defaults or acquire-then-tighten), then every
  authoritative read inside the lease — config (`require_location_qc()`, ≥ 1.3), pin
  (`_is_location_qc_manifest`, 1.6), `qc.location_policy_sha256 ==
  policy.location_bundle_sha256()`, `resume_check`, `active_look_for(root, "location", E)`,
  checkpoint state, mutual single-flight via Step 0's owner identity. A migration or config
  gate committing while the runner waited can never leave it holding stale objects.
  **`sheet_run` gets the same lease-first ordering** (R3#5) — it currently reads config and
  pipeline state before acquiring its lease; Step 0's shared layer fixes both runners.
- **Generation order**: establishing first, always. Generated angles are produced by
  **image-edit/reference calls anchored on the approved establishing plate** (the sheet-from-hero
  pattern), never independent text-to-image (R1#8); their series bind `establishing_asset_id`.
  Imported angles are judged with the establishing plate attached for place identity.
- **Import mode** (per image): stage → kind-safe normalize (Step 6) → checkpoint write →
  import request (board-visible) → after signing, `--resume` finalizes, judges under the
  location bundle (burning an attempt), fills the slot. A failing image raises a
  **kind-bound** single-verdict `qc_override` (`override-<E>-<slot>-<n>`, same durable
  sequence): the override record is versioned to bind `entity_kind` (R4#5), with **explicit
  three-way dispatch** (R5#3) — record_version absent → legacy single-verdict validator;
  `1.1` → batch validator; the NEW version (`1.2`, exact strict schema) → kind-bound
  single-verdict validator. (Today any record carrying `record_version` routes to the batch
  validator, which rejects everything but 1.1 — the naive version bump would break.) The 1.2
  record REQUIRES `request_id`, and post-commit crash recovery (which today recognizes only
  1.1) recovers a 1.2 override only via the unique verified receipt matching request id,
  source digest, scope, AND record digest (C1) — no stranded bound requests, no duplicate
  unbound receipts. The gate constructor requires the request scope to equal the verdict's
  `(entity_kind, entity_id)`, and read-side lookup requires the expected kind — a same-slug
  location request can never solicit authority over a character verdict. A signed override is sealed as a citation in the
  slot; decline discards the image into rejected history.
- **Generate mode**: `build_prompt(look.payload, role, palette)`; Seedream v5 Pro ($0.07/image),
  landscape 1536×1024; reservation → `start_attempt` pre-submit hook → judge → retry within the
  cap. **Budget** (C3): the operative cap EVERYWHERE is the persisted `effective_max_attempts`
  — every attempt allocation, remaining-attempt calculation, cost preflight, and exhaustion
  check uses it; the signed `max_location_attempts` is only its upper bound. One cap per entity
  per look shared across all slots and both modes; pre-flight cost check is
  `remaining_effective_attempts × (price + reserve)` — global, NOT multiplied by slot count
  (R1#17); judge-only attempts (imports) price at the judge call.
- **Rejected history** (R1#15): rejected/declined asset ids AND verdict ids persist per
  entity+look in checkpoint metadata; excluded from pass-reuse, import re-offer, override
  scope, and slot filling — the sheet_run `rejected_sheets` discipline.
- **Escape hatches** (R1#16, semantics fixed by R2#9): `--abort` moves the runner's own open
  requests to a NEW distinct terminal state **`withdrawn`** — never `declined` (that fabricates
  a human decision) and never `abandoned` (that means a consumed/stale signed request). The
  transition is linearized under the approval lock; the refusal rule is scoped to the RACE
  (R3#7): abort is refused only when the CURRENTLY PENDING request turns out `done` (a signed
  decision wins) — earlier consumed receipts from completed import gates are preserved history,
  never an abort blocker, or abort would become unusable after the first approved import.
  Abort clears the draft and run state, keeps burned attempts + rejected history. `--angles` may be re-scoped downward on a fresh run after abort.
  `--abandon` keeps its narrow sheet_run meaning. Crash-safety: every state transition is one
  checkpoint write; `_recover_attempt_generation` pattern reused for reservation crashes.
  Request-state machinery (`request_state`, directories, board rendering) learns `withdrawn`.
- **Explicit phase table** (R4#8): run state carries a `phase` ∈ {`awaiting_slot_input`,
  `import_gate_pending`, `override_gate_pending`, `final_gate_pending`, `projection_pending`}.
  Each CLI flag is legal in an enumerated set of phases and REJECTED elsewhere (`--import`
  only in `awaiting_slot_input`; `--resume` only in the three gate/projection phases;
  `--finish` only in `final_gate_pending`/`projection_pending`; `--abort` in any phase but a
  finished final gate; contradictory combinations like `--abort --finish` are argparse-level
  errors). A direct sheet_run mirror would ignore new flags whenever run state exists — the
  multi-import adoption flow depends on this table.
- **Previous-entry restoration** (R4#4, bounded by R5#1): before a replacement draft overwrites
  the stable-ID entry, the exact previous approved entry AND its receipt tip are persisted in
  transaction state (`location_prev_entry`, the `sheet_prev_entry` contract). Gate construction
  and pre-commit compare against the PERSISTED previous entry/tip, not the (already
  overwritten) bible. Restoration applies ONLY pre-signature: decline and `--abort` (both
  strictly before a signed receipt exists) restore atomically. **Once a replacement is SIGNED,
  restoration is forbidden** — the new receipt is the unique chain tip and unsigned state can
  never revoke signed authority; from that point **immutable-snapshot projection is the SOLE
  recovery path** (C2): `--finish`/`--resume` retry projection from the request's immutable
  snapshot until the entry is sealed — there is no alternative "further signed receipt" route,
  because the pre-sign rule (current entry = old tip) cannot be satisfied while a signed tip
  sits unprojected. `--abandon` on a signed-but-stale location request therefore does NOT
  restore the old entry and does NOT park indefinitely; it re-arms the same projection retry.
  Only after the signed tip is projected can a new `--replace` cycle begin.
- **Present**: when every requested slot holds a passing (or override-cited) image, write the
  draft entry (`status: "draft"`, slot map sealed) at `awaiting_human`, publish
  `location-<E>-<rev>` with `approval_record: location_approval_record(entry, palette)` +
  envelope, bound to the checkpoint digest, preview paths for every slot. **`rev` has a durable
  allocator** (R5#5): a monotonic `location_revisions[entity_id]` in checkpoint metadata (the
  sheet pattern), incremented across EVERY terminal outcome — done, declined, withdrawn,
  abandoned — and bound into run state, the entry, the approval record, and the request id, so
  a terminal request id is never reused.
- **`--finish`** (via Step 0's shared transaction): bound digest current → `exact_approval`
  (kind `location`, record digest, source digest, **`expected_envelope` compared canonically**
  per R2#3) → chain-tip equality via `active_location_for` (R2#2) → `verify_location_plates` →
  flip → single write. **Canon projection** (R1#19): `canon_view.build_view` failure is not swallowed —
  the approval commits, but run state stays held by a durable `projection_pending` marker that
  `--resume` retries and the command surfaces loudly; state clears only on projection success.
- **`--replace`** (semantics pinned by R2#13): raises a superseding location gate whose
  envelope cites the current receipt tip (R1#2). On finish the single stable-ID entry is
  replaced **in place** — the bible never holds two entries for one canon location id; history
  lives exclusively in the signed receipt chain (tip walk) and rejected-history metadata.

### Step 8 — Gate + board deltas
- `scripts/gate_approve.py _construct_location` rebuilt to sheet-constructor parity (R1#1):
  checkpoint-digest binding, `verify_location_plates` at display, at decision reconstruction,
  and pre-commit under the approval lock; envelope construction; per-slot origin
  (imported/generated) + override citations shown in the evidence summary.
- `backlot/state.py _render_location`: slot map rendering + origin labels (renderer contract
  test updated).

### Step 9 — Tests, then adoption
- New/updated suites per the negative-path list Codex demanded: stale/mutated checkpoints; gate
  pre-commit changes; old-receipt replay; same-slug character/location cross-kind imports;
  bible 1.1-downgrade-under-1.6; config-1.3 hero/sheet compatibility; 1.6 batch preservation
  (full 1.5 matrix under a 1.6 pin); migration refused with pending gates; duplicate import
  slots/request ids; declined-pass reuse; crash at every checkpoint/request/receipt transition;
  angle-anchor mismatch (series, verdict tuple, AND `references_applied`); envelope
  supersession replay; chain fork/cycle/hole rejection; one-asset-two-slots rejection;
  `withdrawn`-vs-signed race; resume-under-own-owner vs foreign-owner block; lease-first
  preflight revalidation; `plan_view` location links present after finish.
- Bloodless adoption order (each its own signed gate, one at a time): config 1.3 (egress line
  called out) → `pipeline-migration-1-6` → `location_run --entity sebastians-home --angles 1
  --import sebastian_estate_exterior.png --slot establishing --origin-tool Midjourney` (R3#8:
  the run is opened with exactly the slots it will fill — establishing + one angle) → import
  gate → resume/judge → import great hall → `angle_0` → import gate → resume/judge → location
  gate → finish. Then the three remaining locations by generation (establishing first, anchored
  angles), one at a time.

## Key decisions & tradeoffs

- **D1: Sheet model, not casting.** One approve/decline gate per location entry; no pick-1-of-N.
  Cheaper, matches sheets. A decline regenerates. (Unchanged from round 0; Codex did not
  contest.)
- **D2: One budget per location per look, shared across slots and modes.** One cap to sign, one
  ledger. Tradeoff: a bad establishing streak can starve the angles — mitigated by `--abort` +
  downward re-scope (R1#16).
- **D3: `angles` optional (0–3), establishing required.** Sebastian's home ships with one angle.
  The gate summary states the slot count so the signer sees what they're approving.
- **D4: Manifest 1.6 + config 1.3 + bible v1_2, all now.** Three bumps guard three different
  signatures; the record/schema change is free only while zero location entries exist.
- **D5: Angles are anchored, never independent** (reversed from round 0 by R1#8): generated
  angles are edit/reference calls from the approved establishing plate; all angles are judged
  with the establishing plate for place identity; angle series bind `establishing_asset_id`.
- **D6: No batch (field-bound) waiver for locations in v1.** Single-verdict `qc_override` with
  sealed citations (R1#10) is the relief valve. Revisit if a real run demands it.
- **D7: Locations get a signed envelope with supersession** (reversed from round 0 by R1#2).
- **D8: Step 0 extraction covers sheet+location only; headshot_run untouched** (R1#20 partial —
  rationale in the log).

## Risks / open questions

- **Shared checkpoint concurrency** remains the sharpest edge even with Step 0: both finish
  paths, both preflights, and the migration/config guards must all see both run-state keys and
  all pending request kinds. The test matrix covers every pairing.
- **In-place 1.1 → 1.2 bible upgrade** is refused while anything is in flight; the upgrade write
  itself must leave character bytes untouched (byte-diff test).
- **Import judge fairness**: imports face the same checklist as generated plates; the sealed
  single-override is the relief valve. Same policy, same receipts — provenance changes nothing.
- **Attempt accounting for imports**: an import's judge call burns an attempt from the shared
  cap. Defensible (judge spend is spend); flagged for Ben at config-signing time.
- **Edit-anchored angle generation** assumes the generation endpoint supports reference/edit
  calls for location plates as it does for sheets — verify the exact endpoint contract during
  build; if reference-to-image needs a different endpoint, it must be added to the signed
  config's egress/endpoint surface.
- **Evidence prompt budget**: capped free-text fields (600-char precedent) — enforced at
  signing, so a pre-cap ratified look (sebastians-home) must be checked against the caps before
  adoption; if it exceeds them, the look needs a supersede pass first.

## Out of scope

- Batch (field-bound) waivers for locations (D6).
- `time_variant` plates, posters, storyboards, any video.
- Refactoring `headshot_run.py` onto the Step 0 layer (D8; future work).
- Back-compat for existing location entries (none exist in any project).
- Relational `profiles_opposite` judge fix and other open character-side follow-ups.
- Bloodless asset production beyond the adoption sequence in Step 9 (one entity at a time,
  every image behind a signed gate — no batching ahead of approval).
