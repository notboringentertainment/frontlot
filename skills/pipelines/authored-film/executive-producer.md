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
| Pipeline | `pipeline_defs/authored-film@<version>.yaml`, resolved by `lib/pipeline_loader` against the project's pinned version (1.2 adds `look_lock` and `headshots`) | Stage definitions |
| Skills | All 10 stage director skills (incl. `look-lock-director`, `headshots-director`, `visual-bible-director`) + `canon-guard` + `meta/reviewer` | Stage execution |
| Schemas | All artifact schemas incl. `canon_packet`, `look_spec`, `look_packet`, `headshot_packet` | Validation |
| Writer's system | `story-wayfinder` skill ("cast for production" procedure) | Look tickets are written and resolved there, never here |
| Input | The writer's development assets (paths supplied by the writer) | The story |
| Project config | `projects/<slug>/project.yaml` (`schemas/project_config.schema.json`) | `budget_usd_cap`, `wall_time_minutes`, `cast_cap`, `provider_egress` — overrides the manifest defaults |
| Layer 3 | `.agents/skills/seedream`, `.agents/skills/kling-o3-reference` (`.agents/skills/seedance-2-5` for entity-free scenes only) | Provider prompting guidance for the visual bible and shot generation |

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
   before money moves. For this pipeline the budget lives in
   `project.yaml.budget_usd_cap` (manifest `budget_default_usd` is only the
   fallback). Visual-bible plus trailer work runs to hundreds of dollars, not
   the $5 manifest default — say the real number.
4. **Cast cap and egress.** `project.yaml` must carry `cast_cap {characters,
   locations}` and `provider_egress {provider: fal, content_classes: [prompts,
   reference_images]}`. The egress block is the writer's explicit, one-time
   acknowledgment that prompts and generated images leave the machine for
   FAL. Ask for it plainly; never infer it.

Then run the standard preflight (provider menu, composition runtimes, playbook
survey) per `AGENT_GUIDE.md`, plus the authored-film preflight:

```text
PREFLIGHT (before any stage runs)
  - Run lease: take projects/<slug>/.run-lease (O_EXCL; pid, start time,
    hostname, heartbeat). Held by a live process → fail fast and report.
    Never override a live owner with a stale heartbeat.
  - Config approval: sha256(project.yaml) must match a decision_log entry of
    category approval_policy written through the human-gate path. No match
    → present the config as a gate, END TURN. A changed digest is a new gate.
  - Pipeline pin: the project's pipeline version is a signed
    `pipeline_migration` receipt (the unique tip of its supersession chain in
    approvals.jsonl). project.json.pipeline_manifest_version is only a cache
    of that tip. No receipt → present a migration gate for the manifest
    version you intend to run, END TURN. A manifest whose digest differs
    from the pinned tuple is refused by write_checkpoint — never edit the
    cache to make it fit; a downgrade or upgrade is a new human-approved
    migration receipt naming supersedes_receipt_id.
  - Egress: provider_egress present, or headshots / visual_bible cannot start.
  - Cast cap: proposal cast within cast_cap (checked again after PROPOSAL).
  - Budget: projected paid calls for the next stage vs budget_usd_cap minus
    spend to date; every paid call is preceded by a persisted cost
    reservation. A reservation in `submitting` with no provider request id
    (crash between submit and persist) is `indeterminate` → halt, ask the
    writer to reconcile. Never resubmit.
```

## Execution Protocol

Serial stages per manifest 1.2: `canon_ingest → proposal → look_lock →
headshots → visual_bible → script → scene_plan → assets → edit → compose`.
Standard checkpoint protocol applies. Gates at canon_ingest, proposal,
look_lock, headshots, visual_bible, script, scene_plan, and assets are
non-negotiable. Inside `look_lock`, `headshots`, `visual_bible` and the
storyboard sub-step of `assets`, every approval is a signed receipt minted by
the gate handler (`record_human_approval` with a one-use gate token) — no
director, including you, can record one. `headshots` uses the handler's
selection mode: the writer picks a candidate in the terminal and the handler
constructs the record; there is no agent-authored record to approve.
`look_packet` is required by `headshots`; `look_packet` + `headshot_packet`
by `visual_bible`; `visual_bible` by every later stage. Per-entity flow (D18):
`headshots` and `visual_bible` accept those inputs from an `in_progress` /
`awaiting_human` predecessor (a partial packet) for the entities present, so
one character's look → face → sheet runs ahead of the rest of the cast;
`script` onward still needs `look_lock`, `headshots` and `visual_bible`
`completed` — trailer assembly needs everyone. Every checkpoint
carries `pipeline {name, version, manifest_digest}`, filled by
`write_checkpoint` from the loaded manifest.

