# Peer Review — authored-film pipeline (Codex, 2026-08-08)

Reviewer: OpenAI Codex (GPT), run via codex-companion from Claude Code session.
Scope: `pipeline_defs/authored-film.yaml`, `schemas/artifacts/canon_packet.schema.json`, `skills/pipelines/authored-film/`, plus loader/checkpoint runtime (`lib/checkpoint.py`, pipeline_loader) and adjacent test suite.
Method: static schema/manifest inspection plus live behavioral probes against the checkpoint engine (empty-artifact completion attempts, invalid-decision persistence, compose completion without canon pass).

Status of this document: reviewer claims, reproduced verbatim below. Not independently confirmed beyond the probe results the reviewer reported.

---

## Verdict: REQUEST CHANGES

Manifest loads. Schemas parse. Skill paths resolve. Human gates work. Authored-film contract still bypassable; not canon-safe yet.

### Real breakages

1. **Critical — `canon_ingest` can complete without `canon_packet`.**

   Manifest declares output at `pipeline_defs/authored-film.yaml:82`. Checkpoint validation uses static canonical-stage map lacking `canon_ingest`, then skips missing-output enforcement at `lib/checkpoint.py:124`. Runtime never consumes `produces` or `required_artifacts_in`.

   Probe results:

   - Empty `canon_ingest` completed checkpoint: accepted.
   - `proposal` prerequisite after empty checkpoint: accepted.

   Fix: manifest-aware output and input validation. At minimum require `canon_packet` for `canon_ingest`; ideally enforce declared `produces` and `required_artifacts_in` generically.

2. **High — required `canon_ruling` decisions fail schema validation and still persist.**

   Skills require `category: "canon_ruling"` in `skills/pipelines/authored-film/canon-guard.md:62` and `skills/pipelines/authored-film/proposal-director.md:59`. Enum omits value in `schemas/artifacts/decision_log.schema.json:25`.

   Worse: cumulative log merges before checkpoint validation at `lib/checkpoint.py:527`. Probe: checkpoint rejected, but invalid `canon_ruling` remained in `decision_log.json`.

   Fix: add enum value; validate before mutation; make cumulative-log update atomic.

3. **High — compose can complete without canon pass.**

   Manifest declares `final_review`, but makes `canon_packet` optional at `pipeline_defs/authored-film.yaml:243`. Checkpoint engine requires only canonical `render_report`. Probe accepted completed compose artifacts containing no `final_review`.

   Existing `schemas/artifacts/final_review.schema.json:19` also defines no structured lock/protected-line/continuity/tone checks—only free-form `metadata`.

   Fix: require `canon_packet` input and `final_review` output; add structured canon-pass fields or authored-specific validation.

### Documentation/enforcement concerns

- Canon Director mandates SHA-256 for every source, but `schemas/artifacts/canon_packet.schema.json:23` makes `sha256` optional and permits empty paths.
- Authority enum has no distinct writing-document tier; `pitch_export` also missing from lock `source_type`. Workarounds exist, but authority ladder becomes ambiguous.
- Asset continuity evidence lacks named schema fields; agents must overload `prompt`, `generation_summary`, or metadata.
- No tests mention `authored-film`, `canon_ingest`, `canon_packet`, or `canon_ruling`.

### Verified working

- `load_pipeline("authored-film")`: passes; correct seven-stage order.
- `canon_packet` registered; Draft 2020-12 schema valid.
- All required skills and produced-artifact schemas exist.
- Human-approval enforcement rejects unapproved completed `canon_ingest`.
- Adjacent manifest/checkpoint suite: **12 passed**.
- Broader slice: **46 passed, 1 unrelated environment failure**—missing `requests` during ComfyUI discovery.

Merge blocker: three real failures above plus authored-specific regression tests.
