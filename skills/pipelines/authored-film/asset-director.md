# Asset Director — Authored-Film Pipeline

## When To Use

Fifth stage (`assets`). You generate/select every asset the scene plan needs,
with the canon packet's continuity apparatus applied at generation time and
recorded in the manifest.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Artifact validation |
| Prior artifacts | `scene_plan` (v1.1: `shots[]`, refs, `model_endpoint`), `canon_packet`, `visual_bible` | What to make + continuity truth + approved sheets |
| Config | `projects/<slug>/project.yaml` | Budget cap, run lease, egress (already approved before this stage) |
| Layer 3 | `.agents/skills/seedance-2-5`, `.agents/skills/seedream` | Provider prompting guidance |
| Tools | `seedream_image` (storyboard frames), `seedance_video` (`model_version: 2.5`, reference-to-video), music tools, TTS, subtitle_gen, audio_enhance | Capabilities |

## Process

### 1. Storyboard Frames First — One Per `shot_id`, Approved as a Batch

Before any video call, generate a `storyboard_frame` for **every** `shot_id` in
the scene plan (`asset_class: storyboard_frame`, `type: image`, `shot_id`,
`continuity.references_applied` required). Each frame is a `seedream_image`
`edit` call with the shot's approved sheets as references (packing policy
below, minus the storyboard slot), the shot's `description`, the character
`approved_prompt_block`s pasted verbatim, the palette, and the shot's
`shot_language` framing. Storyboard frames are per-production assets that
**cite** canon; they are not canon and do not enter `canon/visual/`.

Present the whole set on the filmstrip and checkpoint `awaiting_human`. The
writer approves the batch (receipt `kind: storyboard_batch`, minted by the gate
handler — never by you) or names frames to redo. Redo → re-present the batch.
The approval record is the ordered map `{"storyboard_frames": {shot_id:
sha256}}` (`lib.canon_enforcement.storyboard_batch_record`), so the receipt
binds each frame to its shot — frames cannot be swapped between shots after
approval, and a redone frame needs a new batch receipt.
**No `seedance_video` call may run until the batch receipt exists.** Approval
precedes *spend*, not selection: enforcement rejects any `shot_visual` that is
`candidate` or `selected` (anything not `rejected`) whose `shot_id → frame
hash` is not covered by a verified `storyboard_batch` receipt
(`lib.receipts.require_storyboard_receipt`, which the video tool also calls in
preflight).