Two stages contain no creative work by any director: `look_lock` carries the
writer's wayfinder decision into a receipt (the director may draft a ticket
Question and never an Answer), and `headshots` renders that decision and lets
the writer choose a face. If you find either stage "moving fast" without a
wayfinder session or a terminal selection in the middle, something is being
invented.

**Invalidation is derived from the ledger, not from a file.** A `look_lock`
`retire` receipt invalidates `headshots` onward; a superseding `headshot`
receipt invalidates `visual_bible` onward; either one marks every completed
downstream checkpoint written after it as not completed. `get_latest_checkpoint`,
prerequisite enforcement, and delivery recompute this on every call;
`invalidations.jsonl` is a rebuildable cache verified fail-closed. When a
writer retires a look or replaces a hero, tell them which checkpoints go
stale before they do it, then re-run from the first invalidated stage. Never
resume past an invalidated checkpoint and never "re-approve" it in place —
each stage is re-presented and re-approved as a new checkpoint.

**The canon gate (canon_ingest) is the foundation gate.** The writer confirms
you read their canon correctly before any creative work happens. Present the
packet as: locks list (quoted), protected lines (quoted), beat map, open
questions with blocking flags. A wrong reading approved here poisons every
later stage — invest in this presentation.

## EP-Specific Cross-Stage Checks

### After CANON_INGEST:
```text
CHECK: Completeness against the source tree
  - Was every canon/ atom read? Every resolved ticket? The continuity log?
  - Did any groundwork/sketch material leak into locks[]?
CHECK: Blocking questions
  - Any open_questions[].blocking == true → surface NOW, at this gate.
```

### After PROPOSAL:
```text
CHECK: Same-story invariant
  - Do all treatment options tell the identical locked story? Any option that
    changes a beat, an ending, a character fate is malformed — rebuild it.
CHECK: Tone compliance
  - Each option scored against tone_words / anti_comps / must_never_feel_like.
CHECK: Required v1.1 fields
  - runtime_shape.format (trailer|teaser|short) and cast {character_ids,
    location_ids} are present and explicit — no defaults, no empty cast.
  - Every cast id resolves to a canon_packet entity id; counts within cast_cap.
```

### After LOOK_LOCK:
```text
CHECK: One ratified look per cast entity
  - look_packet.looks[] covers every id in proposal.cast (characters and
    locations); each look_hash recomputes from its block; each receipt_id is
    a signed look_lock receipt at the ledger tip for that (entity_kind,
    entity_id) key. No shape_only, no minor.
CHECK: Authority
  - Every source_ticket_ref is a resolved wayfinder ticket, type grill, mode
    hitl, with exactly one Look spec block. The director drafted at most a
    Question — spot-check a ticket: the Answer is in the writer's voice, not
    the pipeline's.
CHECK: Spoilers and format
  - Trailer/teaser: no cast entity's active look is spoiler: true.
CHECK: Reference imports
  - Every import has a reference_import receipt binding origin_class and
    normalized_pixel_hash. imported_synthetic objects are under objects/
    with a generator_kind: imported receipt; casting_inspiration files are
    under canon/visual/casting-inspiration/ and appear NOWHERE else — not in
    a prompt, a summary, a manifest, or a decision-log entry.
```

### After HEADSHOTS:
```text
CHECK: One approved hero per cast character
  - headshot_packet.state == approved; one entry per proposal.cast.character_ids
    (locations absent); each approval_receipt_id is the unique tip of that
    character's headshot chain; look_ref.look_hash is the current active look.
CHECK: The handler chose, not the director
  - Each headshot receipt's record carries candidates_checkpoint_digest and
    the chosen asset_id; the asset is one of the pending packet's candidates.
    An imported_synthetic hero cites its import receipt and matching
    normalized_pixel_hash.
CHECK: Prompts from the recipe
  - Every generated candidate's receipt seals look_refs and a prompt_recipe
    whose rendered_sha256 matches the prompt; no candidate came from a
    hand-written prompt. Reject-all notes are in the decision log verbatim.
```

