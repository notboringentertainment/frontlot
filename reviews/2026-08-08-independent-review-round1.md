# Independent Technical Review — `authored-film` pipeline

Date: 2026-08-08  
Repository: OpenMontage (local working copy)  
Verdict: **REQUEST CHANGES**

## Executive assessment

Manifest loads, seven-stage order is correct, new schema is registered, skill paths resolve, and manifest human gates are enforced by `write_checkpoint`. Those are real strengths.

Pipeline is not canon-safe yet. Runtime enforces approval flag, but does not enforce manifest-declared inputs or complete output sets. `canon_ingest` can complete with no `canon_packet`; proposal can advance without canon or decision log; unresolved blocking canon questions can survive an approved proposal; compose can complete with no `final_review`, no canon pass, and even a nonexistent output file. Required `canon_ruling` entries are schema-invalid and persist despite rejected checkpoint.

Canon-fidelity contract therefore lives mainly in skill prose. That fits OpenMontage for qualitative judgment, but not for structural invariants explicitly described as binding or mechanical. Before first production run, runtime must enforce facts it can determine exactly: required artifacts, question-resolution state, decision validity, source references, protected-line equality evidence, canon-pass presence/status, and render existence.

Review order honored requested anti-anchoring method: build, runtime, schemas, skills, and probes were reviewed first. Prior report opened only after preliminary verdict and findings were recorded.

## Findings, ranked by severity

### [Critical] 1. Manifest stage contracts are not runtime contracts

Manifest declares `canon_ingest` produces `canon_packet`, proposal requires it, and proposal produces both `proposal_packet` and `decision_log` (`pipeline_defs/authored-film.yaml:82-115`). Manifest schema recognizes `required_artifacts_in` and `produces` (`schemas/pipelines/pipeline_manifest.schema.json:47-63`).

Checkpoint validation ignores those fields. It uses hard-coded `CANONICAL_STAGE_ARTIFACTS`, which has no `canon_ingest` entry (`lib/checkpoint.py:30-40`), then skips missing-artifact enforcement when map lookup returns `None` (`lib/checkpoint.py:124-143`). Prerequisite enforcement verifies only predecessor checkpoint completion/approval, not declared input artifacts (`lib/checkpoint.py:284-346`).

Independent probes:

| Probe | Result |
|---|---|
| Complete `canon_ingest` with `{}` and no approval | Rejected: gate works |
| Complete `canon_ingest` with `{}` and `human_approved=True` | **Accepted** |
| Complete proposal with only valid `proposal_packet`, after empty ingest | **Accepted** |
| Omit proposal's declared `decision_log` output | **Accepted** |

Impact: pipeline can cross foundation gate without authoritative story artifact. Every downstream fidelity claim then lacks source of truth.

Required fix: derive required outputs from current manifest stage for `completed` and `awaiting_human`; derive required inputs from completed predecessor artifacts before stage advancement. Keep hard-coded map only as legacy fallback. Validate every declared artifact with registry schema.

### [Critical] 2. Compose can self-certify without canon pass

Manifest declares `render_report` and `final_review`, but makes `canon_packet` optional at compose (`pipeline_defs/authored-film.yaml:241-277`). Runtime requires only hard-coded canonical `render_report` (`lib/checkpoint.py:30-40`, `124-157`). `render_report.final_review_ref` is optional (`schemas/artifacts/render_report.schema.json:52-59`).

Compose skill requires lock-by-lock, protected-line, continuity, and tone verdicts (`skills/pipelines/authored-film/compose-director.md:31-41`). Shared `final_review` schema defines no canon-pass object. Its required check objects are technical/general (`schemas/artifacts/final_review.schema.json:19-155`), and each inner object can be empty.

Independent probes:

| Probe | Result |
|---|---|
| Complete full stage sequence; compose with valid `render_report` only | **Accepted** |
| Validate `final_review` with `status: pass` and five empty required check objects | **Accepted** |

Impact: auto-proceeding final stage can declare success without checking pipeline's core promise.

