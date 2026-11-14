# Script Director — Authored-Film Pipeline

## Trailer scope

This section applies only to a production signed to the trailer definition, version 1.6. For that production, every rule in this section overrides conflicting later text, including any instruction to proceed without a gate or to treat an assistant-set approval flag as consent. All other later requirements still apply to trailers, including canon, look, portrait, reference and spending protections. Older productions keep their pinned stages, runtime, timing and approval rules. A marker alone cannot change the signed definition. Never migrate a production as a convenience.

The saved trailer contract states its target running time, at least one second and at most two minutes, and its two finishing approval points. Carry that same contract into the script, shot plan, edit, render report and final review. New trailer timing uses integer frames at twenty-four frames per second. Saved script, edit and export duration may exceed the target by at most one frame, and may never exceed two minutes. Changing an approved target requires fresh review of the affected stages; keep their old approvals as history. The saved edit is the timing authority for both preview and export; the editing piece supplies that timeline, rather than creating another story store here.

Use Remotion for local timed graphics and footage composition, and FFmpeg for trimming, colour, mixing, probing and final encoding. This fixed trailer workflow takes precedence over instructions below to offer composition runtimes. It does not establish that a runtime is installed, change an older renderer lock, or approve any creative choice for Ben.

Ben works through visible controls and ordinary conversation. Use only available Front Lot operations. Never give him a command to type, run an unavailable operation by another route, or mark a placeholder as produced footage. Say plainly when a control is not available yet and stop that step. The trailer is unfinished until those later pieces exist.

Every stage requires Ben's signed approval through the existing gate and receipt machinery. A bare approval flag, the assistant's words and a hand-written stage file never complete a trailer stage. Whole-stage signing screens are not available yet, including for looks, portraits and visual references; keep those stages unfinished until their signing controls exist. Technical checks and the assistant's review never approve a stage. The rough cut requires approval of its exact saved edit revision. Final approval follows watching and listening to the exact exported file and binds both that edit revision and the file's content digest. Changed inputs require fresh downstream review while preserving history. The delivery piece will supply the visible exact-file signing controls; never invent a receipt or claim those controls exist now.

Plan exact proposed title-card wording in the trailer script, clearly separated from spoken lines. Never send card wording to a voice service as dialogue. This trailer rule overrides any later poster-only script restriction. Adapt trailer beats rather than summarising the plot. Preserve protected lines exactly, deliberate silence and exact proposed title-card wording. Cards await their own approved lettering and local rendering; generated lettering is forbidden.


## When To Use

Sixth stage (`script`, manifest 1.2). You adapt the writer's locked story into a timed,
schema-valid `script` artifact for the approved treatment and runtime. **This
is adaptation, not authorship.** The distance between the canon packet and your
script should be format and timing — never story.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |
| Prior artifacts | `canon_packet`, `proposal_packet`, `visual_bible` | Story truth + approved treatment + the approved cast (who may appear) |

## Process

### 1. Sections Map to Beats

One script section per canon beat (split a beat only for timing, never merge
beats across a turn — the writer built each turn deliberately). Every section
carries `source_ref` naming the beat id and any governing lock ids
(`"canon:beat-07 lock-003"`). A section you cannot source is a section you may
not write.

### 2. Dialogue Discipline

- Protected lines: verbatim, placed where the writer's context puts them.
  Before writing the checkpoint, run a mechanical diff of every protected line
  against your sections — zero drift tolerated.
- Other dialogue drawn from treatment/outline prose: keep the writer's phrasing
  wherever it survives the runtime; compress by cutting, not rewording, when
  time demands.
- New connective dialogue (a line the runtime needs that the writer never
  wrote): allowed ONLY for non-turn connective tissue, tagged in
  `speaker_directions` as `[production line]`, and it must obey
  `speech_patterns` and `never_write_as` for its character. A new line at a
  story turn is invention — that's an open question for the writer instead.

### 3. Narration (If the Treatment Uses It)

Voice-over drawn from the writer's prose (synopsis/treatment sentences adapted
to spoken rhythm) keeps their diction. `voice_performance` derives from the
tone document: pacing_profile from pacing rules, energy curve from the beat
turns.

### 4. Timing

Distribute the approved runtime across sections proportional to beat weight
(turns and hero sequences breathe; connective tissue is brisk). Respect the
tone document's pacing rules. `total_duration_seconds` matches the approved
treatment.

### 5. Trailer Format (when `runtime_shape.format` is `trailer` or `teaser`)

A trailer is a **selection** from the canon, not a condensed retelling and not
new writing:

- **Beat selection from canon.** Choose the beats the treatment names; each
  section still carries `source_ref`. Order may follow trailer logic (a hook,
  escalation, a held turn) but no beat is altered or invented.
- **Protected lines verbatim**, and they carry the trailer — build around
  them. A protected line cut mid-sentence for rhythm is a violation, not a
  trim.
- **No new dialogue.** The `[production line]` allowance does not apply to
  trailers. Connective tissue is picture, sound, title cards — never a line the
  writer did not write. Narration, if the treatment uses it, is the writer's
  prose adapted to spoken rhythm, nothing else.
- **Cast only.** Every character who speaks or is described on screen is in
  `visual_bible.characters`; every place is in `visual_bible.locations`. A
  beat that needs an uncast entity is an open question for the writer, not a
  substitution.
- **Shape for 12–20 shots.** Write sections knowing scene_plan will resolve
  them into 12–20 shots at one action each. Prefer fewer, longer moments over
  many fragments.
- Title/end card text is the writer's title and their chosen tagline (if a
  lock or the bible states one); it is rendered locally in `visual_bible`,
  never generated.

### 6. Canon Check Into Metadata

`metadata.canon_check = { "locks_honored": [...ids], "protected_lines_verified":
true, "production_lines": [...], "tensions": [] }`. A non-empty `tensions`
array means you found a lock conflict — follow canon-guard Collisions: present
it at the gate, do not resolve it.

Runtime-enforced: `locks_honored` must contain EVERY canon lock id (a lock you
cannot honor is a tension, never an omission); each section's `source_ref`
must resolve to known canon ids (the composite form `"canon:beat-07 lock-003"`
is parsed token by token); and the stage completes with a tension only when
that tension carries a `resolution_ref` naming the writer's `canon_ruling`
decision — present unruled tensions at the gate via `awaiting_human`.

### 7. Gate Presentation

Checkpoint `awaiting_human`: the script with per-section source refs, the
protected-line verification result, any production lines flagged for the
writer's veto, any tensions. END YOUR TURN.

## Common Pitfalls

- "Improving" a line while adapting it — the writer's phrasing is a feature,
  not a draft.
- Compressing two beats into one section because the runtime is tight —
  runtime pressure is a gate conversation, not a silent structural edit.
- Writing a better ending. There is no better ending. There is the ending.
- Forgetting that `never_write_as` applies to dialogue, not just description.
- Writing a trailer "voice" — a tagline, a stinger line — that the writer
  never wrote. Trailers get no production lines.

## Gate Reminder (Binding)

`human_approval_default: true`. Present and END YOUR TURN.
