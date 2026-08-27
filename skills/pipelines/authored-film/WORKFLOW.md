# Authored-Film — The User Workflow

What actually happens when you produce a film from your finished writing.
Written from a live end-to-end shakedown run (2026-08-08, project
`shakedown-last-signal`), not from theory.

## What you need before you start

1. **Your development assets, done.** Any mix of: synopsis / treatment /
   outline / story bible (with AI-production annexes if you have them),
   wayfinder MAP + resolved tickets, canon atoms, PitchStudio export.
   The pipeline adapts what exists; it never invents what doesn't. One
   thing it will ask you to write during the run: a **look ticket** per cast
   entity in your wayfinder map (see Gate 3). Nobody else decides what your
   characters look like.
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
   Kling o3 pro reference-to-video (video; Seedance 2.5 only for entity-free
   scenes — it rejects human-face references). A licensed font file for the title card.

## The one start command

Open Claude Code in the OpenMontage repo and say:

> Produce this with the authored-film pipeline.
> Sources: <path to your docs>, <path to wayfinder/>, <path to atoms/>.
> Project id: <kebab-case-title>.

That's the whole invocation. The agent must then (per AGENT_GUIDE): take the
run lease, check `project.yaml` against its approval, run preflight and show
you the real capability menu, read the stage director skills, and open the
first gate. The stage order (manifest 1.2) is
`canon_ingest → proposal → look_lock → headshots → visual_bible → script → scene_plan → assets → edit → compose`.

The pipeline version is pinned to your project by a signed
`pipeline_migration` receipt — the first run on a new project asks you to
approve the pin as its own gate, and an existing project gets a one-time
migration receipt for the version it was built under. Upgrading (or
downgrading) later is another receipt that supersedes the last one; nothing
edits the pin quietly.

## What you'll actually experience — the eight gates

You are the writer. The pipeline stops for you eight times (and, inside three
of those stops, several more times for individual images); each stop is a
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

**Gate 3 — Look lock.** For every character and location in the approved
cast, the agent opens (or finds) a **look ticket** in your wayfinder map —
`area: look`, one per entity, created through the wayfinder "cast for
production" procedure. The agent may draft the Question; **you write the
Answer**, as a structured `## Look spec` block (age band, build, hair,
marks, default wardrobe, era signals, continuity risks, negatives, a 20–80
word description — or for a location, establishing view, palette anchors,
terrain, dressing, weather). You also attest the subject is fictional, flag
spoilers, and mark minors (refused for generation). While you draft, the
ticket asks: *do you already have a reference image?* Three answers:

- *No* — the look is words; faces are generated at Gate 4.
- *Yes, generated elsewhere* — imported as **`imported_synthetic`** (JPEG,
  HEIC or PNG; normalized to a clean PNG, hashed, original never copied in)
  with your attestation that it is machine-made and depicts no real person.
  It becomes the hero candidate and a legitimate root for everything
  descended from it.
- *Yes, a real person I'm thinking of* — imported as
  **`casting_inspiration`**: stored for **your eyes only**, shown to you by
  the gate handler, never opened or described by the agent, never sent to
  any model, permanently barred from every lineage. You type the type fields
  (age band, build, era, hair category) into the ticket yourself; faces are
  never fields.

When you resolve a ticket, the agent ingests it, computes its `look_hash`,
and writes a `look_lock` request; you approve it from the terminal. That
receipt — not the ticket, not a chat "yes" — is what makes a look canon.
Changing a look later is a reopened ticket plus a retire receipt, and it
stales every image made from it.

**Gate 4 — Headshots.** One character at a time — and you do not wait for the
rest of the cast (D18): as soon as one look is ratified, that character's face
can be generated, judged, and sheeted while the others are still unwritten.
The stages read "complete" only when everyone is through, which is what gates
trailer assembly, not viewing faces. If you imported a synthetic
image it is the single candidate; otherwise the agent builds a prompt from
your look block (assembled by a builder from the fields — never typed, never
copied) and generates four candidates. Then, in the terminal, the gate
handler shows you the candidates and you pick **1..N or reject-all with a
note**; the handler writes the signed record itself, so there is nothing for
the agent to author. Reject-all → four more, your note logged. Without an
approved headshot no sheet can be generated for that character, by
construction.

**Gate 5 — Visual bible.** Reference sheets, one entity at a time, for
exactly the approved cast. For each character: a six-view sheet derived from
the headshot you picked (front, three-quarter, profile, full body,
expressions, wardrobe), every call carrying your look and headshot
references, approved as one unit. For each location: if you have a reference
image it is imported here through the same gate and the same two classes;
then an establishing plate and 2–3 angles. Then the poster: four key-art
candidates, a title card rendered locally from your font (never by a model),
and the composite. Every one of these answers is recorded as a signed receipt
in `approvals.jsonl`; the agent cannot mint one. An approved sheet is canon —
changing it later needs a ruling from you.

