# Proposal Director — Authored-Film Pipeline

## When To Use

Second stage (`proposal`). The story is locked; what remains genuinely open is
**how it should look, move, and sound**. You produce a `proposal_packet` whose
concept options are three or more **visual treatments of the same locked
story** — never alternative stories.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/proposal_packet.schema.json` | Artifact validation |
| Prior artifact | `canon_packet` | The locked story, tone document, annex |
| Optional | `video_analysis_brief` | Visual reference, if the writer pasted one |
| Tools | `web_search` | Comps visual-language research ONLY |

## Mapping the Schema Honestly

`proposal_packet` was designed for concept generation; here its fields carry
treatment semantics. Fill them so they stay truthful:

| Field | Authored-film meaning |
|-------|----------------------|
| `concept_options[].title` | The treatment's name (e.g. "Locked-frame chamber piece", "Handheld vérité") |
| `.hook` | The treatment's visual thesis in one line — what the audience feels in the first 10 seconds |
| `.narrative_structure` | `"story"` — the structure is the writer's; do not vary it |
| `.visual_approach` | The real content: cinematography, palette, motion vocabulary, texture |
| `.target_duration_seconds` | May differ per treatment (a teaser cut vs full runtime) — the one story dimension that is legitimately open unless a lock fixes it |
| `.why_this_works` | Grounded in canon packet fields: cite tone_words, comps ("comp X is tone, comp Y is shape" style entries), annex sequences — not vibes |
| `grounded_in` | canon_packet lock ids and tone fields, in place of research findings |
| `runtime_shape.format` | **Required (v1.1).** `trailer` \| `teaser` \| `short`. Emit it explicitly — the schema `default` is annotation-only and the loader fills it only for upgraded 1.0 packets |
| `cast` | **Required (v1.1).** `{character_ids[], location_ids[]}` — the canon packet entity ids the approved treatment will put on screen. Bounded by `project.yaml.cast_cap`. No defaults, never empty, never a name in place of an id |

## Process

### 1. Research the Look, Never the Story

`web_search` is scoped to the tone document: how the named comps are shot, lit,
cut; visual references for the annex's key sequences; playbook precedents. A
search shaped like "how should this story end" or "what do audiences want from
X" is out of scope for this pipeline — the writer answered those.

### 2. Build 3+ Treatments

Each treatment: visual thesis, shot vocabulary, palette/texture, pacing shape,
music direction (resolved against the bible's sound/music style), renderer
family implication, and cost consequence. Differentiate on axes the canon
leaves open. Every treatment must pass the tone filter: tone_words present,
anti_comps avoided, `must_never_feel_like` respected. One treatment should
derive a custom playbook from the bible's visual style section when one exists.

### 3. Surface Blocking Questions

Any `open_questions[].blocking == true` in the canon packet gets asked at this
gate, before the writer approves a treatment. Present them in the writer's own
`[NEEDS DECISION: …]` convention. Answers the writer gives here are recorded in
`decision_log` (`category: "canon_ruling"`, with `question_id` set to the
canon packet question's `id`) and honored as locks thereafter. This is
runtime-enforced: the checkpoint writer will not complete this stage — or any
later one — while a blocking question has no matching `canon_ruling`. A ruling
only counts when it is real: `question_id` matching the question, the writer's
options in `options_considered`, `selected` naming one of them, and
`user_approved: true`. An unapproved or malformed ruling releases nothing.

### 4. Name the Cast (v1.1, Required)

The `visual_bible` stage that follows renders exactly the entities you list in
`cast` and nothing else, so the cast is a production decision the writer
approves at this gate. Rules:

- Ids, not names: every entry is a `canon_packet` character/location `id`.
- Bounded by `cast_cap` from `project.yaml`. Over cap → choose, with the
  writer, at this gate; do not hand an over-cap cast downstream.
- Choose from the treatment: who and where the trailer actually shows. A
  character who appears only in a line of narration is not cast.
- `runtime_shape.format` states the deliverable shape (`trailer` for the
  60–120s promo). Later stages honor it as a format, not a story change.
- An upgraded 1.0 packet arrives with `migration_status: needs_review` and no
  cast; you re-emit the packet with cast and format and it is re-approved with
  a receipt (`kind: artifact_review`) before any paid stage reads it. The
  gate request's `artifact` is `{artifact_type: "proposal_packet",
  artifact_version, artifact_digest, migration_status: "reviewed"}` where
  `artifact_digest` is `lib.canon_enforcement.artifact_review_digest(packet)`
  (canonical hash minus `migration_status`). Only after that receipt exists do
  you flip `migration_status` to `ok`; enforcement rejects a completed stage
  whose artifact still says `needs_review`, and rejects `ok` without the
  receipt. Any edit after the review changes the digest and needs a new one.

### 5. Standard Production Plan Duties

Delivery promise (explicit motion_required flag), renderer family selected and
locked, per-item cost breakdown, music plan. Same rigor as the cinematic
pipeline — nothing about authored material relaxes cost honesty.

**Composition runtime (HARD RULE).** When both Remotion and HyperFrames are
available (`video_compose.get_info()["render_engines"]`), **present both
runtimes to the writer** before locking `render_runtime` (`remotion`,
`hyperframes`, or `ffmpeg`) in `production_plan.render_runtime` — one
sentence each on what it does best for
THIS treatment, one honest tradeoff, then your recommendation. Never silently
default. Record the full shortlist (both runtimes plus any applicable ffmpeg
option) as `options_considered` in a `render_runtime_selection` decision in
`decision_log`. If only one runtime is installed, say so explicitly and log
the unavailable one as `rejected_because: "runtime not available"`. The
runtime locked here is carried through `edit_decisions.render_runtime`
unchanged — compose may not swap it.

### 6. Gate Presentation

Checkpoint `awaiting_human`: treatments side by side, tone-compliance note per
treatment, blocking questions first, the proposed cast against the cap, the
format, cost table (visual bible + storyboards + video, against
`budget_usd_cap`). END YOUR TURN.

## Common Pitfalls

- A "treatment" that quietly rewrites a beat, softens the ending, or adds a
  scene — that is a story change; rebuild the option.
- Filling `why_this_works` from taste instead of the canon packet's own tone
  fields.
- Letting web research drift from the comps' craft into story-adjacent content.
- Deferring blocking questions to the script stage "to keep momentum" — the
  writer decides at the earliest gate, always.
- Leaving `cast` or `runtime_shape.format` for a default to fill. Nothing
  fills them; the checkpoint is rejected.

## Gate Reminder (Binding)

`human_approval_default: true`. Present and END YOUR TURN.
