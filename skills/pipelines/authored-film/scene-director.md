# Scene Director — Authored-Film Pipeline

## When To Use

Seventh stage (`scene_plan`, manifest 1.2). You translate the approved script into an ordered
`scene_plan` whose scenes carry the canon's continuity constraints INTO the
plan itself — so asset generation cannot lose them.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Prior artifacts | `script`, `canon_packet`, `visual_bible` | Timing + continuity truth + the approved sheets scenes must bind to |
| Config | `projects/<slug>/project.yaml` | Default `model_endpoint` (Kling o3 pro reference-to-video) |
| Optional | `proposal_packet` | Approved treatment's shot vocabulary |

## Process

### 1. Coverage

Every script section gets at least one scene; `script_section_id` links each
scene back. Scene timing sums to the script's runtime.

### 2. Continuity Travels In the Scene

For every scene involving a tracked character or location, the scene
`description` (or `overlay_notes`) states the constraints generation must
honor: the location's `visual_identity`, the character's relevant
`behavioral_anchors` and `continuity_facts`, and the applicable
`visual_continuity_risks` from the annex. A downstream asset director reading
ONLY this scene must have everything needed to generate it consistently. This
is the stage where authored continuity either becomes enforceable or dies.

Every scene also carries `canon_refs` — the canon packet beat/lock ids it
realizes. Runtime-enforced: the checkpoint writer rejects a scene_plan whose
scenes have no `canon_refs`, or whose refs do not match known canon ids.

**Entity refs (v1.1).** Every scene carries `character_refs[]` and
`location_ref` naming `visual_bible` entity ids (which equal canon ids). These
are what the asset director packs as references, so they must be exact and
complete: a character visible in the shot but missing from `character_refs`
will be generated without their sheet. A scene with no tracked entity (title
card, abstract insert, pure landscape not in the bible) is marked
`entity_free: true` — that is the only legitimate empty-refs case. Refs are
never synthesized by a migration; an upgraded 1.0 plan is `needs_review` until
you re-emit them and the writer re-approves via a `kind: artifact_review`
receipt bound to `{artifact_type: "scene_plan", artifact_version,
artifact_digest: lib.canon_enforcement.artifact_review_digest(plan),
migration_status: "reviewed"}`. Once that receipt exists you flip
`migration_status` to `ok` and complete the stage; enforcement rejects
`needs_review` at completion even with the receipt, and rejects `ok` without
it. An edit after review changes the digest and needs a fresh receipt.

**Model default comes from `project.yaml`.** `scene.model_endpoint` must equal
the receipt-bound `default_video_endpoint` unless the scene carries
`model_override {endpoint, reason}`; the default is never inferred from the
plan itself.

**Model policy is per scene (D4).** `scene.model_endpoint` defaults to the
project's Kling o3 pro reference-to-video endpoint. `model_override {endpoint, reason}` is
allowed only with a real reason (a still-frame insert, a documented failure of
the default on this kind of shot). Seedance 2.5 is a valid override **only**
for scenes marked `entity_free: true`: on FAL it rejects any human face as a
reference (proven 2026-08-25), so a scene with `character_refs` must stay on
the default. Shots carry no model field; every shot and
every selected take in a scene uses the scene's endpoint. Never mix endpoints
within a scene.

### 3. Shot Language From the Treatment

Use the structured `shot_language` vocabulary (shot_size, camera_movement,
lens_mm, lighting_key, depth_of_field, color_temperature) to encode the
approved treatment — not generic coverage. `narrative_role` follows the beat's
function; `shot_intent` says why the shot exists in the writer's story terms.

### 4. Shots (v1.1) and the Trailer Format

Every scene carries `shots[]`, each `{shot_id, description}`; `shot_id` is
unique across the plan (e.g. `s03-sh02`) and is the key the storyboard frame,
every take, and every receipt downstream use. When `runtime_shape.format` is
`trailer` or `teaser`:

- **12–20 shots total** across the plan. Fewer is a thin trailer; more is a
  stitching problem.
- **One action per shot.** A shot description contains one subject doing one
  thing under one camera move. "She turns, then draws, then runs" is three
  shots or one bad one.
- **Longer takes over stitching.** Prefer a 6–10s take that holds a beat to
  three 3s fragments cut together; the model holds identity better within a
  take than across stitches.
- The shot's `description` is the generation brief: it is complete only if it
  names the entities (by ref), the action, the framing from `shot_language`,
  and the continuity constraints that apply.

### 5. Hero Moments and Styleframes

Every sequence in the annex's `key_visual_sequences` maps to scenes marked
`hero_moment: true`. In this pipeline every shot gets a storyboard frame in
`assets` (approved as a batch before video), so `styleframe_scenes` is
redundant with that step; keep the annex's list as a note of which frames
deserve extra candidates.

### 6. World Rules Are Physics

A scene that would show a `never_break` world rule being broken is a canon
collision — canon-guard Collisions procedure, not a creative choice.

### 7. Gate Presentation

Checkpoint `awaiting_human`: scene table with timings and types, hero moments
highlighted, per-scene `character_refs` / `location_ref` / `entity_free`,
`model_endpoint` (and any override with its reason), the shot list with
`shot_id`s and the total count, per-scene continuity constraints visible. END
YOUR TURN.

## Common Pitfalls

- Continuity constraints living only in your head (or only in the canon
  packet) instead of in the scene descriptions where generation will read them.
- Marking every scene hero_moment — if everything is a peak, nothing is.
- Shot language that fights the tone document (whip pans in a piece whose
  pacing rules say patient and observational).
- Leaving `character_refs` empty on a scene that shows a character "because
  the description mentions them" — the description is not a ref.
- Choosing a model per shot, or overriding the scene endpoint without a
  written reason.

## Gate Reminder (Binding)

`human_approval_default: true`. Present and END YOUR TURN.
