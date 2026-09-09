# Plan: Visual Bible stage for authored-film (character/location sheets, storyboards, poster, trailer format)
_Locked via claudex-loop — by Claude + Ben, 2026-08-25_

## Goal
Extend the `authored-film` pipeline so a written project (canon atoms, wayfinder locks, WriterOS documents) can be carried through to a 60–120s promo/trailer plus a poster, with visually consistent characters and locations. The mechanism is a new `visual_bible` stage that produces approved, hashed reference sheets stored as project canon; every downstream shot must be generated from those references and cite them. Trailer is a *format* honored by the script and compose stages, not a separate pipeline. Pilot project: Bloodless (canon at `~/Library/Mobile Documents/com~apple~CloudDocs/My Scripts/Bloodless/`, atoms + Canon Note rev 57 + wayfinder locks). Every gate is human-approved; no autopilot.

## Approach

### 0. Prerequisites (done before any enforcement code; R1 #19, #21, #22; R2 provider coverage)
- Run `make setup`; confirm `requests`/`PIL` import in the interpreter the pipeline uses.
- Verify the official FAL request contracts for **every** new endpoint: `fal-ai/kling-video/o3/pro/reference-to-video` (default video, D4 revised), `bytedance/seedance-2.5/reference-to-video` (entity-free scenes only), `bytedance/seedream/v5/pro/edit` (+ text-to-image), `fal-ai/flux-pro/kontext` (parameter names, reference caps, pricing, request-id field). Record each as a fixture in `tests/fixtures/providers/<endpoint>.json`; register a provider skill/runtime declaration for each in the same commit. One minimal paid smoke call per endpoint (~$5 total) proves the payloads. If Seedance cannot combine a start frame with references, storyboard frames are references only.
- Project config: new `projects/<slug>/project.yaml` (validated by `schemas/project_config.schema.json`) carrying `budget_usd_cap`, `wall_time_minutes`, `cast_cap {characters, locations}`, `provider_egress {provider: fal, content_classes: [prompts, reference_images]}`. The manifest's `budget_default_usd`/`max_wall_time_minutes` stay as defaults; project.yaml overrides. **The config is not self-attesting:** its sha256 is bound to a `decision_log` entry of category `approval_policy` written through the human-gate path; enforcement rejects any run whose current config digest has no matching approval, so raising the budget or authorizing egress requires a new human approval.
- Paid-call idempotency: before submission, CostTracker persists a reservation `{reservation_id, tool, endpoint, normalized_inputs_hash, reserved_usd}`; the reservation carries a client-generated idempotency key (sent as the FAL request's idempotency header where supported, otherwise embedded in a request-tagging field the fixture verifies); the reservation is persisted with state `submitting` **before** the network call; the provider request id is written when returned; reconciliation runs in `finally`. On restart, a reservation in `submitting` with no request id is `indeterminate`: the tool queries the provider by idempotency key if supported, else halts and asks the human to reconcile — it never resubmits automatically. A paid call whose reservation would exceed the cap fails preflight.
- Provider-egress ruling: `visual_bible` cannot start until `project.yaml.provider_egress` is present (Ben's explicit one-time approval that prompts and generated images leave the machine for FAL).

### 1. Stage placement (R1 #1 partial)
Two things were conflated: entity sheets (need only canon + treatment) and storyboards (need shots). Split them:
- `visual_bible` stage: `canon_ingest → proposal → visual_bible → script → scene_plan → assets → edit → compose`. Inputs `canon_packet`, `proposal_packet`. Produces `visual_bible` (characters, locations, palette, poster). Completion criterion: every entity in `proposal_packet.cast` (new field: the treatment names which characters/locations the trailer shows, bounded by `cast_cap`) has an approved sheet.
- Storyboards move into the `assets` stage as its first, gated sub-step (asset `type: storyboard_frame`, **one per `shot_id`**, approved as a batch before any video call). They are not canon; they are per-production assets that cite the canon sheets.
- `visual_bible` is added to `required_artifacts_in` of `script`, `scene_plan`, `assets`, `edit`, `compose` (finding #3), and the EP, script, scene, asset, edit, compose skills are updated to read it.

### 2. Entity identity and schema versioning (R1 #6; R2 migration)
- Versioning: every touched artifact schema (`canon_packet`, `proposal_packet`, `scene_plan`, `asset_manifest`) becomes a `oneOf` over `{v1.0 (unchanged), v1.1}` keyed on `version`; the loader dispatches on `version`. Legacy 1.0 checkpoints and the other pipelines keep validating unchanged; regression-test every existing pipeline fixture against the new `oneOf` schemas. `lib/migrations/artifacts.py` provides 1.0→1.1 upgraders for canon ids (deterministic: `<entity_type>-<source_index>-<sha256(name+source_text)[:8]>`; migration fails if a uniqueness check still finds a collision). **Scene refs and proposal cast are never synthesized:** a migrated 1.0 scene_plan/proposal is marked `migration_status: needs_review` and enforcement treats it as invalid for `assets` until a human-reviewed regeneration (scene director re-emits refs, proposal director emits cast) is approved with a receipt. Empty refs are only valid on scenes the director explicitly marks `entity_free: true`.
- `canon_packet` v1.1: characters and locations gain a required `id`.
- `scene_plan` v1.1: scenes gain `character_refs[]`, `location_ref`, and `shots[]` (`shot_id`, `description`); scenes gain `model_endpoint` and optional `model_override {endpoint, reason}`. **Model selection is per scene** (`scene.model_endpoint`, default from project config = Kling o3 pro reference-to-video; `scene.model_override {endpoint, reason}` optional — Seedance 2.5 only on `entity_free` scenes). Shots carry no model field; every shot and every selected take in a scene uses the scene's endpoint. Storyboard, generation, and take validation are shot-level; model policy is scene-level. This is the single invariant (D4).

### 3. Artifact schema `visual_bible` (new, `schemas/artifacts/visual_bible.schema.json`, registered)
```
visual_bible
  version, project_slug, palette {hues[3..4], notes}, generator_defaults {image_model, edit_model}
  characters[]: {id (= canon id), hero: ImageRef, sheet: {front, three_quarter, profile, full_body, expressions, wardrobe: ImageRef},
                 wardrobe_negative, approved_prompt_block (verbatim text copied into downstream prompts),
                 status: draft|approved|superseded, approval_receipt_id, superseded_by?}
  locations[]:  {id, establishing: ImageRef, angles[2..3]: ImageRef, palette_override?, status, approval_receipt_id}
  poster:       {key_art: ImageRef, title_card: ImageRef, poster_final: ImageRef, status, approval_receipt_id}
ImageRef = {asset_id (sha256), path (content-addressed, see §4), role, provenance: oneOf[
  {generator_kind: model, model_endpoint, prompt, seed?, generation_receipt_id},
  {generator_kind: local, tool, tool_version, parameters_hash, input_asset_ids[], generation_receipt_id}
]}
Local derivations (title card, poster composite) are wrapped so they emit receipts too; every canon asset has exactly one receipt of either kind.
```
Reference objects replace the `path+sha256:` string grammar (finding #7): `canon_packet.reference_assets` is left untouched (packet is immutable after ingest, finding #2); `asset_manifest` v1.1 keeps the existing `type` enum (`image|video|music|font|subtitle|…`) and adds `asset_class: storyboard_frame|shot_visual|non_shot` with conditional requirements: `storyboard_frame` (type image, `shot_id`, `continuity.references_applied` required), `shot_visual` (type image|video, `shot_id`, `take_id`, `usage_status`, `model_endpoint`, `continuity.references_applied: [{asset_id, path, role, visual_bible_entity_id}]`), `non_shot` (no shot/take/model fields). Only `shot_visual` and `storyboard_frame` are subject to the continuity and model rules.

### 4. Storage: content-addressed canon (finding #16)
- Every generated image is written once to `projects/<slug>/canon/visual/objects/<sha256>.png`. Human-readable names are a pointer file: `canon/visual/characters/<id>/sheet.json` mapping role → asset_id. Superseding writes a new object and updates the pointer; old objects are never deleted.
- Supersession requires a `decision_log` entry of category `canon_ruling`, `question_id: visual:<entity-id>`, plus a new approval receipt.

### 5. Approval receipts (R1 #5; R2 API fit + binding)
- New operation `record_human_approval(project_id, stage, scope, approval_record, gate_token)` in `lib/checkpoint.py`, separate from `write_checkpoint`: `gate_token` is a one-use capability minted by the human-gate CLI/Backlot handler at the moment the user answers, bound to `{project, stage, scope, record_sha256, expires}`. The token is an opaque random value returned only to the gate handler; the verifier holds an HMAC of it in orchestrator-owned state under `~/.openmontage/gates/` (outside any project directory an agent writes to) and consumes it atomically (rename-on-consume). Nothing in the project tree can mint or replay one. No free-form `approver` argument exists, so director skills and buggy callers cannot self-approve. The token's user response is recorded in the receipt. it appends to `projects/<slug>/approvals.jsonl` and does **not** change checkpoint status, so sub-gates never prematurely complete the stage. Stage completion still goes through `write_checkpoint(human_approved=True)` and additionally requires the receipts below.
- A receipt binds the whole approved state, not just asset ids: `{receipt_id, kind: hero|sheet|location|poster|storyboard_batch|config|artifact_review, entity_id (or for artifact_review: artifact_type, artifact_version, artifact_digest, migration_status), record_sha256, approved_by, approved_at, source_checkpoint_digest}` where `record_sha256` hashes the RFC 8785 (JCS) canonical JSON of the approved record (NFC-normalized strings, JCS number serialization, sorted keys — tested for Unicode, numeric, and key-order equivalence) (asset ids, roles, `approved_prompt_block`, wardrobe negative, palette, pointer mapping). Any later change to those fields invalidates the receipt.
- Each receipt is HMAC-signed with the orchestrator-held key (same key store as gate tokens, `~/.openmontage/gates/`), and the consumed-token ledger there records `{token_hmac, receipt_id, record_sha256}`. Enforcement recomputes the record hash from the current artifact, verifies the receipt signature, and cross-checks the ledger; a receipt in project-writable `approvals.jsonl` that matches the hash but lacks a valid signature or ledger entry is rejected (tested).
- Enforcement requires an exact receipt match. Status flips, prompt edits, or pointer changes without a fresh receipt are rejected.

### 6. Generation receipts (finding #12)
- `BaseTool.execute` wrapper emits `projects/<slug>/generation-receipts.jsonl`: `{receipt_id, execution_id, tool, model_endpoint, provider_request_id?, normalized_inputs_hash, output_sha256, cost_usd, started_at, finished_at}`. `ImageRef.generation_receipt_id` must resolve to a receipt whose `output_sha256` equals `asset_id`. That is the synthetic-only proof: no receipt, not canon. Receipt writing is fatal on failure (unlike current events).

### 7. Enforcement (`lib/canon_enforcement.py`, `authored-canon` profile)
- `visual_bible` completion: cast coverage, file existence + hash match, receipts (approval + generation) for every approved ImageRef, palette present, poster approved.
- Path safety (R1 #13; R2 output rule): inputs resolved with `strict=True`, must be inside the active project root, symlinks rejected, re-hashed immediately before any upload. Outputs: validate the parent directory (must exist, inside project root, not a symlink), write to a unique staging file under `projects/<slug>/.staging/`, decode with PIL and deterministically re-encode as PNG (fixed encoder parameters, metadata stripped), hash the re-encoded bytes, then atomically move to `canon/visual/objects/<sha256>.png`. Video outputs keep a verified MIME-derived extension after ffprobe. Content-addressed destinations are therefore never known before generation finishes, by design.
- Continuity rule narrowed (finding #8): an asset must cite approved sheets only when its shot's `character_refs`/`location_ref` are non-empty; then every referenced entity must appear in `references_applied`. Title cards, transitions, abstract inserts are exempt by having no entity refs.
- Single-strategy rule (R1 #9; R2 scope): enforced per shot across *selected* takes. `asset_manifest` assets gain `shot_id`, `take_id`, `usage_status: candidate|selected|rejected`, `model_endpoint`. Reject if any selected take in a scene uses an endpoint other than the scene's `model_endpoint`, if a scene's endpoint differs from the project default without `model_override.reason`, or if the storyboard frame for a `shot_id` is missing/unapproved.
- Reference packing (finding #11): deterministic policy per shot — hero + wardrobe for each character in `character_refs`, establishing for `location_ref`, storyboard frame last; preflight fails if the count exceeds the verified endpoint cap instead of silently truncating.

### 8. Skill `skills/pipelines/authored-film/visual-bible-director.md` (+ INDEX, required_skills)
As before: hero portrait (4 candidates → pick) → derived sheet (one unit) per character; locations; palette; then poster. Continuity risks become negatives, wardrobe has its own reference and negative line, palette hues in every prompt. `approved_prompt_block` is the "asset passport" copied verbatim by `shot_prompt_builder.py`. Never batch ahead of an unapproved hero portrait. Single-threaded: one entity at a time. **Run lease (R2):** every pipeline run takes an exclusive per-project lease file (`projects/<slug>/.run-lease`: pid, process start time, hostname, heartbeat timestamp refreshed every 30s; O_EXCL create). A second session fails fast. A lease is reclaimed automatically only when the owner is demonstrably dead (pid gone, or pid reused with a different start time); a live owner with a stale heartbeat blocks and reports, never gets overridden. Checkpoint, decision-log, approvals, receipts, and cost state are written via unique temp file + fsync + atomic rename (replaces the fixed `.json.tmp` path). No parallel-worker locking beyond this.

### 9. Tools
- `tools/graphics/seedream_image.py` (new): FAL Seedream 5 Pro edit + text-to-image; fallback Flux Kontext via `flux_image.py` (add mode if absent).
- `tools/video/kling_reference_video.py` (new, **default video tool**): `fal-ai/kling-video/o3/pro/reference-to-video` per its fixture — `@ImageN` references (plus an `elements` form per entity), storyboard frame packed last, cap 4 with a video reference / conservative 9 without (doc unconfirmed), $0.112/$0.14 per second; same governed path as Seedance 2.5 via the shared helpers (`_shared.storyboard_preflight`, `_shared.bind_reference_manifest`, queue/download/WAL).
- `tools/video/seedance_video.py`: add `model_version` (`2.0`|`2.5`, default `2.5`) and the verified 2.5 payload from §0; keep 2.0 intact. Seedance 2.5 is reachable in authored-film only through a scene `model_override` on an `entity_free` scene. Hardening while in the file (finding #14): total deadline + cancellation, allowlist for status/result hosts (`fal.run`, `queue.fal.run`, `fal.media`), streamed download with size/MIME limits, unique temp file + atomic rename, ffprobe before success.
- Title text (finding #18, replaces Ideogram): rendered locally from a licensed font via Remotion/SVG → PNG. No image model touches typography. Ideogram tool dropped.
- All tools FAL_KEY only. No Higgsfield.

### 10. Poster (finding #17)
Completed entirely inside `visual_bible`: key art (Seedream, sheets as references, 4 candidates), local title card, composited `poster_final` (PIL/ffmpeg), all hashed with receipts. `compose` consumes `poster_final` only as a title-card/end-card video asset; `render_report.outputs` stays video-only.

### 11. Trailer format
- `proposal_packet` v1.1: `runtime_shape.format: trailer|teaser|short` and `cast {character_ids[], location_ids[]}` are **required** fields the proposal director must emit (JSON Schema `default` is annotation-only here, so no reliance on defaults); a normalization step in the loader fills format=trailer only for upgraded 1.0 packets.
- script/scene directors honor the format: beat selection from canon, protected lines verbatim, no new dialogue; 12–20 shots, one action per shot, longer takes over stitching.
- compose adds title/end cards from the poster assets; existing motion-ratio, audio, canon-pass gates unchanged.

### 12. Tests (TDD; finding #20)
- Update the authored-film helper's hardcoded stage order and every fixture affected by the new stage and the v1.1 schemas.
- `tests/lib/test_visual_bible_contract.py`: schema compile; approval without receipt rejected; hash mismatch; missing generation receipt; symlink/`..`/absolute/cross-project path rejected; cast coverage; supersede without ruling; narrowed continuity rule (entity-less shot passes, entity shot without refs fails); mixed selected takes rejected, rejected-candidate from other model ignored; override without reason; reference packing over cap fails preflight; budget cap preflight; poster in visual_bible; 2.0 payload unchanged; 2.5 payload matches fixture; resume after partial approvals.
- Adversarial tests (R3): crash between submit and request-id persistence leaves an `indeterminate` reservation and no resubmission; migrated 1.0 scene_plan without reviewed refs is rejected at `assets`; `record_human_approval` without a valid one-use gate token (missing, reused, wrong scope/digest) is rejected; live owner with stale heartbeat is not reclaimed, dead owner is; JPEG/WebP provider bytes hash identically after PNG re-encode.
- Failure-boundary tests (R2): legacy 1.0 artifacts still validate and every other pipeline's fixtures pass; approval-record tampering (prompt edit after receipt) rejected; paid-call crash before reconcile resumes by request id without a second charge; staging → objects move is atomic and rejects a bad parent; local-derivation receipt for title card/poster; project.yaml digest change without approval rejected; second process fails on the run lease; stale lease recovery.
- Tool tests with mocked FAL (no network). Invented names only in fixtures.

### 13. Pilot run (after build, supervised)
Bloodless, cast cap 4/4, budget cap set in project.yaml, egress ruling recorded, Ben rules every gate. Expected $300–$1,000.

## Key decisions & tradeoffs
- **D1 Extend authored-film, not a new pipeline.** Inherits canon enforcement + 42 tests; costs one more gate. Rejected: separate trailer pipeline (duplicate enforcement, sheets become trailer-only).
- **D2 Sheets are canon**, hashed, superseded only by logged ruling. Rejected: per-production assets (drift between outputs, no approval record).
- **D3 Synthetic-only cast**, runtime-rejects non-pipeline images. Rejected: real-photo references (likeness rights, no release record). Adding a release field later is a small migration.
- **D4 (revised 2026-08-25) Kling o3 pro reference-to-video primary (Seedance 2.5 rejects any human face as reference on FAL — proven 2026-08-25, Probes A/B in PLAN-REVIEW-LOG.md); Seedance only for entity-free scenes via per-scene override with reason. Model is a scene-level property, override per scene with reason, never mixed within a scene.** Rejected: per-shot choice (documented drift cause; round 2 briefly introduced it, round 3 removed it); stylizing characters to get past the Seedance filter (Probe A: still rejected).
- **D5 Storyboard frames are per-production assets in `assets`**, approved as a batch before video, used as a reference (and as start frame only if the verified 2.5 contract allows combining). Tradeoff: extra image spend, locks composition before video credits.
- **D6 Typography never generated by a model**: title rendered locally from a font and composited. Rejected: Ideogram (still in-model text; glyph errors documented) — Codex round 1 caught that the original plan contradicted itself here.
- **D7 Canon packet is immutable after ingest**; visual references live in the `visual_bible` artifact, not written back into the packet.
- **D8 Approval and generation receipts are the evidence**, not self-attested status fields; receipts hash the full approved record, and project config (budget, egress) is itself receipt-bound.
- **D9 Schemas are versioned side by side (1.0 `oneOf` 1.1)** so nothing outside authored-film changes behavior.
- Cosmetic locks (accepted as recommended, one amended in review): Seedream 5 Pro edit for sheets, Flux Kontext fallback; local title rendering (was Ideogram V3); folder layout above; per-character/per-location gates; 12–20 storyboard frames; pilot cap 4/4; no story names in code; PLAN files in this repo.

## Toolchain
- Claude build track loads: `pipelines/authored-film/*` (existing), new `visual-bible-director`, `meta/checkpoint-protocol`. Optional reads for prompt craft: Ben's `video-prompt-builder` and `shadow-director` skills (Claude bench) — advisory only, not runtime dependencies.
- Codex review/inspection track: none required.
- External references consulted, not adopted: `machina-exm/film-studio-skills` (asset passports), `HKUDS/ViMax`.

## Assumptions
(Confirmed ledger, 2026-08-25.) 1 brownfield on `~/Projects/OpenMontage` branch `authored-film`, fork remote exists. 2 story input via existing `canon_ingest`. 3 RAID studio layout not present; project truth in `projects/<slug>/`. 4 NBE roles are on-demand prompted workflows, not agents. 5 every gate human-ruled. 6–8 no sheet/poster/storyboard concept exists today; new stage needed. 9 reference-image conditioning over LoRA. 10 sheet contents per research. 11 lock statics before video; never mix strategies. 12 typography outside video model. 13 FAL backend, Higgsfield excluded. 14 FAL endpoints verified live 2026-08-25 (see `docs/research/2026-08-25-visual-bible-claudex-research.md`). 14a Seedance 2.5 on FAL rejects any recognisable human face as a reference — photoreal or illustrated — with `content_policy_violation` (Probes A/B, 2026-08-25); Kling o3 pro reference-to-video accepts them and holds identity; the no-video reference cap for Kling o3 is unconfirmed (tool caps at 9). 15 cost ~3 gens/usable shot, ~25% keep rate. 16 deliverable = trailer + poster + sheets. 17 pilot Bloodless. 18–19 toolchain scan as above.

## Risks / open questions
- Seedance 2.5 contract verification is now a §0 prerequisite, not a mitigation. If start frame + references cannot be combined, storyboards are reference-only. (Kling o3 does offer `start_image_url`; the storyboard frame stays a reference per D5 — a start-frame policy is a separate ruling.)
- `fal-ai/ideogram/character/edit` and `instant-character` unconfirmed on FAL; not depended on.
- Concurrency: single-threaded by design; a per-project run lease plus atomic state writes guard against two sessions on one project. Full parallel-worker locking remains out of scope.
- Expression-sheet and turnaround generation quality from a single hero portrait is unproven for this stack; may need 2–3 hero angles approved instead of one.
- Trailer needs faces; The Understudy avoided them. First real test of identity hold across 12–20 shots.
- Cost variance ($75 vs $750 per finished minute across sources).
- Python 3.14 env has missing modules (requests/PIL) noted 2026-08-08; `make setup` still not run.

- Gate trust boundary: the TTY-only handler and orchestrator-owned key separate the agent from approvals by process and discipline, not by an OS privilege boundary; a malicious local process running as the user could read the key. Acceptable for a single-user local studio; revisit if the pipeline ever runs multi-user.
- Run lease covers script orchestrators only; multi-turn agent sessions are not lease-protected (inspection #17, rejected with reason).

## Out of scope
Full-length features; Buzz/PitchStudio/WriterOS changes; RAID studio folder structure and role charters; recurring/scheduled agents; Higgsfield; real-person likeness; LoRA training (fallback only, not built now); audience/distribution strategy.
