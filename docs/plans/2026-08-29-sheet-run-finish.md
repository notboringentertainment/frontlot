# sheet_run finish step (D20 follow-up F0)

Revision 6 — 2026-08-29 (rounds 1–5: 12 + 6 + 6 + 5 + 4 findings; all accepted; MAX_ROUNDS reached, Codex's round-5 verdict: core finish path ready, remaining items were crash-consistency in the recovery transitions — fixed here).
Branch `authored-film`.

## Problem

`scripts/sheet_run.py` stops after publishing the `sheet` gate request. After the
human signs, the visual_bible entry stays `status: "draft"` and is flipped to
`approved` + `approval_receipt_id` by hand (skill text in
`skills/pipelines/authored-film/visual-bible-director.md:93`). Done manually for Ace
and Sebastian. `headshot_run.py` has the right shape (`_resume` → `_finish_select`,
`headshot_run.py:709-893`); sheet_run gets the same.

Verified gaps this plan also closes (Codex round 1):
- sheet request is published with `source_checkpoint_digest: None` (`sheet_run.py:344`),
  and the gate never validates that field against the checkpoint for any kind
  (`gate_approve.py:207-233`, `:568-590`, `:1202`);
- the sheet pre-commit check re-checks a captured `entry`, not the entry on disk
  (`gate_approve.py:589`; headshot does reload, `:988-991`);
- approvals.jsonl rows carry no request id, and `_decide` moves the done request
  without recording the receipt id it minted (`receipts.py:193-206`,
  `gate_approve.py:1212`), so "which receipt belongs to this request" is not
  reconstructible today; a crash between commit and move can double-sign one revision;
- a declined sheet is invisible to the next run: `_acceptable_verdict`
  (`sheet_run.py:299`) reuses the passing assets the human just rejected;
- `sheet_run --resume` is dead (accepted, never read); requests are written with
  plain `write_text`; no `resolve_project_root` / `request_id_for` / `require_entity_id`.

## Non-goals

- No change to sheet generation, roles, judge, or caps. A `Blocked` run (cap
  exhausted) stores no run state and stays blocked until an override or series
  change, as today (`tests/lib/test_sheet_run.py:101-110`).
- No migration of Ace's/Sebastian's approved entries (receipts carry
  `source_checkpoint_digest: None`; they are approved; finish is never invoked).
- No schema version bumps: run state and rejection memory live in checkpoint
  `metadata`; `sheet` request already has `source_checkpoint_digest`; done-request
  gains one field (`approval_receipt_id`) — done files are unsigned markers.

## Design

### D1 — Run state in the visual_bible checkpoint; one sub-gate at a time

`sheet_run` gets a `_write(root, vb, *, status, run_state=None, prev_entry=None,
rejected=None)` helper mirroring `headshot_run._write` (`:132-166`): merges
`metadata.run_state`, `metadata.sheet_prev_entry`, `metadata.rejected_sheets`,
`metadata.sheet_revisions`, calls `write_checkpoint(..., human_approval_required=True)`
(the manifest forces True anyway, `checkpoint.py:780-782`), returns
`checkpoint_digest(path)`.

On publish: `metadata.run_state[entity] = {"mode": "sheet", "request_id",
"revision", "expected_kind": "sheet"}`; `metadata.sheet_prev_entry[entity]` = the
entry the draft replaced (or null) so decline can restore it;
`metadata.sheet_revisions[entity]` = max revision ever issued (monotonic, survives
decline).

Order: write checkpoint (draft entry + run_state) → digest → publish request with
`source_checkpoint_digest = digest`, via `atomic_write_json`, request id via
`request_id_for`.

**Q1 resolved — block.** Starting a sheet for entity B while any entity has run
state is refused ("finish or decline <A> first"), because every entity shares one
checkpoint: B's write would invalidate A's bound digest and B's finish would flip
the checkpoint off `awaiting_human`, which A's gate requires.

### D2 — Resume dispatch (headshot_run semantics)

`run_sheet` checks run state first; if present it resumes and ignores generation
flags. `--finish` is added and only it requires run state: with none, raise
`nothing to finish: no run state for <entity>`. `--resume` keeps its D19 meaning
(`docs/plans/2026-08-27-sheet-qc-D19.md:68`: continue generation after a signed
override) — with no run state it is ordinary generation; with run state both flags
resume the same way.

`read_request` (`run_common.py:111`):
- `pending` → print `gate_command`, return `{"status": "pending"}`.
- `declined` → `write_decision`; restore `sheet_prev_entry` into `characters`
  (or drop the draft if none); append the draft's `qc_receipts` verdict ids and
  asset hashes to `metadata.rejected_sheets[entity]`; clear run state; write
  `status="in_progress"`; raise `Declined` (exit 4). **Q3 resolved:** the draft is
  not left in the artifact. `_acceptable_verdict` gains an exclusion: a verdict
  whose id (or asset hash) is in `rejected_sheets[entity]` is never reused, so the
  next run regenerates the rejected roles. Partial reruns (`--roles` subset,
  `sheet_run.py:153-160`) copy omitted roles from `prev` via `kept` without
  `_acceptable_verdict`; a kept role whose asset or QC receipt is in rejection
  memory is refused and added to the regeneration set automatically. Revision for
  the next run =
  `sheet_revisions[entity] + 1`. **Cap-exhausted rejection:** the rejected pass
  still counts in `attempts_started`; if a rejected role's series has no attempts
  left, the next run raises a dedicated `Blocked` ("human-rejected sheet exhausted
  its budget for <role>; raise `qc.max_attempts_per_series` (config re-sign) or
  change the series") instead of a useless override request (there is no failed
  verdict to override). Tested explicitly at cap 1.
- `missing` → republish: reload draft entry from checkpoint, re-run
  `verify_character_sheet(qc_must_be_present=True)`, rewrite checkpoint (same
  content, fresh digest), publish request bound to the new digest.
- `done` → kind must equal `expected_kind` else fail closed → `_finish_sheet` (D3).

### D3 — Receipt identity (Q2 resolved) and `_finish_sheet`

**Gate side (`gate_approve.py`). Lock and atomic transitions apply to every kind; digest-vs-checkpoint validation is added for `sheet`, `headshot` and `hero` only (look-lock, reference-import and grandfather requests also carry digests but keep their current behaviour — a generic per-kind binding validator is a separate follow-up); tuple recovery is scoped to `sheet` only** (the CLI constructs the record before `_decide`, `gate_approve.py:1307`, and state-mutating kinds such as `look_lock`/retire cannot be reconstructed after their receipt committed, so a generic recovery path is unreachable; a signed `request_id` is the general fix and is out of scope):
1. `_decide` runs its whole decision under `gates.receipt_lock(project_id,
   "approval")` — the same stream `record_human_approval` commits on (re-entrant,
   `lib/gates.py:600`): reload → recovery lookup → mint → `record_human_approval` →
   enrich → move. The reload compares the on-disk request's immutable fields to
   the in-memory one and refuses on any difference; fields the CLI supplies at
   decision time (`reason` for `qc_override`, `gate_approve.py:1296`; `note`,
   `selection`) are overlaid from the trusted in-memory values, never taken from
   disk, so the D19 override workflow keeps working (regression test). That makes logical-request uniqueness
   hold across processes, not just ledger appends.
   Transition (never two files at once — `request_state` treats that as corruption,
   `run_common.py:98`): `atomic_write_json` rewrites the *pending* file in place with
   `approval_receipt_id = receipt["receipt_id"]`, then the existing `atomic_move`
   moves that enriched file to `done/`. Crash windows: before the rewrite → pending,
   no receipt id, recovery below; after the rewrite → pending file names a receipt
   id, which is an unsigned marker: `_decide` runs `exact_approval` on it against
   the reconstructed `(kind, entity, record_sha256, source_checkpoint_digest)` and
   completes the move only on a match (stale or tampered id → refuse).
   **Decline** moves under the same lock: reload, confirm still pending,
   `atomic_write_json` the pending file with `declined_note`, `atomic_move` to
   `declined/`. A pending file that already carries `declined_note` is a committed
   decline: `load_request` completes its move to `declined/` under the lock before
   any display or approval, so a crash between rewrite and move can never be
   approved afterwards (test: post-rewrite approve attempt yields no receipt). (today `_decline` writes a copy then unlinks, `gate_approve.py:1134`,
   so a crash leaves both files and an approve/decline race can leave `done` +
   `declined`).
   Recovery (crash after commit, before the rewrite) exists **only for kind
   `sheet`**, and only when the request's non-null `source_checkpoint_digest`
   revalidates against the current checkpoint: exactly one verified receipt matching `(kind, entity_id,
   record_sha256, source_checkpoint_digest)` is reused and the move completed; 0 →
   normal mint; >1 → refuse naming them. Every other kind: if a verified receipt
   already matches the reconstructed tuple, the gate refuses and names it (a human
   decides; receipts do not bind a request id, so silent reuse could attach an old
   approval to a new request).
2. Sheet constructor (`_construct_character`): require
   `req["source_checkpoint_digest"]` to be a 64-hex string equal to
   `checkpoint_digest(checkpoint_visual_bible.json)` now; `None` is refused for
   sheet requests (no legacy pending sheet requests exist). Pre-commit closure
   reloads the checkpoint, recomputes the digest and the entry, and refuses on any
   difference (mirrors `:976-995`).
3. Same digest check added to the headshot/hero constructors when the request
   carries a digest (currently only re-derived, not compared to the request).

**Shared verifier** `lib/receipts.exact_approval(root, *, receipt_id, kind,
entity_id, record_sha256, source_checkpoint_digest=_UNSET)` → the verified row with
that id, or raises if it is absent / mismatched on any supplied field. The digest
argument uses a sentinel: omitted → not compared (canon enforcement, legacy
entries with `None` digests); explicitly passed (including `None`) → must equal the
receipt's field (enriched-marker check for digest-less kinds).
`_require_entry_receipt` (`canon_enforcement.py:1077`) switches to it (keeps its
current receipt_id + digest semantics, gains the explicit-id lookup instead of
latest-wins). Finish uses it with the digest.

**`_finish_sheet`** (fail closed on every check):
1. Parse the done request: bound digest is 64-hex; `approval_receipt_id` present
   (legacy done files without it: unique tuple match, refuse on 0 or >1).
2. The visual_bible checkpoint is still `awaiting_human` and
   `checkpoint_digest(current file) == bound` — nothing may have rewritten the
   checkpoint since the request was published (post-approval TOCTOU). If it has,
   refuse with a message naming `sheet_run --abandon` (below); a republish is not
   attempted because the signed digest can no longer match, and the gate cannot
   decline a request already in `done/`.
   **Abandon transition** (`sheet_run.py --abandon --entity X`, only with run
   state whose request is `done` and stale). The checkpoint is authoritative and
   the transition is idempotent:
   a. Reconcile: the current entry for X must still be exactly the draft this run
      published (same `sheet_revision`, same `character_approval_record` digest).
      If not, refuse and keep run state — a newer edit exists and a human must
      reconcile; nothing is overwritten.
   b. One checkpoint write: restore `sheet_prev_entry` (or drop the draft) **and**
      set `run_state[X] = {"mode": "abandoning", "request_id", "receipt_id"}`.
   c. `write_decision` (receipt id, reason "stale checkpoint digest").
   d. `atomic_move` the done marker to `.gate-requests/abandoned/` (a fourth
      directory `request_state` never looks in).
   e. Checkpoint write clearing `run_state[X]`, status `in_progress`.
   Resume with mode `abandoning` re-runs c–e idempotently (marker already moved →
   skip; decision already logged → skip) and never enters the missing-request
   republish path. The receipt stays in the ledger, unused. Next run starts
   revision `sheet_revisions[X] + 1`. Tested, including a crash after step d.
3. (was 1) `approval_receipt_id` present
   (legacy done files without it: fall back to unique tuple match, refuse on 0 or >1).
3. Draft entry for entity in `vb["characters"]` with `sheet_revision ==
   state["revision"]` and `status == "draft"`.
4. `exact_approval(receipt_id, "sheet", entity, sha256(character_approval_record
   (entry, palette)), source_checkpoint_digest=bound)`.
5. `verify_character_sheet(entry, qc_must_be_present=True)`.
6. Flip `status="approved"`, `approval_receipt_id=receipt_id`; `_write(status=
   "in_progress", run_state={entity: None}, prev_entry cleared)`.
7. `canon_view.build_view(root)` best effort.
8. Print `approved <entity> rev N receipt <id>` and the next command; return
   `{"entity_id", "status": "approved", "revision", "receipt_id"}`.

### D4 — Enforcement backstop

`canon_enforcement` `in_progress` path (`:1915-1921`) returns before
`_check_visual_bible`, so approved data in a partial bible is never
receipt-checked. Add to `_check_visual_bible_v12`: the same exact-receipt checks
`_check_visual_bible` runs at `:1183` (characters), `:1188` (locations) and `:1224`
(poster) for every `status == "approved"` character, every approved location, and
an approved poster. One failing test per category (bogus receipt id in an
`in_progress` bible). Ace's and Sebastian's entries must pass this — verify on
Bloodless before committing.

### D5 — Hygiene (all accepted)

- `resolve_project_root`, `require_entity_id`, `request_id_for` from `run_common`;
  keep `run_lease.acquire` as-is.
- `atomic_write_json` for sheet and override requests.
- `Declined` / `EXIT_DECLINED=4` added to sheet_run.

### D6 — Director skill text

`visual-bible-director.md:93-99`: replace the by-hand flip with
`sheet_run.py --project X --entity Y --finish`; keep the "never approved without a
receipt" warning (now enforced by D4).

## Steps

1. `lib/receipts.exact_approval` + tests.
2. `gate_approve.py`: `_decide` and `_decline` under `receipt_lock`; enrich-in-place
   then `atomic_move` for both; enriched-marker check via `exact_approval`; sheet-only
   tuple recovery; sheet digest validation + reloading pre-commit; headshot
   digest-vs-request check. Tests: gate-time digest tampering refused; checkpoint
   mutated between construct and commit refused; recovery reuses the single receipt.
3. `canon_enforcement`: D4 backstop + test (approved entry with bogus receipt id in
   an `in_progress` bible is refused).
4. `sheet_run.py`: `_write`, run state, `_resume`, `_republish`, `_finish_sheet`,
   decline handling with rejection memory, one-at-a-time, `--finish` (+`--resume`
   alias), hygiene.
5. Tests `tests/lib/test_sheet_run.py`:
   - happy path: publish → `--finish` pending prints gate command → `approve_request`
     → re-run → approved, `approval_receipt_id` set, checkpoint `in_progress`,
     run_state empty, `by-entity/<CHAR>/{hero,turnaround,expressions}.png` present,
     `wardrobe.png` only when requested, legacy role links (`front`, `three_quarter`,
     `profile`, `full_body`) absent (1.3 sheet contract, `sheet_verify.py:44`);
   - tampered `source_checkpoint_digest` in done request → refused;
   - done request of another kind → refused;
   - declined → decision logged, prev entry restored, rejected ids recorded, next
     run regenerates (gen count rises) at revision 2;
   - missing request → republished after re-verification;
   - `--finish` with no state → "nothing to finish";
   - second entity while first pending → refused;
   - declined at cap 1 → dedicated Blocked, no override request written;
   - gate: two *processes* (`multiprocessing`) deciding one sheet request yield one
     receipt and one `done` file; approve-vs-decline race across two processes ends
     in exactly one of `done`/`declined`; crash between commit and rewrite recovers
     the single sheet receipt; enriched pending file with a wrong receipt id is
     refused; non-sheet kind with an existing matching receipt is refused, not
     reused; decline crash-safe (never two files);
   - finish refuses when the checkpoint was rewritten after signing (digest ≠ bound);
     `--abandon` then archives the marker, restores prev entry, clears state; the
     next run is revision N+1;
   - `qc_override` decision under the lock keeps the typed reason;
   - partial rerun with a rejected kept role regenerates that role;
   - enforcement: approved character / location / poster with a bogus receipt id in
     an `in_progress` bible each refused;
   - update `test_happy_path_writes_draft_and_gate:83-86` (re-run after signing now
     finishes rev 1; rev 2 comes from a third run).
6. Director text (D6).
7. `make test` green (baseline 1849 collected). On Bloodless: enforcement rerun with
   D4 passes for Ace + Sebastian; `sheet_run --finish --entity sebastian-dragos`
   reports "nothing to finish". No paid generation.
