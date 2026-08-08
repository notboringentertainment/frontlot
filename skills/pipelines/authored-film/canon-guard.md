# Canon Guard — Authored-Film Pipeline

The binding contract every authored-film stage operates under. Read this before
any stage director skill in this pipeline. It is short because it is absolute.

## The One Rule

**The writer already wrote the story. You are producing it, not improving it.**

The canon packet is the authoritative story source. Its `locks[]` are immutable
within a production run — treat them exactly like `human_approval_default: true`
in a manifest: binding, never re-judged, enforced mechanically.

## Authority Ladder

When sources disagree, higher authority wins. Never resolve a same-level
conflict yourself — escalate it (see Collisions below).

1. **Canon atoms** (`authority: canon`) — ratified in the writers' room, often
   evidence-hashed. Highest story authority.
2. **Locked decisions** (`authority: locked_decision`) — wayfinder resolved
   tickets / MAP "Decisions so far", continuity log rows marked locked, producer
   rulings, pitch-export departures declarations.
3. **The writing documents** (synopsis / treatment / outline / story bible
   prose) — authoritative where no lock speaks.
4. **Groundwork** (`authority: groundwork`) — research summaries, homework,
   sketches. NEVER story authority. A sketch headed "DISPOSABLE, NOT CANON"
   means exactly that.
5. **Reference** — tone/visual references. Shapes the look, never the story.

## Hard Constraints

- **Protected lines are verbatim.** Character-for-character, punctuation
  included. Paraphrasing a protected line is a CRITICAL violation, same class as
  a silent renderer swap.
- **Never invent canon.** A gap in the material (missing ending, unnamed minor
  character, undefined world rule) becomes an `open_questions[]` entry surfaced
  at the next human gate — never a value you fill in. The writer's own
  convention for this is `[NEEDS DECISION: …]`; use their phrasing when
  presenting.
- **Never answer an open question yourself.** Only the writer locks story
  decisions. This mirrors the writer's own development rule that only
  human-in-the-loop work can lock canon.
- **`never_write_as` is a hard filter**, not a suggestion. If a character's
  entry says never write them as X, any script line, scene description, or asset
  prompt that reads as X fails review.
- **Tone document governs feel.** `must_never_feel_like` is a review criterion
  at every gate from proposal to final_review.

### Runtime backstop (validation_profile: authored-canon)

These constraints are not prose-only. `lib/checkpoint.py` +
`lib/canon_enforcement.py` enforce the structural half at every checkpoint
write: manifest `produces` / `required_artifacts_in` are binding; blocking
open questions without a matching `canon_ruling` block completion; script
sections need canon `source_ref`s and verbatim protected lines; scenes need
`canon_refs`; visual assets need `continuity` evidence; compose needs a
passing `canon_pass` and a real, ffprobe-valid render inside the project
workspace. A rejected checkpoint persists nothing — not even decision-log
merges. Semantic judgment (tone, feel, voice) remains yours and the writer's.

## Collisions

When a stage's natural output conflicts with a lock — the reviewer wants a
different ending, the treatment runtime can't fit all beats, a generated asset
reads against a continuity fact:

1. **Stop. Never silently pick a side.** The cinematic pipeline's re-log rule
   for changed decisions applies doubled here.
2. Name both sides: the lock (id + decision, quoted) and the production pressure
   against it.
3. Write the checkpoint `awaiting_human` with the conflict in the artifact
   summary and END YOUR TURN. The writer rules; their ruling goes in the
   `decision_log` with `category: "canon_ruling"` (and `question_id` when it
   answers a canon packet open question — that id match is what releases a
   blocking question at the runtime level).
4. If the writer changes a lock, that is THEIR reopening — record the
   superseded decision in the decision log; suggest they mirror it back into
   their development system (wayfinder reopening procedure / new atom) so the
   canon stays one thing everywhere.

## Provenance

Every artifact this pipeline produces traces back: script sections carry
`source_ref` → canon beat/lock ids; scene descriptions cite continuity facts;
asset prompts record which references and risk notes were applied. The writer
must always be able to ask "where did this come from?" and get a file path.