**Gate 6 — Script.** The adaptation, with per-section provenance back to
your beat/lock ids, protected lines verbatim, any `[production line]` the
runtime needed to add, flagged for your veto, and any lock conflict presented as a
tension (never silently resolved).

**Gate 7 — Scene plan.** Scenes with your continuity facts embedded in the
descriptions, each naming which sheets it binds to (`character_refs`,
`location_ref`), the video model per scene (Kling o3 pro unless a scene says
otherwise with a reason), and the shot list — for a trailer, 12–20 shots, one
action each.

**Gate 8 — Assets.** Two stops. First the storyboard: one frame per shot,
generated from your approved sheets, approved as a batch before a single
video credit is spent. Then the takes: for each shot, candidates generated
with the hero, wardrobe, location and storyboard frame packed as references,
every call naming the looks it renders, with the evidence of which references
were actually sent. You see spend against your cap at both stops.

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
sub-gates (look locks, reference imports, headshot picks, sheets, storyboard
batch, poster): those are answered by you, one at a time, because each answer
mints a one-use token. And it cannot resolve a wayfinder ticket — that is a
writing session, not a gate.

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
| Write and resolve look tickets in wayfinder; attest fictional subjects | Drafts the Question at most; ingests the resolved ticket; writes the look_lock request | Look is canon only via a signed `look_lock` receipt bound to `look_hash`; no shape-only or minor looks reach generation |
| Import a reference image, or not | Runs the import gate; never opens a casting-inspiration image | Two origin classes: `imported_synthetic` (attested lineage root) and `casting_inspiration` (your eyes only, tainted out of every lineage, hash-bound in the ledger) |
| Pick a headshot in the terminal | Builds the prompt from the look, generates candidates | The gate handler verifies candidates and writes the record itself; no sheet without an approved `headshot_ref` |
| Approve sheets, storyboards, poster | Derives sheets from the hero, packs references | Every approval is a signed receipt; every image has a generation receipt; every governed call names its `look_refs`; sheets are hashed canon |

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
├── artifacts/                  canon_packet, proposal, look_packet, headshot_packet, visual_bible, script, scene_plan, manifest…
├── canon/visual/
│   ├── objects/<sha256>.png    every approved image, written once, never deleted (incl. imported_synthetic)
│   ├── casting-inspiration/    real-person references — yours only, outside objects/, never in a lineage
│   ├── characters/<id>/sheet.json   role → asset_id pointers
│   ├── locations/<id>/sheet.json
│   ├── poster/sheet.json
│   └── palette.json
├── assets/                     audio/, video/, images/ (storyboard frames, takes), music/
├── composition/                atelier Remotion entry (if atelier mode)
├── renders/                    the film (poster_final is delivered from canon/, not here)
├── approvals.jsonl             signed receipts for every gate answer you gave (incl. look_lock, reference_import, headshot, pipeline_migration)
├── invalidations.jsonl         rebuildable cache of checkpoints staled by retire receipts — never the authority
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

Gate kinds you will meet, and what each records:

| Kind | What you decide | What the receipt binds |
|---|---|---|
| `approval_policy` | The `project.yaml` digest; autopilot | config sha256 |
| `look_lock` | Ratify (or `retire`) one entity's look | `look_hash`, entity key, `source_ticket_ref`, optional `supersedes_look_hash`, `action: activate \| retire` |
| `reference_import` | Import one JPEG/HEIC/PNG as `imported_synthetic` or `casting_inspiration` | origin class, normalized pixel hash, your attestation text |
| `headshot` | **Selection mode**: the handler lists the candidates, verifies their bytes, and you type `1..N` or `reject-all` + note | chosen asset, pixel hash, origin, import receipt, recipe hash, candidates checkpoint digest — constructed by the handler, never by the agent |
| `sheet`, `location`, `poster`, `storyboard_batch` | Approve the record the director presented | the hashed record (asset ids, `look_ref`, `headshot_ref`, `prompt_recipe`…) |
| `pipeline_migration` | Pin (or change) the project's pipeline version | `{name, version, manifest_digest}` + `supersedes_receipt_id` |

Look-lock, reference-import and headshot receipts form supersession chains
per entity; only the newest link counts, and a `retire` on any of them
stales every checkpoint downstream of it (derived from the ledger, not from a
file you can edit).
