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

## The one start command

Open Claude Code in the OpenMontage repo and say:

> Produce this with the authored-film pipeline.
> Sources: <path to your docs>, <path to wayfinder/>, <path to atoms/>.
> Project id: <kebab-case-title>.

That's the whole invocation. The agent must then (per AGENT_GUIDE): run
preflight and show you the real capability menu, read the stage director
skills, and open the first gate.

## What you'll actually experience — the five gates

You are the writer. The pipeline stops for you five times; each stop is a
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

**Gate 3 — Script.** The adaptation, with per-section provenance back to
your beat/lock ids, protected lines verbatim, any `[production line]` the
runtime needed to add, flagged for your veto, and any lock conflict presented as a
tension (never silently resolved).

**Gate 4 — Scene plan.** Scenes with your continuity facts embedded in the
descriptions, key sequences from your annex as hero moments.

**Gate 5 — Assets.** The generated/authored assets, scene by scene, with
continuity evidence (which references and risk mitigations were actually
applied) before compose locks them in.

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
questions, by construction.

## What the machine does vs. what you do

| You (writer) | The agent | The runtime (enforced) |
|---|---|---|
| Supply finished development assets | Reads them into the canon packet | Packet must be schema-valid, hashed, nothing invented |
| Approve gates, answer blocking questions | Presents gates, asks questions in your [NEEDS DECISION] convention | No gate skip; no unanswered blocking question passes |
| Rule on collisions | Escalates collisions, never resolves them | Unruled tensions cannot complete |
| Watch the film | Runs the canon pass against the real render | No canon pass = no completed film; line must be audible; file must exist |

## Keyless reality check

With no API keys, "asset generation" = authored Remotion/HyperFrames
composition + local TTS + ffmpeg-synthesized ambience. That produced a real
film in the shakedown — but it is animation, not generated photography.
For photographic frames, configure at least one image/video provider in
`.env` (the preflight menu lists exactly what each key unlocks).

## Where everything lands

```
projects/<your-project>/
├── artifacts/        canon_packet, proposal, script, scene_plan, manifest…
├── assets/           audio/, video/, images/, music/
├── composition/      atelier Remotion entry (if atelier mode)
├── renders/          the film
├── decision_log.json every ruling and choice, append-only
└── checkpoint_*.json the gate trail (history/ keeps superseded versions)
```

`python -m backlot open <project-id>` gives you the live board view of all
of it while the run is happening.