Required fix: make `canon_packet` required at compose; require `final_review`; require structured `canon_pass` containing every lock verdict, protected-line text/audio verdict, continuity samples, tone verdict, unresolved flags, and overall pass/fail. Reject completed compose unless canon pass exists and passes. Canon collision should force `awaiting_human`.

### [High] 3. Blocking questions and writer rulings have no enforceable state transition

Canon schema says `open_questions[].blocking=true` stops pipeline at next gate (`schemas/artifacts/canon_packet.schema.json:187-199`). Proposal skill says questions must be answered there, recorded as `canon_ruling`, and honored as locks thereafter (`skills/pipelines/authored-film/proposal-director.md:54-60`).

Runtime reads neither blocking flags nor resolution state. Probe completed and approved proposal while prior valid canon packet still contained unresolved blocking question: **accepted**.

Even cooperative path lacks durable effective-canon model. Ruling is placed in cumulative decision log, while downstream script contract requires only `canon_packet` and `proposal_packet` (`pipeline_defs/authored-film.yaml:132-151`). Original canon packet remains unchanged; no question ID, status, resolution link, canon amendment, or runtime-generated effective lock set exists.

Impact: exact moment human fills canon gap is not reliably carried downstream. Agent can silently invent answer or forget ruling.

Required fix: give questions stable IDs and explicit `open/resolved` state; link resolution to validated writer decision; materialize effective canon as immutable base packet plus validated amendments, or revised canon packet with revision/hash chain. Block proposal completion while any blocking question lacks valid resolution. Make effective canon required downstream.

### [High] 4. Canon rulings are schema-invalid, and rejected data persists

Both canon guard and proposal director require `category: "canon_ruling"` (`skills/pipelines/authored-film/canon-guard.md:62`; `proposal-director.md:59`). Decision schema enum omits it (`schemas/artifacts/decision_log.schema.json:25-44`).

Checkpoint writer merges and writes cumulative decision log before checkpoint validation (`lib/checkpoint.py:527-547`); merge writes directly to final path (`lib/checkpoint.py:392-419`).

Independent probe:

- Proposal checkpoint with otherwise valid `canon_ruling`: rejected by schema.
- After rejection, `decision_log.json` existed and contained invalid `canon_ruling`: **confirmed persisted**.

Impact: required happy path fails, then corrupts cumulative audit state. Subsequent stages/reviews may consume decision runtime explicitly rejected.

Required fix: add `canon_ruling` with schema suited to writer rulings. Validate incoming and merged log before any mutation. Stage checkpoint and cumulative log to temp files; commit only validated state. Add rollback/recovery test for write failure.

### [High] 5. Shared artifact schemas accept zero canon evidence

Manifest success criteria demand source refs and canon checks (`pipeline_defs/authored-film.yaml:143-151`), but shared script schema requires neither: `source_ref` is optional and `metadata` is opaque (`schemas/artifacts/script.schema.json:38-101`). Scene schema has no structured canon/continuity refs (`schemas/artifacts/scene_plan.schema.json:15-114`). Asset skill requires recording applied references and risk notes (`skills/pipelines/authored-film/asset-director.md:28-37`), but asset item schema has no such fields and forbids unknown fields (`schemas/artifacts/asset_manifest.schema.json:14-55`).

Independent schema probes all **accepted**:

- Script section containing invented ending with no `source_ref` or `metadata.canon_check`.
- Asset entry with no continuity references or risk-note evidence.
- Final review marked pass with empty checks and no canon evidence.

Impact: a misbehaving or context-drifted agent can produce fully schema-valid artifacts contradicting canon. Human gates reduce risk but do not make contract enforceable.

Required fix: add authored-film validation profile or authored-specific artifact schemas. At minimum require, in authored mode:

- script: per-section beat/lock refs; structured canon check; protected-line verification;
- scene plan: canon refs and structured continuity constraints per tracked entity;
- asset manifest: references applied, risk notes applied, source canon refs;
- final review: structured canon pass described above.

