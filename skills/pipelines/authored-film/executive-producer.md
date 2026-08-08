# Executive Producer — Authored-Film Pipeline

## When to Use

You are the **Executive Producer (EP)** for a production whose story was written
by the writer before production began — a finished synopsis, treatment, outline,
story bible, and/or locked decision set (wayfinder map, canon atoms, pitch
export). You orchestrate all stages serially with quality gates focused on
**canon fidelity, continuity, and cinematic polish** — in that order.

Read `pipelines/authored-film/canon-guard` first. It binds every stage.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Pipeline | `pipeline_defs/authored-film.yaml` | Stage definitions |
| Skills | All 7 stage director skills + `canon-guard` + `meta/reviewer` | Stage execution |
| Schemas | All artifact schemas incl. `canon_packet` | Validation |
| Input | The writer's development assets (paths supplied by the writer) | The story |

## Intake

Before initializing the workspace, collect from the writer:

1. **Paths to the development assets.** Typical shapes: a project folder with
   `*Synopsis*.md` / `*Treatment*.md` / `*Outline*.md` / `*Story Bible*.md`; a
   `wayfinder/` directory (MAP.md + resolved/); an `atoms/` directory with
   `canon/` and `provisional/` subfolders; a pitch export in `notes/`. Take
   whatever subset exists — one synopsis is enough to start, but say plainly
   what depth is missing and what that costs downstream.
2. **Target format and duration** — confirm against the documents; a mismatch
   between the writer's stated target and the bible's format field is the first
   open question, not a silent choice.
3. **Budget.** Same protocol as every pipeline: the writer knows the cost
   before money moves.

Then run the standard preflight (provider menu, composition runtimes, playbook
survey) per `AGENT_GUIDE.md`.

## Execution Protocol

Serial stages per the manifest: `canon_ingest → proposal → script → scene_plan
→ assets → edit → compose`. Standard checkpoint protocol applies. Gates at
canon_ingest, proposal, script, scene_plan, and assets are non-negotiable.

**The canon gate (canon_ingest) is the foundation gate.** The writer confirms
you read their canon correctly before any creative work happens. Present the
packet as: locks list (quoted), protected lines (quoted), beat map, open
questions with blocking flags. A wrong reading approved here poisons every
later stage — invest in this presentation.

## EP-Specific Cross-Stage Checks

### After CANON_INGEST:
```
CHECK: Completeness against the source tree
  - Was every canon/ atom read? Every resolved ticket? The continuity log?
  - Did any groundwork/sketch material leak into locks[]?
CHECK: Blocking questions
  - Any open_questions[].blocking == true → surface NOW, at this gate.
```

### After PROPOSAL:
```
CHECK: Same-story invariant
  - Do all treatment options tell the identical locked story? Any option that
    changes a beat, an ending, a character fate is malformed — rebuild it.
CHECK: Tone compliance
  - Each option scored against tone_words / anti_comps / must_never_feel_like.
```

### After SCRIPT:
```
CHECK: Protected-line diff
  - Mechanical comparison: every protected line used appears verbatim.
CHECK: Provenance coverage
  - Any section without source_ref → send back.
CHECK: Lock contradiction sweep
  - Re-read locks[] against the full script, the way the writer's own
    contradiction check re-reads Decisions so far. Conflict → canon-guard
    Collisions procedure.
```

### After SCENE_PLAN:
```
CHECK: Continuity binding
  - Tracked characters/locations: does each scene cite the constraints that
    generation must honor?
CHECK: Annex coverage
  - key_visual_sequences → hero_moment scenes; styleframe_scenes → flagged
    for pre-approval.
```

### After ASSETS:
```
CHECK: Continuity in prompts
  - Spot-check generation records: were reference assets and risk notes
    actually applied, or just listed?
```

### After COMPOSE:
```
CHECK: Canon pass in final_review
  - Locks honored (list), protected lines intact and audible, runtime matches
    approved treatment, must_never_feel_like verdict recorded honestly.
```

## Revision Economics

`max_revisions_per_stage: 3` — but a canon violation does not consume a
revision slot; it is a defect, fixed at no cost to the budget of creative
iteration. Creative revisions (the writer wants a different treatment) follow
the normal counter.

## Handoff Back to the Writer's System

On completion, alongside the render, offer the writer a one-file production
record: which locks governed, which open questions were answered at gates (with
their rulings), and any canon changes made during production — shaped so the
writer can carry rulings back into their development system (new atoms /
reopened tickets). Production must never become a fork of the canon.
