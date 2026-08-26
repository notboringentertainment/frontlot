# Asset Director — Authored-Film Pipeline

## When To Use

Eighth stage (`assets`, manifest 1.2). You generate/select every asset the
scene plan needs, with the canon packet's continuity apparatus applied at
generation time and recorded in the manifest. Every governed visual call you
make names the ratified looks it renders (`look_refs`) — the only exception is
a shot whose approved scene record is `entity_free: true`, and that is checked
server-side, not taken from you.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Artifact validation |
| Prior artifacts | `scene_plan` (v1.1: `shots[]`, refs, `model_endpoint`), `canon_packet`, `visual_bible` (v1.1: `look_ref`, `headshot_ref`, `prompt_recipe`, `sheet_revision` per entity), `look_packet` | What to make + continuity truth + approved sheets + the ratified looks they render |
| Prompt builder | `tools/prompt_builder` | Rebuilds each entity's identity paragraph from its `prompt_recipe`; verifies `rendered_sha256` |
| Config | `projects/<slug>/project.yaml` | Budget cap, run lease, egress (already approved before this stage) |
| Layer 3 | `.agents/skills/kling-o3-reference`, `.agents/skills/seedream` (and `.agents/skills/seedance-2-5` only for entity-free scenes) | Provider prompting guidance |
| Tools | `seedream_image` (storyboard frames), `kling_reference_video` (default: Kling o3 pro reference-to-video, faces allowed), `seedance_video` (`model_version: 2.5`, entity-free scenes only via `model_override`), music tools, TTS, subtitle_gen, audio_enhance | Capabilities |

## Process

### 1. Storyboard Frames First — One Per `shot_id`, Approved as a Batch

Before any video call, generate a `storyboard_frame` for **every** `shot_id` in
the scene plan (`asset_class: storyboard_frame`, `type: image`, `shot_id`,
`continuity.references_applied` required). Each frame is a `seedream_image`
`edit` call with the shot's approved sheets as references (packing policy
below, minus the storyboard slot), the shot's `description`, each character's
identity paragraph **rebuilt from its `prompt_recipe`** (§2a), the palette,
and the shot's `shot_language` framing, with `look_refs` for every entity in
the shot. Storyboard frames are per-production assets that **cite** canon;
they are not canon and do not enter `canon/visual/`.

Present the whole set on the filmstrip and checkpoint `awaiting_human`. The
writer approves the batch (receipt `kind: storyboard_batch`, minted by the gate
handler — never by you) or names frames to redo. Redo → re-present the batch.
The approval record is the ordered map `{"storyboard_frames": {shot_id:
sha256}}` (`lib.canon_enforcement.storyboard_batch_record`), so the receipt
binds each frame to its shot — frames cannot be swapped between shots after
approval, and a redone frame needs a new batch receipt.
**No `kling_reference_video` (or `seedance_video`) call may run until the batch receipt exists.** Approval
precedes *spend*, not selection: enforcement rejects any `shot_visual` that is
`candidate` or `selected` (anything not `rejected`) whose `shot_id → frame
hash` is not covered by a verified `storyboard_batch` receipt
(`lib.receipts.require_storyboard_receipt`, which the video tool also calls in
preflight).