The tool enforces this itself: for **every** shot take you call `seedance_video`
with `asset_class: shot_visual`, `shot_id`, and `storyboard_frame_sha256` (the
approved frame's asset id). Preflight verifies the signed storyboard receipt
covering that exact `shot_id → frame` pair before any reservation or upload;
a missing or mismatched receipt fails the call with no budget consumed. Never
omit these three inputs on a shot take, and never pass `asset_class` for
non-shot material.

Seedance 2.5 has no start-frame parameter: the storyboard frame is a
**reference** (composition, blocking, light), not a first frame. Do not promise
the writer the video will begin on it.

### 2. Reference Packing (Deterministic, Per Shot)

For each shot, pack references in this exact order, each becoming `@ImageN` in
the Seedance 2.5 prompt (N in packing order):

1. For every id in the scene's `character_refs`, in order: that character's
   approved **hero**, then its **wardrobe** view.
2. For `location_ref`: the location's approved **establishing** plate.
3. The shot's approved **storyboard frame**, last.

Preflight counts the pack against the verified endpoint cap (`seedance-2.5
reference-to-video`: 30 images) and **fails** if over — it never truncates
silently. If a shot legitimately cannot fit, that is a scene-plan problem
(too many entities in one shot), raised at the gate, not solved by dropping a
reference.

The prompt addresses each reference by slot: "@Image1 is the character's face
and hair — maintain exact appearance; @Image2 is her costume — do not alter
garment category or colour; @Image3 is the location; @Image4 is the
composition to match." Then the shot's action (one action), the pasted
`approved_prompt_block` for each character, the `wardrobe_negative`, the
scene's continuity constraints, the palette hues, and the annex's
`visual_continuity_risks` as negative lines. A reference that is packed but not
addressed in the prompt is not applied.

`continuity.references_applied[]` has two item shapes (asset_manifest 1.1):
an **entity reference** `{asset_id, path, role, visual_bible_entity_id}` for
every approved sheet packed, and one **storyboard reference** `{asset_id,
path, role: "storyboard", shot_id}` for the frame — `shot_id` must be the
take's own shot and `asset_id` the content hash of that shot's approved
`storyboard_frame`. Only entity references count toward entity coverage; a
storyboard reference never stands in for a missing sheet.

Record every packed reference as an object in
`continuity.references_applied[]`: `{asset_id, path, role,
visual_bible_entity_id}` (storyboard frames use the frame's own manifest id and
no entity id). The EP compares this list against the request payload stored
with the generation receipt.

### 3. Takes, Endpoints, and Receipts (Single Strategy)

- Every video asset is a `shot_visual`: `shot_id`, `take_id`, `usage_status`
  (`candidate` | `selected` | `rejected`), `model_endpoint`, and
  `continuity.references_applied`.
- `model_endpoint` is the **scene's** endpoint (default Seedance 2.5, or the
  scene's `model_override.endpoint` with its reason). Candidates may be
  generated on it only. Every `selected` take in a scene shares the scene
  endpoint — enforcement rejects a plan where selected takes in one scene use
  different endpoints, or where an override lacks a reason.
- Every generated asset — storyboard frame, candidate, selected take — has a
  generation receipt in `generation-receipts.jsonl` whose `output_sha256` is
  the asset's hash. No receipt, no asset; a file that did not come through the
  wrapped tool cannot be selected.
- Budget: ~3 candidates per shot is the honest planning number; each paid
  call is reserved before submission. An `indeterminate` reservation on resume
  halts the stage for human reconciliation — never resubmit.
- Prefer one longer take per shot over stitching shorter clips; a take that
  drifts identity mid-shot is `rejected`, not trimmed around.

### 4. Continuity Into Every Prompt

For each generated asset involving a tracked character or location:

- Start from the `visual_bible` sheets (the canon packet's
  `reference_assets` describe the writer's intent; the sheets are what the
  model sees).
- Paste `approved_prompt_block` verbatim — never reworded — and the
  `wardrobe_negative`.
- Carry the scene's continuity constraints into the generation prompt.
- Apply the annex's `visual_continuity_risks` as explicit negative/guard
  instructions, and its `prompt_language_notes` where they apply.
- Record the evidence in each visual asset's structured `continuity` field:
  `canon_refs` (the lock/beat ids or entity ids the asset must stay true to),
  `references_applied` (objects, above), and `risk_notes_applied`. Runtime-
  enforced: the checkpoint writer rejects an asset_manifest whose image/video/
  animation entries have no `continuity` object, and — when the shot's
  `character_refs`/`location_ref` are non-empty — one whose
  `references_applied` does not cover every referenced entity. Shots on
  `entity_free` scenes (title cards, transitions, abstract inserts) are exempt
  and are `asset_class: non_shot` or carry empty refs honestly.

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
- Generating video before the storyboard batch is approved, or generating a
  storyboard frame without the sheets as references.
- Mixing endpoints inside a scene, or choosing a model per shot.
- Truncating the reference pack to fit a cap instead of failing preflight.
- Treating the storyboard frame as a start frame (Seedance 2.5 has none).
- Letting a provider's house style override the treatment (the playbook and
  tone document outrank a model's defaults — regenerate or switch providers).
- Generating hero moments with the same effort as connective scenes — heroes
  earn extra takes.

## Gate Reminder (Binding)

`human_approval_default: true`. Present and END YOUR TURN.
