# Authored-Film — The User Workflow

What actually happens when you produce a film from your finished writing.
Written from a live end-to-end shakedown run (2026-08-08, project
`shakedown-last-signal`), not from theory.

## What you need before you start

1. **Your development assets, done.** Any mix of: synopsis / treatment /
   outline / story bible (with AI-production annexes if you have them),
   wayfinder MAP + resolved tickets, canon atoms, PitchStudio export.
   The pipeline adapts what exists; it never invents what doesn't.
2. **A voice source.** The canon pass requires protected lines AUDIBLE in
   the mix — a machine with zero TTS cannot pass the canon validation. Local
   free path: `pip install piper-tts` into the repo venv, then
   `python -m piper.download_voices en_US-lessac-medium --download-dir ~/.piper/models`.
   Better voices: put an ElevenLabs/OpenAI key in `.env`.
3. **`make setup` run once** (venv + deps + Remotion). Node ≥ 22 and ffmpeg.
4. **`projects/<your-project>/project.yaml`** (schema
   `schemas/project_config.schema.json`) with `budget_usd_cap`,
   `wall_time_minutes`, `cast_cap {characters, locations}` and
   `provider_egress {provider: fal, content_classes: [prompts,
   reference_images]}`. The egress block is your explicit, one-time
   acknowledgment that prompts and generated images are sent to FAL; without
   it the visual bible cannot start. The file is not self-attesting: the
   agent presents it, you approve it, and that approval is bound to the
   file's sha256 — change a number and it must be approved again.
5. **`FAL_KEY` in `.env`** for Seedream (sheets, storyboards, key art) and
   Seedance 2.5 (video). A licensed font file for the title card.

## The one start command

Open Claude Code in the OpenMontage repo and say:

> Produce this with the authored-film pipeline.
> Sources: <path to your docs>, <path to wayfinder/>, <path to atoms/>.
> Project id: <kebab-case-title>.

That's the whole invocation. The agent must then (per AGENT_GUIDE): take the
run lease, check `project.yaml` against its approval, run preflight and show
you the real capability menu, read the stage director skills, and open the
first gate. The stage order is
`canon_ingest → proposal → visual_bible → script → scene_plan → assets → edit → compose`.

## What you'll actually experience — the six gates

You are the writer. The pipeline stops for you six times (and, inside two of
those stops, several more times for individual images); each stop is a
checkpoint written as `awaiting_human`, and the runtime physically cannot
advance past a gate without your recorded approval.

**Gate 1 — Canon ingest.** The agent shows you its reading of your story:
every lock (with source + hash), protected lines character-for-character,
beats, characters/locations with continuity risks, and every open question
with an honest blocking flag. You approve the reading or correct it.
*This is the most important gate — everything downstream obeys this packet.*

**Gate 2 — Proposal.** 3+ visual treatments of the SAME locked story (never
alternative stories), the runtime choice (Remotion vs HyperFrames vs ffmpeg,
both presented), music plan, cost. **Blocking questions get asked here** —
production cannot proceed past this gate while any `[NEEDS DECISION]` you
marked blocking is unanswered. Your answers are recorded as canon rulings
and honored as locks from then on.

**Gate 3 — Visual bible.** Character and location reference sheets, one
entity at a time, for exactly the cast named in the approved proposal. For
each character: four hero portrait candidates (you pick one or reject all),
then a six-view sheet derived from the one you picked (front, three-quarter,
profile, full body, expressions, wardrobe), approved as one unit. For each
location: an establishing plate then 2–3 angles. Then the poster: four key-art
candidates, a title card rendered locally from your font (never by a model),
and the composite. Every one of these answers is recorded as a signed receipt
in `approvals.jsonl`; the agent cannot mint one. An approved sheet is canon —
changing it later needs a ruling from you.

**Gate 4 — Script.** The adaptation, with per-section provenance back to
your beat/lock ids, protected lines verbatim, any `[production line]` the
runtime needed to add, flagged for your veto, and any lock conflict presented as a
tension (never silently resolved).

**Gate 5 — Scene plan.** Scenes with your continuity facts embedded in the
descriptions, each naming which sheets it binds to (`character_refs`,
`location_ref`), the video model per scene (Seedance 2.5 unless a scene says
otherwise with a reason), and the shot list — for a trailer, 12–20 shots, one
action each.

**Gate 6 — Assets.** Two stops. First the storyboard: one frame per shot,
generated from your approved sheets, approved as a batch before a single
video credit is spent. Then the takes: for each shot, candidates generated
with the hero, wardrobe, location and storyboard frame packed as references,
with the evidence of which references were actually sent. You see spend
against your cap at both stops.

