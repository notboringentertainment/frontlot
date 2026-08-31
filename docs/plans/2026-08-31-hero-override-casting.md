# Hero override: waive items, don't cast — casting stays with the writer

Status: DRAFT — awaiting Codex review. Do not build from this until approved.
Origin: found live 2026-08-31 on Alyssa Byrne (Bloodless). Twelve hero candidates
generated, all failed `no_occlusion` (her canonical thin-framed glasses). The
budget-spent path wrote an override request bound to ONE candidate — the judge's
fewest-failures pick — so the writer's only choice was accept *that face* or
nothing. Seven candidates failed on glasses alone; the writer never saw them as
choices. The system collapsed a policy decision (accept glasses despite the hero
policy) into a casting decision (this woman is Alyssa).

## The principle

An override answers "is this rule waived for this character's look?" — never
"which face is right?" Casting is the writer's, exercised at the headshot
selection gate over every candidate the waiver unlocks. Two decisions, two
gates, in that order.

## Current mechanism (verified in code, 2026-08-31)

- `scripts/headshot_run.py` generate mode, budget-spent path: `best = min(failed,
  key=len(failing_items))` → `_write_override_request(root, project_id,
  entity_id, best)`. One request, one verdict, one face.
- `lib/sheet_qc/verify.py::overrides_for(project_dir, qc_receipt_id, entity_id)`:
  a signed `qc_override` receipt counts only when both the receipt's and the
  record's `qc_receipt_id` equal the ONE verdict being tested.
- `headshot_run.py::_overridden`: candidate eligible iff
  `failing ⊆ overrides_for(root, verdict["receipt_id"], entity_id)` — so after
  signing, exactly one candidate is eligible; the writer saw
  "presenting 1 of 4 requested".
- `scripts/gate_approve.py::_construct_qc_override`: requires `qc_receipt_id`,
  `item_ids`, and a typed human reason (10+ chars).
- `NON_OVERRIDABLE = {"coverage"}`; verdicts from `provider == "local"` are
  never overridable. Both stay.

## Design

### D1 — Series-scoped item override (new scope, same kind)

Keep the `qc_override` approval kind. Add an optional scope to the record:

```
scope: {series: "hero", entity_id, look_hash}
```

A record WITH scope waives its `item_ids` for every verdict in that hero
series under that exact `look_hash`. A record WITHOUT scope keeps today's
verdict-bound meaning — existing receipts (Ace `override-ace-handler-hero-1`,
Alyssa `override-alyssa-byrne-hero-1`) verify unchanged, no migration.

Binding to `look_hash` is the invalidation story: edit the look ticket →
new hash → the waiver dies with the look it was judged against. This mirrors
how the budget already resets on a look change.

### D2 — The budget-spent override request presents the field, not a pick

`_write_override_request` (generate path only) changes to:

- `item_ids`: the union of failing items across the series' failed verdicts,
  excluding verdicts with `provider == "local"` and any verdict touching
  `NON_OVERRIDABLE` items. Each item annotated with how many candidates it
  blocks and how many become eligible if accepted (alone and cumulatively).
- visual packet: every failed candidate's image path + its failing set, so the
  board and the signer both show the whole field — all twelve faces, not one.
- The signer lets the human accept a SUBSET of `item_ids` (existing
  `_construct_qc_override` already takes item_ids; it gains the subset choice
  and writes the `scope` block). Typed reason unchanged.

### D3 — Eligibility union

`_overridden(root, verdict, entity_id)` becomes: eligible iff

```
failing ⊆ (verdict-bound overrides ∪ series-scoped overrides for (entity, look_hash))
```

with the local-provider and NON_OVERRIDABLE refusals evaluated first, exactly
as today. New helper `series_overrides_for(project_dir, entity_id, look_hash)`
beside `overrides_for` in `lib/sheet_qc/verify.py`; `lib/headshot_verify.py`'s
"no signed qc_override covers" check consults both.

### D4 — Selection presents every unlocked candidate

After a series-scoped override is signed, the re-run's `_passing_verdicts`
picks up every fully-covered candidate. Present ALL eligible candidates at the
headshot gate (the 12-attempt budget is the natural cap; `MAX_CANDIDATES = 4`
keeps governing how many NEW generations a run may request, not how many
existing candidates a gate may present). The writer casts from the full field.

### D5 — Scope boundaries (explicit non-goals)

- **Hero series only.** Sheet QC overrides stay verdict-bound: a sheet is
  derived from an already-cast face, so "one verdict, one override" is correct
  there. No change to `require_qc_pass`.
- **Import path unchanged.** An imported reference is inherently one image;
  its single-candidate continuation (`import_blocked`) is correct. Optional
  later: let an import's failing set be satisfied by an existing series-scoped
  waiver so re-imports don't need a second signature. Not in this change.
- **No policy edits.** The hero policy bundle and its hashes are untouched;
  this changes who chooses among failures, not what counts as failure.

## Trust invariants (unchanged, restated for the reviewer)

Override signing remains TTY-only, human-only, one-use token, typed reason,
HMAC receipts, WAL. Nothing auto-approves; the agent still cannot sign. A
series waiver can never cover `NON_OVERRIDABLE` items or local-provider
verdicts. Backlot remains read-only: it renders the richer packet, signs
nothing.

## Order of work

1. `lib/sheet_qc/verify.py`: `series_overrides_for` + record scope validation
   (schema: approval record gains optional `scope`; absent = verdict-bound).
2. `scripts/gate_approve.py::_construct_qc_override`: subset choice, scope
   block, per-item unlock counts in the prompt.
3. `scripts/headshot_run.py`: request construction (D2), `_overridden` union
   (D3), presentation of all eligible (D4).
4. `lib/headshot_verify.py`: dual-binding check.
5. Backlot: override gate detail renders the candidate field (paths + failing
   sets already in the raw request JSON; board stays read-only).
6. Tests: eligibility union; look-hash invalidation (edit look → waiver dead);
   back-compat (Ace's and Alyssa's verdict-bound receipts still verify);
   local-provider and NON_OVERRIDABLE refusals; flow test 12-fail →
   item waiver → N presented → cast; gate_approve golden prompt; board golden
   for the multi-candidate override packet.

## Proof

On a fixture project: generate a hero series where all attempts fail on one
overridable item. Sign a series-scoped waiver for that item. Re-run presents
every candidate whose only failure was that item; the writer's selection gate
lists them all; picking any one completes hero → sheet → finish. Then edit the
look (new hash) and show the waiver no longer covers anything.
