# Hero override: waive items, don't cast — casting stays with the writer

Status: BUILT and APPROVED. Plan: 5 Codex review rounds (49 findings, all
accepted). Build: Claude, commits b132783 through fix round 5; 5 fresh-session
Codex inspection rounds (43 findings: fixed or rejected-with-logged-reason),
final verdict APPROVED. Suite 2133 green. Adoption: a project moves to
authored-film@1.5 only through its signed pipeline_migration pin gate —
Bloodless stays on 1.4 until Ben signs.
Origin: found live 2026-08-31 on Alyssa Byrne (Bloodless). Twelve hero candidates
generated, all failed `no_occlusion` (her canonical thin-framed glasses). The
budget-spent path wrote an override request bound to ONE candidate — the judge's
fewest-failures pick — so the writer's only choice was accept *that face* or
nothing. Seven candidates failed on glasses alone; the writer never saw them as
choices. The system collapsed a policy decision (accept glasses despite the hero
policy) into a casting decision (this woman is Alyssa).

## The principle

An override answers "are these failed items acceptable for these already-judged
candidates?" — never "which face is right?" Casting is the writer's, exercised
at the headshot selection gate over every candidate the waiver unlocks. Two
decisions, two gates, in that order.

## Design lineage

Revision 1 proposed a persistent series-scoped waiver with future authority;
Codex round 1 (finding #15) showed the field is already finite at budget
exhaustion, so revision 2 replaced it with one immutable field-bound batch.
Round 2 found the batch design under-specified in six places (self-referential
state, scan-based authority discovery, coverage unions, missing version
discriminator, 1.4 runtime bleed, and the one-passing-candidate collapse).
Revision 3 pins those down. Core shape unchanged: **no future-verdict authority
exists anywhere; every signature binds to evidence the human was shown.**

## When the gate fires (round 2 #8)

The batch-waiver gate is offered whenever the hero budget is exhausted AND the
shared field validator (below) finds at least one failed candidate that a
waiver could unlock — **including when some candidates already pass**. Flow:
budget spent → if the field is enlargeable, write the batch override request
and stop; the writer may sign it (field grows) or decline it (typed note, per
existing decline flow) → the selection gate then presents passing ∪ unlocked.
A writer who declines is choosing the judge-approved faces only — explicitly,
on the record, not by construction of the request.

If the budget is exhausted and NO failed candidate is unlockable (every failure
is local-provider, `NON_OVERRIDABLE`, imported/grandfather, or rejected
history), no override request is written: the run reports a terminal state
naming why each failed row is excluded, with the real options — repair the
judge chain or edit the look (round 2 #12).

## Current mechanism (verified in code, 2026-08-31)

- `scripts/headshot_run.py` budget-spent path (only when `passing` is empty):
  `best = min(failed, key=len(failing_items))` → one request, one verdict, one
  face.
- `lib/sheet_qc/verify.py::overrides_for`: override counts only for the ONE
  verdict both envelope and record name; `lib/receipts.py` enforces equality
  and validates item_ids against that verdict.
- `scripts/gate_approve.py::_construct_qc_override`: `qc_receipt_id`,
  `item_ids`, typed reason (10+ chars).
- Presentation caps at 4: `headshot_packet` `maxItems: 4`, signer rejects >4,
  authored-film@1.4 promises "up to 4". Hero-QC routing recognizes exact
  manifest versions (`canon_enforcement.py` ~1491).
- Request states are pending/done/declined only (`run_common.request_paths`);
  the one-use token is consumed before pre-commit checks run (`receipts.py`
  ~205).
- `NON_OVERRIDABLE = {"coverage"}`; local-provider verdicts never overridable;
  `qc.max_hero_attempts` is signed config, 1–24.

## Design

### D1 — Batch override record (record_version "1.1", same `qc_override` kind)

```
record 1.1: {
  record_version: "1.1",                     # explicit discriminator (r2 #5)
  request_id,                                # binds record to its gate request (r3 #4)
  batch: [ {qc_receipt_id, asset_id, accepted_item_ids}, ... ],
             # unique qc_receipt_ids and asset_ids, canonical sort order,
             # every accepted_item_ids nonempty (r2 #5)
  field_manifest_sha256,                     # digest of the reviewed field (D2)
  unlocked_asset_ids: [...],                 # the exact result shown pre-sign (r2 #13);
                                             # MUST be nonempty — the signer refuses an
                                             # approval that unlocks nothing (r3 #5)
  look_hash, look_receipt_id,                # active look, both bindings
  config_approval_receipt_id, config_sha256, # the real identifiers (r2 #11)
  budget_cap, attempts_spent,
  expected_active_headshot_receipt_id,       # nullable; non-null on --replace fields (r2 #9)
  reason
}
```

**Additional batches over the residual field** (r3 #5 — replaces revision 3's
"superseding batch" language, which is gone): after a batch is signed, a later
batch request may be raised ONLY over the residual field — failed candidates no
prior batch unlocked. Each candidate is unlocked by exactly one batch row, that
row alone must cover all its failing items, and each batch's manifest is
computed over its own residual field. Nothing composes across receipts, and no
batch supersedes another; earlier batches remain the sole authority for their
candidates.

Envelope 1.1 carries `field_manifest_sha256` in place of the single
`qc_receipt_id`. `lib/receipts.py` gains the 1.1 branch with a strict field
allowlist; hybrid or mixed 1.0/1.1 shapes are rejected; **the 1.0 branch is
preserved byte-for-byte** — existing receipts (Ace, Alyssa) verify unchanged.
Import and grandfather flows construct 1.0 records only; the batch path is
reachable only from the generated-series budget-spent branch, and the field
validator (D2) excludes their rows structurally, not by assertion (r2 #7).

### D2 — The field manifest: the signer proves what the human reviewed

At signing time `gate_approve` **reconstructs** the field from verified QC rows
(never from the request JSON), renders every candidate image with its failing
items, and computes the canonical manifest: sorted `{qc_receipt_id, asset_id,
failing_items}` tuples, canonical JSON, sha256. A request whose field disagrees
with reconstruction is refused as `packet_error`.

**Shared field validator** (one function, used identically at request
construction, signer reconstruction, and pre-commit — r1 #11): a row enters the
field iff its verdict is committed, not voided, judged under the currently
verified policy/config chain; it cites a resolvable **model-generated**
generation receipt (the `IMPORTED_SENTINEL` is rejected; `series_key.
grandfather` must be absent or false — r2 #7); pixels are present and re-hash
correctly; `provider != "local"`; every failing item is overridable
(`NON_OVERRIDABLE` excluded); and the asset is not in rejected history
(`metadata.rejected_candidates`, reject-all included).

**Subset preview binds the outcome** (r2 #13): when the human chooses accepted
items, the signer renders, before confirmation, the per-candidate coverage and
the exact list and count of assets the subset unlocks. That list is sealed in
the record as `unlocked_asset_ids`; pre-commit recomputes it and refuses on any
difference.

**Legacy coverage folds in** (r2 #3): if a 1.0 verdict-bound override already
covers items for a field verdict, the signer displays that inherited coverage
and writes it INTO the batch row. After a batch is signed, the batch row is the
complete and only authority for its candidate.

### D3 — Eligibility: cited authority, never scanned (r2 #2, #3; r3 #1, #2)

Authority is carried, not discovered. Every batch-covered candidate row — in
`run_state`, in the presented packet, and in the final headshot record —
carries the full citation: `qc_override_receipt_id`,
`qc_override_record_sha256`, and `field_manifest_sha256` (the record hash is
what the existing `exact_approval` API requires — r3 #2; no new lookup API).

The resolver enforces the whole binding (r3 #1):
`batch_row_for(project_dir, override_receipt_id, override_record_sha256,
qc_receipt_id, asset_id, field_manifest_sha256, entity_id)` resolves the
receipt via `exact_approval`, checks `record_version == "1.1"`, checks the
record's `field_manifest_sha256` equals the cited one, finds the row whose
`qc_receipt_id` AND `asset_id` both match, requires
`asset_id ∈ unlocked_asset_ids`, and tests `failing ⊆ accepted_item_ids` of
that row alone. Any mismatch = not covered. No unions across receipts: a
candidate is eligible under exactly one authority — its cited batch row, or
(legacy path) a 1.0 override bound to its verdict.

`lib/headshot_verify.py` accepts either the cited 1.1 row or a 1.0
verdict-bound receipt. Sheet QC (`require_qc_pass`) is untouched.

**Casting finality wins over field reuse** (r4 #3, superseding revision 4's
whole-field `field_citations`): selection permanently rejects every unchosen
candidate — that is existing, correct semantics, and this plan keeps it.
Authority is retained only for the SELECTED candidate, sealed in its headshot
record; batch receipts for unselected candidates stand as independent audit
records with no forward power. Replace and retire get exact semantics (r5 #2): a
`--replace` run excludes the currently active asset from any field or
presentation; retirement adds the retired asset to durable rejected history;
and since the batch path only exists at budget exhaustion, both report terminal
no-capacity unless a signed cap increase or a look change creates new budget —
they never "generate anew" out of thin air, and the old face can never be
re-presented. A human-approved "reconsider rejected candidates" transition is
explicitly OUT of scope. Wherever per-candidate citations are stored transiently (the packet),
they are keyed `entity_id → asset_id` with the bound look_hash in the entry,
since content-addressed assets can collide across characters (r4 #6).

### D4 — Request lifecycle: no stale authority, no recursive digests

The budget-spent path writes the checkpoint FIRST (run_state mode
`override_pending` holding only request_id, revision, and the config/field
identifiers — never a digest of its own checkpoint, honoring D20's rule), then
computes the checkpoint digest and writes it into the gate request's
`source_checkpoint_digest` (r2 #1) — the same order the import flow uses.

Signer pre-commit re-checks against live state: active look unchanged
(`look_hash` AND `look_receipt_id`); budget still exhausted under the currently
verified config (resolved by `config_approval_receipt_id` + `config_sha256`;
a raised cap refuses the request — generation resumes; a lowered cap
re-filters the field); checkpoint digest unchanged; and the active headshot
receipt equals `expected_active_headshot_receipt_id` (null = first casting) —
catching replace/retire/selection races (r2 #9).

**Abandonment is a first-class state** (r2 #10; r3 #3; r4 #8): `abandoned/`
joins pending/done/declined in `run_common.request_paths` and every consumer.
Pre-commit failure after token consumption abandons in a MANDATED order under
the approval lock: move pending→abandoned FIRST, then clear the
`override_pending` checkpoint state — so a crash between the two leaves an
abandoned request plus stale state (recoverable: the next run sees its request
in `abandoned/` and clears state), never an orphan pending request.
Fault-injection tests before and after each operation. Recovery distinguishes
the two windows per D20's republish rule: a request that is **missing** (the
checkpoint-before-request crash window) is revalidated and republished under
the SAME id; only a request positively found in `abandoned/` gets a NEW id
with rebuilt run_state — ids are never reused after abandonment and never
changed on republish.

**Successful-signature crash window** (r3 #4): record 1.1 binds `request_id`,
so a crash after receipt commit but before the done-move leaves a pending
request whose unique verified receipt is recoverable by that binding; the next
run (or signer invocation) attaches it and completes the done-move instead of
refusing. Crash-injection test between receipt commit and `_complete_done`.

**Declining a batch is a recorded decision** (r3 #8): the batch decline path
requires a typed note (10+ chars, refused when empty) persisted to
`declined_note` and surfaced in the decision log, so "the writer chose the
judge-approved faces only" is auditable, not inferred.

### D5 — Continuation

Every exhausted-budget re-run reconstructs the field from the immutable attempt
ledger through the shared validator — not from that invocation's observations.
A zero-unlock batch cannot be signed (the signer refuses it, D1), so a signed
batch always enlarges the field; if live state has moved since signing, the
citations simply fail re-verification and the run reports what changed. When
uncovered unlockable candidates remain, the run offers a new batch request over
the residual field (D1); when none remain, terminal state: edit the look (new
budget) or accept the field as presented. The empty-field case never writes a
request at all (see "When the gate fires").

### D6 — Presentation: versioned contracts, version-gated behavior

- `headshot_packet` 1.2: `candidates.maxItems` = 24 (the config schema's
  `max_hero_attempts` ceiling); runtime bound = the project's verified cap.
  Candidate entries gain nullable `qc_override_receipt_id`,
  `qc_override_record_sha256`, `field_manifest_sha256`, AND `legacy_citations`
  (r5 #3) — the full citation set under the same conditional-shape rules as
  headshot record 1.2 (r4 #1) — preserved verbatim through display, selection,
  pre-commit, and into the headshot record (r2 #4); the record copies the
  exact displayed `legacy_citations` list, never rediscovering it. The legacy
  list is a deterministic sufficient set chosen and sealed at packet creation
  (oldest receipts first until the verdict's failing items are covered);
  downstream verification inspects ONLY that cited set, so later duplicate
  1.0 overrides can neither change nor invalidate it (r5 #4). 1.0/1.1 packets
  verify unchanged. Packet production and headshot-selection pre-commit BOTH
  re-check that the budget is still exhausted under the config/cap snapshot
  bound into selection state; a raised cap stops presentation and resumes
  generation instead of showing a stale field (r4 #4).
- authored-film@1.5: headshots stage promises "up to the hero-attempt cap";
  1.4 + its director byte-frozen per the D20 golden pattern.
- **Version gating is behavioral, not aspirational** (r2 #6): every batch and
  packet-1.2 branch in `headshot_run` and `gate_approve` is gated on an exact
  authored-film ≥1.5 capability predicate resolved from the project's verified
  pin. Behavioral tests prove a 1.4-pinned project still emits 1.0 override
  requests and ≤4 candidates. Bloodless adopts 1.5 only through the existing
  pipeline_migration pin gate — Ben signs or nothing changes.
- Signer selection UI pages long fields; Backlot gate detail renders the full
  field (image grid + per-candidate failing items + per-candidate authority
  citation) for both gates; board stays read-only.

### D7 — Selection seals its authority: headshot record 1.2, explicit (r3 #7)

Headshot record **1.2** (1.0 and 1.1 verify unchanged; strict allowlist; the
version is named, not implied). New fields, with conditional legality per flow:

| flow | legacy_citations | qc_override_receipt_id | qc_override_record_sha256 | field_manifest_sha256 |
|---|---|---|---|---|
| clean pass (batch preceded or not) | [] | null | null | null |
| legacy 1.0-override candidate | canonical list of {receipt_id, record_sha256} — every 1.0 receipt whose items combine to cover the verdict (today's union semantics, represented exactly — r4 #5) | null | null | null |
| batch-unlocked candidate | [] | set (1.1 receipt) | set | set |
| imported selection under ≥1.5 | [] | null | null | null |
| grandfather | emits NO record 1.2 at all — the attestation is its record; the existing headshot record is untouched (r4 #7) |  |  |  |

Any other combination is rejected. A clean selection seals nothing about
preceding batches — those receipts are independent audit records (r4 #2,
simpler branch chosen). This replaces the generic "accepted by qc_override"
audit phrase with the concrete citation the packet carried from gate to gate
(r2 #4, r1 #14).

## Trust invariants (unchanged, restated)

Override signing remains TTY-only, human-only, one-use token, typed reason,
HMAC receipts, WAL, transactional pre-commit. Nothing auto-approves; the agent
still cannot sign. No future-verdict authority, no scan-discovered authority,
no digest-of-self. Import and grandfather trust paths are byte-identical to
D20 and structurally excluded from batch fields. Backlot renders, never signs.

## Order of work

1. `lib/run_common.py`: `abandoned` request state + recovery contract.
2. `lib/receipts.py`: `qc_override` record/envelope 1.1 (strict allowlist,
   discriminator, hybrid rejection; 1.0 preserved exactly) + canonical
   field-manifest helper + `exact_approval` resolution for batch citations.
3. `lib/sheet_qc/verify.py`: shared field validator + `batch_row_for`;
   `lib/headshot_verify.py`: cited-authority check.
4. `scripts/headshot_run.py`: fire condition (enlargeable field, even with
   passing candidates), checkpoint-then-request write order, `override_pending`
   run_state, continuation, version-gated branches.
5. `scripts/gate_approve.py`: batch constructor — reconstruction, rendering,
   subset preview with sealed `unlocked_asset_ids`, legacy fold-in, pre-commit
   (look both-bindings, budget/config, checkpoint digest, expected headshot
   tip), abandonment on pre-commit failure.
6. Contracts: `headshot_packet` 1.2 (+ authority fields), authored-film@1.5,
   1.4 byte-freeze, headshot record version for D7, version-keyed signer cap.
7. Backlot gate-detail rendering for both gates.
8. Tests: everything in the round-1 list PLUS: 1.4-pin behavioral regression
   (1.0 requests, ≤4 candidates); one-passing-candidate fires the gate; decline
   requires and persists a typed note (empty refused; decision log preserved);
   no-unlockable-field terminal (no request written); two-batch non-composition
   (A-receipt + B-receipt cannot unlock an A+B candidate; residual-field batch
   covers its candidate fully or not at all); zero-unlock approval refused by
   the signer; subset-preview seal (pre-commit refuses when recomputed unlocked
   set differs); resolver full-binding refusals (wrong asset, wrong manifest,
   wrong record hash, asset ∉ unlocked_asset_ids); missing-request republish
   under the SAME id vs abandoned-request NEW id; crash injection between
   receipt commit and `_complete_done` (recovery attaches by request_id
   binding); headshot record 1.2 conditional-shape matrix (every illegal
   combination rejected); unchosen candidates remain rejected after selection
   and cannot be resurrected by replace or retire (r5 #1); `--replace`
   excludes the active asset, retire adds it to durable rejected history, and
   both hit terminal no-capacity without a signed cap increase or new look
   (r5 #2); cap-raise immediately after batch receipt commit AND immediately
   before headshot-selection pre-commit both force generation to resume
   (r5 #5); `legacy_citations` sealed at packet creation and immune to later
   duplicate 1.0 receipts (r5 #4); replace-race refusal via expected headshot
   tip; imported and grandfather rows structurally excluded from fields.

## Proof

Fixture project, authored-film@1.5, hero series where attempts fail on one
overridable item and ONE attempt passes: the batch gate fires anyway; sign a
batch accepting the item with the previewed unlocked list; re-run presents
passing ∪ unlocked (>4, packet 1.2); writer casts one; headshot record cites
the batch receipt + manifest digest; sheet → finish. Then: (a) edit the look →
pending request abandoned at pre-commit, run_state recovered, new request id on
re-run; (b) raise `max_hero_attempts` via signed config → request refused,
generation resumes; (c) a 1.4-pinned fixture emits a 1.0 single-verdict request
and ≤4 candidates; (d) Ace's and Alyssa's live 1.0 receipts still verify on
Bloodless.
