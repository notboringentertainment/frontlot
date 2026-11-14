# Independent Review Request — authored-film pipeline (OpenMontage)

You are performing an independent technical review. Repo: `~/Projects/OpenMontage`.

## What was built

A new "authored-film" pipeline was added to OpenMontage (an agent-first film-generation framework with stage-based pipelines, YAML manifests, JSON-Schema artifact contracts, a checkpoint/gate engine, and markdown "director skill" files that instruct agents at each stage).

Purpose: ingest a writer's finished story-development assets — synopsis/treatment/outline/story bible (with AI-production annexes), "wayfinder" decision tickets and MAP, canon atoms (locked story decisions with evidence hashes), and PitchStudio exports — and produce a film without ever forking or silently mutating that canon. Canon fidelity is the core requirement: locked decisions and protected lines must survive every stage; conflicts must escalate to the human rather than resolve silently; gaps must become explicit questions, not inventions.

New/modified files:

- `pipeline_defs/authored-film.yaml` — seven stages: canon_ingest → proposal → script → scene_plan → assets → edit → compose. Human approval gates on the first five stages; edit/compose auto-proceed unless a canon collision forces a gate.
- `schemas/artifacts/canon_packet.schema.json` — new artifact type: locks with evidence hashes, protected lines, characters (incl. never_write_as, continuity risks), locations, world rules, tone doc, beat structure, open questions with blocking flags.
- `skills/pipelines/authored-film/` — nine director skill files. `canon-guard.md` defines the cross-stage contract (authority ladder: canon atoms > wayfinder locks > writing docs > groundwork > reference; collision escalation; no silent resolution). `canon-director.md` maps the writer's literal source formats into canon_packet.
- Registrations: `schemas/artifacts/__init__.py` (artifact registry), `AGENT_GUIDE.md`, `skills/INDEX.md`.

Relevant runtime (pre-existing, not authored in this change): `lib/checkpoint.py` (checkpoint/gate engine), the pipeline loader, `schemas/artifacts/decision_log.schema.json`, `schemas/artifacts/final_review.schema.json`, and the repo test suite.

Builder's validation claims: schema compiles; manifest validates; fixtures validate; registry resolves; pipeline loads through the repo's own loader; repo instruction-integrity tests pass; a read-only dry-run mapped 10 real canon atoms to schema-valid locks.

## Prior review exists — treat as claims, not ground truth

A prior automated peer review is at `reviews/2026-08-08-codex-peer-review.md`. It concluded REQUEST CHANGES, alleging three runtime-enforcement failures (stage completion without required output artifact; invalid decision categories persisting to the cumulative decision log despite checkpoint rejection; compose completing without a canon pass) plus several schema/documentation gaps.

Do not assume that review is correct, complete, or correctly prioritized. It may contain false positives (e.g., misread code paths, probes that don't reflect real pipeline invocation), miscalibrated severities, and misses. Your job is to form your own judgment:

1. Review the build on its own terms first — read the manifest, schemas, skills, and the checkpoint/loader runtime before opening the prior review.
2. Independently test the enforcement questions that matter for this pipeline's stated purpose: can any stage complete without its declared outputs? Are declared input requirements enforced? Can invalid data persist? Do the human gates actually gate? Do it with real executions/probes against the repo code where possible, not by reading prose.
3. Then read the prior review and, for each of its findings, mark: confirmed (with your own evidence), refuted (with evidence), or unverifiable. Note anything it missed and anything it over- or under-weighted.
4. Assess the design questions the prior review didn't settle: is the canon-fidelity contract actually enforceable by the runtime as built, or does it live only in skill prose that a misbehaving agent could ignore? Is that acceptable for this framework's execution model?

## Deliverable

Your own verdict (ship / ship-with-fixes / request-changes) with:

- Findings ranked by severity, each backed by file:line evidence or a reproducible probe.
- A disposition table for the prior review's findings (confirmed / refuted / unverifiable / severity adjusted).
- Anything the prior review missed.
- The minimal fix set you'd require before a first real production run.

Nothing here is committed to git yet; this review gates the commit.
