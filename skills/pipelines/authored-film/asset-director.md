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
| Prior artifacts | `scene_plan`, `canon_packet` | What to make + continuity truth |
| Tools | Per manifest: image/video selectors, generators, music tools, subtitle_gen, audio_enhance | Capabilities |

## Process

### 1. Styleframes First

Scenes flagged for styleframes in the scene plan: generate the styleframe,
present for approval (this stage's gate can be visited more than once — the
Backlot storyboard filmstrip is the natural surface), THEN batch-generate.
Never batch ahead of an unapproved styleframe for the same sequence.

### 2. Continuity Into Every Prompt

For each generated asset involving a tracked character or location:

- Start from `reference_assets` when the canon packet lists them.
- Carry the scene's continuity constraints into the generation prompt.
- Apply the annex's `visual_continuity_risks` as explicit negative/guard
  instructions, and its `prompt_language_notes` where they apply.
- Record the evidence in each visual asset's structured `continuity` field:
  `canon_refs` (the lock/beat ids or character/location names the asset must
  stay true to), `references_applied`, and `risk_notes_applied`. Runtime-
  enforced: the checkpoint writer rejects an asset_manifest whose image/video/
  animation entries have no `continuity` object.
- Record in the manifest entry which references and risk notes were applied —
  the EP spot-checks that this is real, not decorative.

A character drifting off their described appearance between scenes is the
authored-film equivalent of a broken build.

### 3. Narration and Dialogue Audio

TTS honors the script's `delivery_cues` and `voice_performance`. Protected
lines get a listen-check: the line as synthesized must be the line as written
(no TTS normalization silently rewording). Flag any pronunciation gap as a
`pronunciation_guides` addition rather than respelling the writer's text.

### 4. Music and Ambience

Resolved against the bible's sound/music style and the approved treatment's
music direction. The tone document's `must_never_feel_like` applies to the
music bed as strongly as to the visuals.

### 5. Gate Presentation

Checkpoint `awaiting_human` with the scene-by-scene contact sheet: takes,
prompts, per-asset cost, continuity applications, quality scores. END YOUR
TURN.

## Common Pitfalls

- Listing references in the manifest without them ever touching a prompt.
- Letting a provider's house style override the treatment (the playbook and
  tone document outrank a model's defaults — regenerate or switch providers).
- Generating hero moments with the same effort as connective scenes — heroes
  earn extra takes.

## Gate Reminder (Binding)

`human_approval_default: true`. Present and END YOUR TURN.
