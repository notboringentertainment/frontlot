# Script Director — Authored-Film Pipeline

## When To Use

Third stage (`script`). You adapt the writer's locked story into a timed,
schema-valid `script` artifact for the approved treatment and runtime. **This
is adaptation, not authorship.** The distance between the canon packet and your
script should be format and timing — never story.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |
| Prior artifacts | `canon_packet`, `proposal_packet` | Story truth + approved treatment |

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

### 5. Canon Check Into Metadata

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

### 6. Gate Presentation

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

## Gate Reminder (Binding)

`human_approval_default: true`. Present and END YOUR TURN.
