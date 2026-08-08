# Edit Director — Authored-Film Pipeline

## When To Use

Sixth stage (`edit`). You produce `edit_decisions` that assemble the approved
assets into the writer's story at the approved runtime.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/edit_decisions.schema.json` | Artifact validation |
| Prior artifacts | `scene_plan`, `asset_manifest` | Structure + material |
| Optional | `script`, `canon_packet` | Beat truth for pacing decisions |

## Process

### 1. Cut On Turns

The writer built each beat to turn. Cuts land on turns; a turn is never
buried mid-shot or thrown away in a transition. When the edit wants to trim,
trim within beats — removing or reordering a beat is a story change and
follows canon-guard Collisions.

### 2. Protect the Protected

Protected lines: never trimmed, never ducked under music, never stepped on by
an SFX hit. Verify each protected line's audio region is clean in the decisions.

### 3. Pacing From the Tone Document

The bible's pacing rules govern rhythm globally; the approved treatment's
pacing shape governs the curve. Hero moments get room; the edit never
manufactures energy the tone document forbids.

### 4. Subtitles and Overlays

Subtitle text matches the script verbatim (the writer's punctuation included).
Overlays follow the scene plan's overlay_notes.

## No Gate By Default

`human_approval_default: false` — proceed on completion, but a Collision
(story-affecting trim, beat reorder) forces an `awaiting_human` checkpoint
regardless. Canon outranks the manifest's convenience.

## Common Pitfalls

- Fixing a pacing problem by cutting the beat that caused it — pacing problems
  at locked beats are gate conversations.
- Music swells that flatten a quiet turn the writer built deliberately.
