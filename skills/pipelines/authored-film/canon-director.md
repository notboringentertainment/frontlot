# Canon Director — Authored-Film Pipeline

## When To Use

First stage (`canon_ingest`). You read the writer's development assets and
produce the `canon_packet` — the authoritative story source for the whole run.
This stage replaces web research: the ground truth for an authored film is the
writer's canon, not the content landscape.

Read `pipelines/authored-film/canon-guard` first.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/canon_packet.schema.json` | Artifact validation |
| Input | Writer-supplied paths to development assets | Source material |

## Source Formats You Will Meet

The writer's development system produces these shapes. Recognize them; do not
demand any one of them.

| Shape | Recognize by | Mine for |
|-------|--------------|----------|
| Synopsis | Logline + compressed prose paragraphs | Story spine, ending, stakes |
| Treatment | Cinematic prose, may end with an "AI Production Implications" section | Full story flow, tone in action, the annex |
| Outline | Beat/sequence/scene tables or lists | `structure.beats[]` — highest-precedence structure source |
| Story bible | Sections: tone and style, world rules, character index, character entries (want/need/wound/behavioral anchors/never write them as), locations, continuity log, AI production annex | characters[], locations[], world_rules[], tone, locks from "Locked?" continuity rows |
| Wayfinder map | `wayfinder/MAP.md` — Destination, Notes (voice lock), Decisions so far | Every "Decisions so far" line is a lock; Notes feed tone |
| Wayfinder resolved tickets | `wayfinder/resolved/*.md` — Question + Answer | The full reasoning behind each lock; quote decisions from here |
| Wayfinder open tickets | `wayfinder/tickets/*.md` | In-scope unresolved ones become open_questions[] |
| Canon atoms | Files under `atoms/canon/` — title, `type: atom`, `status: canon`, `evidence:` hashes, `## Decision`, `## Context` | Highest-authority locks; carry `evidence` hashes into evidence_ref |
| Provisional atoms | `atoms/provisional/` | open_questions or low-authority notes — NEVER locks |
| Pitch export | Clean external document in `notes/`, with a departures declaration | Premise-level locks; departures are explicit decisions |
| Sketches / instrument outputs | `assets/` files, sketches headed "DISPOSABLE, NOT CANON" | authority: groundwork. Never mine for story truth |

## Process

### 1. Inventory Before Reading

List every file in the supplied paths. Classify each into the table above and
record it in `source_documents[]` with its authority level and a sha256 hash.
Files you decline (old drafts superseded, sketches) go in
`provenance.documents_declined` with a reason — the writer sees what you chose
not to read.

### 2. Locks First

Extract locks in authority order: canon atoms (quote the `## Decision` body,
carry evidence hashes), then wayfinder Decisions so far + resolved ticket
answers, then continuity log rows marked locked, then pitch-export departures.
One entry per decision; `decision` in the writer's words — quoted or faithfully
compressed, never reinterpreted. Assign `scope` (premise / ending / character /
tone / structure / dialogue / world_rule).

Dialogue the writer has protected (atoms or bible entries that name exact
lines) goes to `protected_lines[]` character-for-character.

### 3. Structure From the Sharpest Source

Precedence: scene-by-scene outline > sequence outline > beat sheet > treatment
prose > synopsis paragraphs. Record which won in `structure.structure_source`.
Each beat: writer's summary, the turn, characters, location, `lock_refs` to
any lock that constrains it.

### 4. People, Places, Rules, Tone

Fill characters[] / locations[] / world_rules[] / tone from the bible (or from
the treatment where no bible exists). Copy `never_write_as` and behavioral
anchors exactly — these become hard review filters downstream. Aggregate every
AI-annex section into `ai_production_notes`.

### 5. Gaps Are Questions, Not Blanks To Fill

Every `[NEEDS DECISION: …]` marker, every in-scope open ticket, every gap you
hit (missing ending, contradictory character fact, undefined rule the script
will need) → `open_questions[]`. Set `blocking: true` when production cannot
proceed honestly without the answer. **Filling a gap from your own imagination
is the one unforgivable failure of this stage.**

Give every question a stable `id` (`q-001`, `q-002`, …) and a `status`
(`open`, or `resolved` only when the source material itself answers it).
These are runtime-enforced: the checkpoint writer refuses to complete any
post-ingest stage while a `blocking: true` question is `open` and no
`canon_ruling` decision carries its `question_id`.

### 6. Contradiction Sweep

Before writing the checkpoint, re-read locks[] as a set. Two locks in genuine
tension → open_questions with `blocking: true`, both locks named. The writer's
own development system does this check before every lock; production meets the
same bar.

### 7. Gate Presentation

Write the checkpoint `awaiting_human`. Present: locks (quoted, grouped by
scope), protected lines, the beat map, tone summary, open questions with
blocking flags, and the declined-documents list. Ask the writer to confirm the
reading or correct it. END YOUR TURN.

## Common Pitfalls

- Promoting a provisional atom or groundwork file to a lock.
- Compressing a lock until it means something slightly different — quote when
  in doubt.
- Reading only the bible and skipping the resolved tickets: tickets carry the
  reasoning that disambiguates thin bible lines.
- Treating an empty template field ("**Secret**:") as content.

## Gate Reminder (Binding)

`human_approval_default: true`. The checkpoint writer will refuse a completed
status without `human_approved=True`. Do not perform proposal work in the same
turn as the gate presentation.