Do not globally tighten shared schemas if other pipelines cannot satisfy fields; use pipeline-aware validation.

### [High] 6. Compose success criteria do not verify render exists

Manifest requires output file exists and passes ffprobe (`pipeline_defs/authored-film.yaml:274-277`). Render schema requires only path string and metadata (`schemas/artifacts/render_report.schema.json:10-28`). Checkpoint validation performs JSON Schema validation only (`lib/checkpoint.py:145-157`).

Compose probe used `renders/output.mp4`; file did not exist. Completed compose was **accepted**.

Impact: pipeline can report completed film with no film.

Required fix: compose completion validator must resolve paths inside project workspace, require regular files, and verify duration/container/audio/resolution from actual probe output. Prevent path escape while resolving.

### [Medium] 7. Canon provenance and authority schema are weaker than director contract

Canon director requires every source document gets SHA-256 (`skills/pipelines/authored-film/canon-director.md:40-45`). Schema does not require `sha256`, gives it no hash pattern, and permits empty paths (`schemas/artifacts/canon_packet.schema.json:18-39`). Lock IDs, decisions, and source paths permit empty strings; evidence ref is optional (`canon_packet.schema.json:42-60`). Probe with empty source path, empty lock ID/decision/source path, no SHA-256: **accepted**.

Authority ladder gives writing documents distinct tier (`skills/pipelines/authored-film/canon-guard.md:14-29`), but source-document authority enum has no writing-document value (`canon_packet.schema.json:31-34`). Canon director mines pitch departures into locks (`canon-director.md:48-55`), while lock `source_type` omits `pitch_export` (`canon_packet.schema.json:52-55`).

Impact: packet can be schema-valid while provenance is unusable or authority is misclassified.

Required fix: require non-empty paths/IDs/decisions; require 64-hex SHA-256 for every read source; use conditional evidence requirement where source contains evidence; add writing-document authority tier and exact lock source types used by ingest skill.

### [Medium] 8. No authored-film regression tests

Search across `tests/` found zero files mentioning `authored-film`, `canon_ingest`, `canon_packet`, or `canon_ruling`.

Impact: manifest/schema smoke tests pass while core behavioral contract remains broken.

Required tests: reproduce every probe above, including rejection side-effect checks, blocking-question resolution, structured canon evidence, compose canon pass, output file verification, and positive seven-stage happy path.

## Prior-review disposition

| Prior claim | Disposition | Independent evidence / adjustment |
|---|---|---|
| `canon_ingest` can complete without `canon_packet`; inputs/outputs ignored | **Confirmed** | Empty approved ingest and proposal missing canon/decision log accepted. Severity remains Critical. |
| `canon_ruling` invalid yet persists | **Confirmed** | Schema rejected category; cumulative file retained it. Severity remains High. |
| Compose completes without canon pass | **Confirmed; severity raised** | Render-only compose accepted; empty canon-free final review also validates. Critical because compose auto-proceeds and canon fidelity is pipeline's core promise. |
| SHA-256 optional; empty paths allowed | **Confirmed** | Direct schema probe accepted missing hash and empty strings. Medium. |
| Authority tier ambiguous; `pitch_export` missing from lock source types | **Confirmed** | Skill/schema enums conflict. Medium. |
| Asset continuity evidence lacks named schema fields | **Confirmed** | Asset without evidence validates; schema forbids adding undeclared structured fields. High in authored pipeline because continuity provenance is core. |
| No authored-specific tests | **Confirmed** | Repository search returned `MATCHING_TEST_FILES=0`. Medium release-risk finding. |

No prior core finding was refuted. None was unverifiable.

## Important misses in prior review

1. **Blocking questions can survive proposal approval.** This is separate from missing input check: even valid canon packet with `blocking=true` does not stop advancement.
2. **Canon rulings lack downstream canonicalization.** Logging ruling does not produce effective lock set or resolve question in canonical state.
3. **Ungrounded scripts are schema-valid.** `source_ref` and `canon_check` are optional despite manifest success criteria.
4. **Empty final-review checks pass.** Problem is broader than missing canon fields: required check objects require no meaningful subfields.
5. **Nonexistent render completes.** Manifest success criterion is prose only.