### After VISUAL_BIBLE:
```text
CHECK: Coverage and receipts
  - Every cast entity has status approved with an approval_receipt_id that
    resolves in approvals.jsonl, signature-valid, ledger-backed.
  - Every ImageRef has a generation receipt whose output_sha256 == asset_id
    (synthetic-only proof: root is a pipeline generation or an attested
    imported_synthetic import). Palette present. Poster approved.
CHECK: Looks and heroes bound
  - Every entity's look_ref.look_hash equals the active look for its key;
    every character sheet receipt carries a headshot_ref matching the current
    hero; no hero was generated in this stage; sheet_revision present.
CHECK: Passports
  - prompt_recipe (look_hash, builder_version, fields_used, rendered_sha256)
    and wardrobe_negative present per entity — no approved_prompt_block
    string, no hand-written identity text.
CHECK: Lineage
  - No tainted (casting_inspiration) hash in any lineage; every packed
    reference has a receipted lineage to a root.
CHECK: Typography
  - Title card provenance is generator_kind: local. No model-generated text.
```

### After SCRIPT:
```text
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
```text
CHECK: Continuity binding
  - Tracked characters/locations: does each scene cite the constraints that
    generation must honor?
  - character_refs / location_ref resolve to approved visual_bible entities;
    empty refs only on scenes marked entity_free: true.
CHECK: Model policy (D4)
  - Every scene carries model_endpoint (default Kling o3 pro reference-to-video); any
    model_override has a reason. A Seedance 2.5 override is valid only on an
    entity_free scene (Seedance rejects human-face references on FAL). Shots carry no model field.
CHECK: Trailer format
  - 12–20 shots total, one action per shot, shot_id on every shot.
CHECK: Annex coverage
  - key_visual_sequences → hero_moment scenes; styleframe_scenes → flagged
    for pre-approval.
```

### After ASSETS:
```text
CHECK: Continuity in prompts
  - Spot-check generation records: were reference assets and risk notes
    actually applied, or just listed? Compare references_applied against the
    request payload recorded with the generation receipt.
  - Every governed call's receipt seals look_refs for every entity in the
    shot (or names a shot_id whose approved scene record is entity_free);
    identity paragraphs were rebuilt from prompt_recipe with a matching
    rendered_sha256 — no pasted or reworded text.
CHECK: Storyboard batch
  - One storyboard_frame per shot_id, approved as a batch (receipt kind
    storyboard_batch) BEFORE the first video call.
CHECK: Single strategy
  - All selected takes in a scene use the scene's model_endpoint; every
    shot_visual carries shot_id, take_id, usage_status, model_endpoint and a
    generation receipt.
```

### After COMPOSE:
```text
CHECK: Canon pass in final_review
  - Locks honored (list), protected lines intact and audible, runtime matches
    approved treatment, must_never_feel_like verdict recorded honestly.
CHECK: Poster usage
  - poster_final appears only as a title/end card; render_report.outputs is
    video-only.
```

## Revision Economics

`max_revisions_per_stage: 3` — but a canon violation does not consume a
revision slot; it is a defect, fixed at no cost to the budget of creative
iteration. Creative revisions (the writer wants a different treatment) follow
the normal counter.

## Money and Locks Are Not Creative Decisions

Raising `budget_usd_cap`, widening `cast_cap`, or authorizing egress is a new
human approval of a new config digest — never something you adjust to finish a
run. Supersession of any approved sheet requires a `canon_ruling` with
`question_id: visual:<entity-id>` from the writer; supersession of a look
starts in the writer's wayfinder ticket and lands as a signed `retire` +
`activate` pair; supersession of a hero is a new selection in the gate
handler. Changing the pinned pipeline version is a new `pipeline_migration`
receipt, never a cache edit. Release the run lease when the run ends or
halts.

## Handoff Back to the Writer's System

On completion, alongside the render and the poster, offer the writer a
one-file production record: which locks governed, which looks were ratified
(entity key, `look_hash`, ticket path, receipt id), which open questions were
answered at gates (with their rulings), and any canon changes made during
production — shaped so the writer can carry rulings back into their
development system (new atoms / reopened tickets). Production must never
become a fork of the canon; a look that changed during production is a
reopened wayfinder ticket, not a pipeline-side edit.