**Then edit + compose run without gates** — unless anything collides with
canon, which forces a gate anyway. Compose ends with a canon pass verified
against the actual render (locks honored, protected lines verbatim AND
audible, continuity spot-checks, tone verdict) and a real file check
(exists, video stream, duration, dimensions). A failing canon pass cannot
self-certify; it comes back to you.

## Answering in one line

Gates accept terse replies. Real examples from the shakedown:

> approve
> approve, B      (approving the gate + ruling on question q-001)
> revise: lock atom-003 is missing from the packet

**Autopilot:** you may pre-authorize remaining gates ("autopilot" /
"pre-authorize remaining gates"). This is recorded as an `approval_policy`
decision in the audit log — approval is never assumed. Blocking canon
questions still require YOUR answer; autopilot cannot answer story
questions, by construction. Autopilot also does not cover receipt-bound
sub-gates (hero picks, sheets, storyboard batch, poster): those are answered
by you, one at a time, because each answer mints a one-use token.

## Money, and what stops the run

- **`budget_usd_cap`** in `project.yaml` is the ceiling. Before every paid
  call the agent writes a reservation to `cost-reservations.jsonl`; a
  reservation that would cross the cap fails before any network call.
- **Indeterminate paid calls halt the run.** If the process dies between
  sending a request and recording the provider's request id, the reservation
  is left in state `submitting` with no id. On restart the agent does not
  guess and does not resubmit: it stops and asks you to check the FAL
  dashboard and mark the reservation reconciled (charged or not). This is the
  one place a resume needs you before any gate.
- **One session per project.** `.run-lease` is taken when a run starts; a
  second session on the same project fails immediately and tells you who
  holds the lease. A lease is reclaimed only when its owner process is
  demonstrably dead.

## What the machine does vs. what you do

| You (writer) | The agent | The runtime (enforced) |
|---|---|---|
| Supply finished development assets | Reads them into the canon packet | Packet must be schema-valid, hashed, nothing invented |
| Approve gates, answer blocking questions | Presents gates, asks questions in your [NEEDS DECISION] convention | No gate skip; no unanswered blocking question passes |
| Rule on collisions | Escalates collisions, never resolves them | Unruled tensions cannot complete |
| Watch the film | Runs the canon pass against the real render | No canon pass = no completed film; line must be audible; file must exist |
| Pick portraits, approve sheets, storyboards, poster | Generates candidates, derives sheets, packs references | Every approval is a signed receipt; every image has a generation receipt; sheets are hashed canon |

## Keyless reality check

With no API keys, "asset generation" = authored Remotion/HyperFrames
composition + local TTS + ffmpeg-synthesized ambience. That produced a real
film in the shakedown — but it is animation, not generated photography.
For photographic frames, configure at least one image/video provider in
`.env` (the preflight menu lists exactly what each key unlocks).

## Where everything lands

```
projects/<your-project>/
├── project.yaml                budget cap, wall time, cast cap, egress — approved by digest
├── artifacts/                  canon_packet, proposal, visual_bible, script, scene_plan, manifest…
├── canon/visual/
│   ├── objects/<sha256>.png    every approved image, written once, never deleted
│   ├── characters/<id>/sheet.json   role → asset_id pointers
│   ├── locations/<id>/sheet.json
│   ├── poster/sheet.json
│   └── palette.json
├── assets/                     audio/, video/, images/ (storyboard frames, takes), music/
├── composition/                atelier Remotion entry (if atelier mode)
├── renders/                    the film (poster_final is delivered from canon/, not here)
├── approvals.jsonl             signed receipts for every gate answer you gave
├── generation-receipts.jsonl   one receipt per generated file: tool, endpoint, request id, hash, cost
├── cost-reservations.jsonl     every paid call, reserved before it was sent
├── .run-lease                  who is running this project right now
├── decision_log.json           every ruling and choice, append-only
└── checkpoint_*.json           the gate trail (history/ keeps superseded versions)
```

Receipt signatures and the consumed-token ledger live outside the project in
`~/.openmontage/gates/` — nothing the agent writes in the project tree can
forge an approval.

`python -m backlot open <project-id>` gives you the live board view of all
of it while the run is happening.

## Human gate handler

Approvals are recorded only by `scripts/gate_approve.py`, run by the human from a real
terminal (it refuses a non-TTY stdin, so agents cannot mint tokens). Directors write a
request under `projects/<slug>/.gate-requests/<id>.json` and stop at `awaiting_human`;
the human runs `python scripts/gate_approve.py --project <slug>` to list, then
`--request <id>` to decide. Approve → signed receipt in `approvals.jsonl` + ledger entry;
decline → request moved to `.gate-requests/declined/`, no receipt.