## Is prose-only enforcement acceptable here?

Partly.

OpenMontage explicitly defines itself as instruction-driven: agent owns orchestration and creative judgment; Python owns tools and persistence (`AGENT_GUIDE.md:70-88`). Natural-language questions such as whether performance “feels” off-tone or whether scene subtly violates character identity can remain skill/reviewer/human judgments.

Structural invariants are different. File presence, artifact types, lock IDs, exact protected-line equality, unresolved blocking flags, decision enums, decision atomicity, canon-pass presence, and render existence are deterministic. Framework already claims completed checkpoints include canonical artifact and invalid artifacts fail fast (`AGENT_GUIDE.md:564-568`, `608-616`). Leaving those to prose contradicts framework's own contract.

Therefore: prose-guided semantic review is acceptable; prose-only structural enforcement is not. Current build cannot honestly promise “without ever forking or silently mutating canon.” It can promise only “agent instructed to preserve canon, with human gates,” which is materially weaker.

## Minimal fix set before first real production run

1. **Manifest-aware checkpoint validation**
   - Require all `produces` artifacts on `completed`/`awaiting_human`.
   - Require and validate all `required_artifacts_in` from completed predecessors.
   - Add authored-film happy-path and omission tests.

2. **Decision-log repair**
   - Add `canon_ruling` schema.
   - Validate before mutation; stage atomic writes; prove rejected checkpoints leave no log changes.

3. **Effective-canon state**
   - Stable question IDs and resolution state.
   - Validated writer-ruling/amendment chain.
   - Block advancement with unresolved blocking questions.
   - Require effective canon at every downstream stage, including edit and compose.

4. **Authored canon validation profile**
   - Enforce script refs/check, scene continuity refs, asset reference/risk evidence, protected-line verification.
   - Require structured `final_review.canon_pass` and reject non-pass compose.

5. **Compose reality check**
   - Require `final_review` output and canon input.
   - Require actual output file and successful ffprobe-derived facts before completion.

6. **Schema provenance hardening**
   - Non-empty identifiers/paths/decisions, SHA-256 pattern and requirement, authority/source-type alignment.

These are first-run blockers. Full automated semantic contradiction detection can wait; humans and reviewer skill can judge nuanced story meaning after structural evidence is guaranteed.

## Verification performed

- Loader/order/registration checks: passed. Order is `canon_ingest → proposal → script → scene_plan → assets → edit → compose`.
- Draft 2020-12 schema validation: `canon_packet` passed.
- All stage skills and produced-artifact schemas exist.
- Focused adjacent suite: **16 passed**.
- Broader selected slice: **50 passed, 1 environment failure** (`requests` missing during ComfyUI discovery).
- Full suite could not collect in current interpreter: 12 environment import errors (`fastapi`, `PIL`, `requests`, `numpy`). No product-code failure inferred from those collection errors.
- Custom isolated probes used temporary directories; no project run or repository file was mutated.

## Reproducible probe calls

Core calls were made against `lib.checkpoint.write_checkpoint` using `tempfile.TemporaryDirectory`:

```python
write_checkpoint(root, "p", "canon_ingest", "completed", {},
                 pipeline_type="authored-film", human_approved=True)
# Accepted; should reject missing canon_packet.

write_checkpoint(root, "p", "proposal", "completed",
                 {"proposal_packet": valid_proposal},
                 pipeline_type="authored-film", human_approved=True)
# Accepted after empty ingest; canon_packet input and decision_log output absent.

write_checkpoint(root, "p", "compose", "completed",
                 {"render_report": valid_render_report},
                 pipeline_type="authored-film")
# Accepted after valid predecessor checkpoints; final_review absent and output path nonexistent.
```

Invalid-log probe supplied otherwise valid `decision_log` with `category="canon_ruling"`; checkpoint raised schema error, then `projects/<id>/decision_log.json` still contained rejected entry.