The tool enforces this itself: for **every** shot take you call `kling_reference_video`
with `asset_class: shot_visual`, `shot_id`, and `storyboard_frame_sha256` (the
approved frame's asset id) — these are **required**, not opt-in. Preflight
verifies the signed storyboard receipt covering that exact `shot_id → frame`
pair before any reservation or upload, then **locates the frame file itself**
(`storyboard_frame_path`, a project-local path, or by hash at
`canon/visual/objects/<sha>.png`), re-hashes it, and requires the hash to equal
`storyboard_frame_sha256`. That file is uploaded and packed **last** in
`image_urls`. A missing receipt, a missing file, or a hash mismatch fails the
call with no budget consumed. Never omit these inputs on a shot take, and never
pass `asset_class` for non-shot material. A shot take accepts **only local,
hash-verified references**: `reference_image_urls` is refused on `shot_visual`.

The storyboard frame is a **reference** (composition, blocking, light), not a
first frame: Kling o3 packs it as the last `@ImageN`, and Seedance 2.5 has no
start-frame parameter at all. Do not promise the writer the video will begin on
it. (Kling's optional `start_image_url` is refused on `shot_visual` takes.)

**Why Kling, not Seedance (D4 revised 2026-08-25).** On FAL, Seedance 2.5
rejects *any* recognisable human face as a reference (photoreal or drawn —
`content_policy_violation`, see `PLAN-REVIEW-LOG.md` → "Probes"). Kling o3
pro reference-to-video accepted the same pipeline-generated portraits and held
identity. Seedance stays available only for scenes marked `entity_free: true`
(no character refs) through a scene-level `model_override` with a reason.

### 2. Reference Packing (Deterministic, Per Shot)

For each shot, pack references in this exact order, each becoming `@ImageN` in
the Kling o3 prompt (N in packing order; the `elements` form groups the same
manifest into `@ElementN` per entity — hero → frontal, other views → references):

1. For every id in the scene's `character_refs`, in order: that character's
   approved **hero**, then its **wardrobe** view.
2. For `location_ref`: the location's approved **establishing** plate.
3. The shot's approved **storyboard frame**, last.

Preflight counts the pack against the verified endpoint cap (Kling o3: 4 total
when a video reference is present; otherwise a conservative 9 until the
no-video cap is confirmed live; Seedance 2.5: 30 images) and **fails** if over — it never truncates
silently. If a shot legitimately cannot fit, that is a scene-plan problem
(too many entities in one shot), raised at the gate, not solved by dropping a
reference.

The prompt addresses each reference by slot: "@Image1 is the character's face
and hair — maintain exact appearance; @Image2 is her costume — do not alter
garment category or colour; @Image3 is the location; @Image4 is the
composition to match." Then the shot's action (one action), each character's
rebuilt identity paragraph (§2a), the `wardrobe_negative`, the scene's
continuity constraints, the palette hues, and the look's `continuity_risks[]`
/ `negative_lines[]` as negative lines. A reference that is packed but not
addressed in the prompt is not applied.

### 2a. Rebuild Identity Paragraphs From the Recipe — Never Paste, Never Reword

The visual bible no longer carries an `approved_prompt_block` string. Each
entity carries a `prompt_recipe {look_hash, builder_version, fields_used[],
rendered_sha256}`. For every entity in a shot:

1. Call `tools/prompt_builder` with the entity's recipe. It reads the ratified
   `look_spec` for `look_hash` from `look_packet`, renders the same
   `fields_used[]` with the same `builder_version`, and returns the paragraph.
2. Verify the returned `rendered_sha256` equals the recipe's. A mismatch means
   the look was superseded, the builder version moved, or the bible is stale
   — stop and raise it at the gate; do not "fix" the text.
3. Insert the paragraph unchanged into the prompt, and pass `look_refs:
   [{entity_kind, entity_id, look_hash}]` for every entity the shot renders.

You never type identity lines by hand and never edit the rendered paragraph.
`paid_call_context` verifies each look ref against the active `look_lock`
receipt before upload, checks the prompt against the recipe, and binds
`look_refs` into the signed generation receipt. A call whose `look_hash` is
not the current active look for that key fails with no budget consumed.

**`look_refs` is required on every governed visual call** (`seedream_image`,
`kling_reference_video`, `seedance_video`). A call may omit it only when it
names a `shot_id` whose approved scene-plan record is `entity_free: true` —
the tool looks that up in the approved `scene_plan` checkpoint itself; a
boolean you pass is never trusted. There is no other entity-free path, and no
non-shot material with a tracked entity in it.

`continuity.references_applied[]` has two item shapes (asset_manifest 1.1):
an **entity reference** `{asset_id, path, role, visual_bible_entity_id}` for
every approved sheet packed, and one **storyboard reference** `{asset_id,
path, role: "storyboard", shot_id}` for the frame — `shot_id` must be the
take's own shot and `asset_id` the content hash of that shot's approved
`storyboard_frame`. Only entity references count toward entity coverage; a
storyboard reference never stands in for a missing sheet.

**Caller contract (binding):** pass the entity references twice, in the same
order — `reference_image_paths` (the files) and `reference_manifest` (one
object per file: `{asset_id, path, role, visual_bible_entity_id}`). The tool
resolves each `path` inside the project, re-hashes the file, and refuses the
call if the hash differs from `asset_id`, if the manifest and the path list
differ in length or order, or if a storyboard object appears in the manifest
(the tool appends the frame itself from `shot_id` /
`storyboard_frame_sha256`). `reference_manifest` is required whenever
`reference_image_paths` is non-empty, for shot and non-shot calls alike.

The list the tool actually uploaded, in payload order, comes back as
`result.metadata.references_applied` and is sealed into the signed generation
receipt. Copy it **verbatim** (same objects, same order — the storyboard entry
carries the project-relative `path` the tool reports) into the take's
`continuity.references_applied[]`. Enforcement rejects any non-rejected
`shot_visual` whose manifest list does not equal the receipt's list, or that
cites anything other than exactly one storyboard reference (its own shot's
approved frame).

### 3. Takes, Endpoints, and Receipts (Single Strategy)

- Every video asset is a `shot_visual`: `shot_id`, `take_id`, `usage_status`
  (`candidate` | `selected` | `rejected`), `model_endpoint`, and
  `continuity.references_applied`.
- `model_endpoint` is the **scene's** endpoint (default Kling o3 pro reference-to-video, or the
  scene's `model_override.endpoint` with its reason). Candidates may be
  generated on it only. Every `selected` take in a scene shares the scene
  endpoint — enforcement rejects a plan where selected takes in one scene use
  different endpoints, or where an override lacks a reason.
- Every generated asset — storyboard frame, candidate, selected take — has a
  generation receipt in `generation-receipts.jsonl` whose `output_sha256` is
  the asset's hash. No receipt, no asset; a file that did not come through the
  wrapped tool cannot be selected. Receipts also seal the `prompt`, `seed`
  and (for takes) `references_applied`.
- Budget: ~3 candidates per shot is the honest planning number; each paid
  call is reserved before submission. An `indeterminate` reservation on resume
  halts the stage for human reconciliation — never resubmit.
- Crash safety: the paid tools write a generation write-ahead entry the moment
  a verified output exists, then receipt → ledger → terminal reservation →
  WAL delete. A crash in between is replayed automatically by the next paid
  call's `resume_check` (or by `scripts/reconcile_paid_calls.py`); only an
  output that has gone missing blocks — recover it, never regenerate it.
- Prefer one longer take per shot over stitching shorter clips; a take that
  drifts identity mid-shot is `rejected`, not trimmed around.

### 4. Continuity Into Every Prompt

For each generated asset involving a tracked character or location:

- Start from the `visual_bible` sheets (the canon packet's
  `reference_assets` / `reference_assets_needed` describe the writer's
  intent and are advisory only; the sheets are what the model sees). Every
  reference you pack is local, hash-verified, and has a receipted lineage
  back to a pipeline generation or an attested `imported_synthetic` import;
  remote URLs are refused, and anything touching a `casting_inspiration`
  hash fails before upload.
- Rebuild each character's identity paragraph from its `prompt_recipe` (§2a)
  and add the `wardrobe_negative`. Never paste, never reword.
- Confirm the sheet's `look_ref.look_hash` is still the active look and its
  `headshot_ref` still the current hero; a retired look or superseded hero
  has already invalidated the bible, and the runtime will refuse the call.
- Carry the scene's continuity constraints into the generation prompt.
- Apply the look's `continuity_risks[]` / `negative_lines[]` (and the annex's
  `visual_continuity_risks`) as explicit negative/guard instructions, and the
  annex's `prompt_language_notes` where they apply.
- Record the evidence in each visual asset's structured `continuity` field:
  `canon_refs` (the lock/beat ids or entity ids the asset must stay true to),
  `references_applied` (objects, above), and `risk_notes_applied`. Runtime-
  enforced: the checkpoint writer rejects an asset_manifest whose image/video/
  animation entries have no `continuity` object, and — when the shot's
  `character_refs`/`location_ref` are non-empty — one whose
  `references_applied` does not cover every referenced entity. Shots on
  `entity_free` scenes (title cards, transitions, abstract inserts) are exempt
  from `look_refs` and entity coverage only because the approved scene record
  says so — they are `asset_class: non_shot` or carry empty refs honestly,
  and the tool verifies the `shot_id` against the approved plan.

A character drifting off their described appearance between scenes is the
authored-film equivalent of a broken build.

### 5. Narration and Dialogue Audio

TTS honors the script's `delivery_cues` and `voice_performance`. Protected
lines get a listen-check: the line as synthesized must be the line as written
(no TTS normalization silently rewording). Flag any pronunciation gap as a
`pronunciation_guides` addition rather than respelling the writer's text.

### 6. Music and Ambience

Resolved against the bible's sound/music style and the approved treatment's
music direction. The tone document's `must_never_feel_like` applies to the
music bed as strongly as to the visuals.

### 7. Gate Presentation

This stage gates twice: once for the storyboard batch (§1), once for the
takes. The final checkpoint `awaiting_human` carries the scene-by-scene contact
sheet: storyboard frame beside each selected take, candidates with
`usage_status`, prompts with the `@ImageN` mapping, `references_applied`,
per-asset cost and receipt ids, spend against cap. END YOUR TURN.

## Common Pitfalls

- Listing references in the manifest without them ever touching a prompt or
  the payload.
- Typing identity lines by hand, editing the rebuilt paragraph, or reusing a
  paragraph whose `rendered_sha256` no longer matches the recipe.
- Omitting `look_refs` on a governed call, or claiming a shot is entity-free
  when its approved scene record is not.
- Packing a reference with no receipted lineage (or a remote URL) and hoping
  the provider does not mind.
- Generating video before the storyboard batch is approved, or generating a
  storyboard frame without the sheets as references.
- Mixing endpoints inside a scene, or choosing a model per shot.
- Truncating the reference pack to fit a cap instead of failing preflight.
- Treating the storyboard frame as a start frame (Kling packs it as a reference; Seedance 2.5 has no start frame).
- Sending a shot with character refs to Seedance 2.5 (it will be rejected for faces; use the default Kling endpoint).
- Letting a provider's house style override the treatment (the playbook and
  tone document outrank a model's defaults — regenerate or switch providers).
- Generating hero moments with the same effort as connective scenes — heroes
  earn extra takes.

## Gate Reminder (Binding)

`human_approval_default: true`. Present and END YOUR TURN.
