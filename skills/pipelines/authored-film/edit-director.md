# Edit Director — Authored-Film Pipeline

## Trailer scope

For a signed trailer, the cutting view supports separately signed intertitles and an end card. This overrides the later poster-only title restriction for those cards. Their exact words, licensed font file, size, presentation and whole-frame timing come from the saved edit. Do not rewrite, trim, re-case, re-punctuate, wrap or shrink the wording automatically. Save a draft, then have Ben review and sign that exact card through the existing gate. Changed words, font, size or timing need another signature before preview. The local title tool sets the lettering and Remotion places it at the saved frames. Missing fonts, unsupported lettering or text outside the safe margins are refusals, never substitutions. Approved poster files remain immutable and all final pinned poster requirements remain in force. Signing a card approves no stage.

This section applies only to a production signed to the trailer definition, version 1.6. For that production, every rule in this section overrides conflicting later text, including any instruction to proceed without a gate or to treat an assistant-set approval flag as consent. All other later requirements still apply to trailers, including canon, look, portrait, reference and spending protections. Older productions keep their pinned stages, runtime, timing and approval rules. A marker alone cannot change the signed definition. Never migrate a production as a convenience.

The saved trailer contract states its target running time, at least one second and at most two minutes, and its two finishing approval points. Carry that same contract into the script, shot plan, edit, render report and final review. New trailer timing uses integer frames at twenty-four frames per second. Saved script, edit and export duration may exceed the target by at most one frame, and may never exceed two minutes. Changing an approved target requires fresh review of the affected stages; keep their old approvals as history. The saved edit is the timing authority for both preview and export; the editing piece supplies that timeline, rather than creating another story store here.

Use Remotion for local timed graphics and footage composition, and FFmpeg for trimming, colour, mixing, probing and final encoding. This fixed trailer workflow takes precedence over instructions below to offer composition runtimes. It does not establish that a runtime is installed, change an older renderer lock, or approve any creative choice for Ben.

Sound-effect takes are prepared and signed in the board's audio workspace. Use only
kept, unchanged takes with their recorded moment and provenance. A loop intention
does not mix or repeat a take automatically. The cutting view will place and mix
these effects; preparing a take does not make a rough cut or finish a stage. Never
call generation from a mixer or substitute another service.

Ben works through visible controls and ordinary conversation. Use only available Front Lot operations. Never give him a command to type, run an unavailable operation by another route, or mark a placeholder as produced footage. Say plainly when a control is not available yet and stop that step. The trailer is unfinished until those later pieces exist.

Every stage requires Ben's signed approval through the existing gate and receipt machinery. A bare approval flag, the assistant's words and a hand-written stage file never complete a trailer stage. Technical checks and the assistant's review never approve a stage. In Delivery, Ben uses “Review and sign the saved rough cut”, then “Make the final file”. After watching and listening, he uses “Review and sign final approval” for that exact exported file. “Show approved file in Finder” reveals the approved local file; nothing is uploaded. Earlier stage reviews and the separate story review remain binding. Other whole-stage signing screens that are not available, including looks, portraits and visual references, remain unfinished. The rough-cut signature binds the exact saved edit revision; final approval also binds the file and its measurements. Changed inputs require fresh downstream review while preserving history. Never invent a receipt.

The rough cut must use accepted media, readable cards, source trims and intentional sound placement. Save the edit revision digest with the edit decisions. Do not automatically complete this stage after a technical pass. The cutting view provides playback; Delivery provides the exact-revision signing control.


## When To Use

Ninth stage (`edit`, manifest 1.2). You produce `edit_decisions` that assemble the approved
assets into the writer's story at the approved runtime.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/edit_decisions.schema.json` | Artifact validation |
| Prior artifacts | `scene_plan`, `asset_manifest` | Structure + material |
| Optional | `script`, `canon_packet` | Beat truth for pacing decisions |
| Optional | `visual_bible` | `poster.poster_final` / `title_card` for title and end cards only |

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

### 5. Title and End Cards From the Poster

When `visual_bible.poster` is approved, the trailer's title card and end card
are `poster_final` (or `title_card` alone, per the treatment) held as a still
for the timed duration. That is the **only** use of the poster assets in the
cut: they are not b-roll, not a background plate, not a transition texture.
Never re-render, re-type, or model-generate the title — typography is local and
already approved. Cut only `selected` takes; a `candidate` or `rejected` take
in the timeline is a defect.

## Approval defaults for older productions only

Trailers do not use this default. Use Delivery for Ben's signed rough-cut approval. Existing earlier reviews remain required; no technical pass completes the stage.

`human_approval_default: false` — proceed on completion, but a Collision
(story-affecting trim, beat reorder) forces an `awaiting_human` checkpoint
regardless. Canon outranks the manifest's convenience.

## Common Pitfalls

- Fixing a pacing problem by cutting the beat that caused it — pacing problems
  at locked beats are gate conversations.
- Music swells that flatten a quiet turn the writer built deliberately.
