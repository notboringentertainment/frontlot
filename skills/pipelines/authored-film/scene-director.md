# Scene Director — Authored-Film Pipeline

## When To Use

Fourth stage (`scene_plan`). You translate the approved script into an ordered
`scene_plan` whose scenes carry the canon's continuity constraints INTO the
plan itself — so asset generation cannot lose them.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Prior artifacts | `script`, `canon_packet` | Timing + continuity truth |
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

### 3. Shot Language From the Treatment

Use the structured `shot_language` vocabulary (shot_size, camera_movement,
lens_mm, lighting_key, depth_of_field, color_temperature) to encode the
approved treatment — not generic coverage. `narrative_role` follows the beat's
function; `shot_intent` says why the shot exists in the writer's story terms.

### 4. Hero Moments and Styleframes

Every sequence in the annex's `key_visual_sequences` maps to scenes marked
`hero_moment: true`. Scenes listed in `styleframe_scenes` get a note that a
styleframe must be generated and approved before batch asset generation — the
assets stage honors this ordering.

### 5. World Rules Are Physics

A scene that would show a `never_break` world rule being broken is a canon
collision — canon-guard Collisions procedure, not a creative choice.

### 6. Gate Presentation

Checkpoint `awaiting_human`: scene table with timings and types, hero moments
highlighted, styleframe list, per-scene continuity constraints visible. END
YOUR TURN.

## Common Pitfalls

- Continuity constraints living only in your head (or only in the canon
  packet) instead of in the scene descriptions where generation will read them.
- Marking every scene hero_moment — if everything is a peak, nothing is.
- Shot language that fights the tone document (whip pans in a piece whose
  pacing rules say patient and observational).

## Gate Reminder (Binding)

`human_approval_default: true`. Present and END YOUR TURN.
