# Video take approval card

Date: 2026-10-20. Status: plan, waiting for Codex review. Nothing built yet.

## The job

Front Lot makes video takes for supervised shots but gives Ben no way to judge them in the app.
Image requests get a card under **Needs you**; video takes get nothing. Today a take is chosen only
when Front Lot's Claude runs `shot_select` for him. Ben wants to watch each take and press approve or
decline himself.

Real case: the test film's stopped continuity-test shot has held one unreviewed take since
2026-10-09. A stopped shot's takes still need a decision, so stopped shots get cards too.

## What Ben gets

1. Every shot with a take waiting on a decision shows one card under **Needs you**, in the same list
   and style as the image requests, and counts toward the Needs you number.
2. Opening the card puts the take in the viewer: a real `<video>` with play, pause and scrub. It
   always plays the entire original take file, across its full duration. Approval playback is never
   limited to a trimmed section; trimming for later use is separate and outside this card.
3. Under each take: **Approve** and **Decline**, an optional note box. Approve makes it the shot's
   chosen take. Decline sets it aside. Both only work from Ben's press in the app window.
4. A shot with several waiting takes shows them side by side, each playing on its own, each with its
   own buttons. If the shot already has a chosen take, it sits at the end labelled "Chosen now", for
   comparison, without buttons.
5. The card shows, in plain words: the shot's name; what the shot was asked for (the brief's
   direction, first sentence with the rest a tap away); what each take cost; and, when it applies,
   "Something this shot was briefed from has changed since this take was made: <document or look
   name>." No hashes, paths or ids.

## Rules

- **Waiting** means a take that is not declined, is not the chosen take, and was made after the
  shot's latest approval (or the shot has none). So approving one take settles the whole card: the
  other takes on it are passed over, and the card says so before the press ("Approving this take
  sets the other N aside"). A new take made later reopens the card.
- Declining the last waiting take closes the card without choosing anything.
- The decision records Ben's note, or a fixed note when he leaves it blank ("Approved on the take
  card." / "Declined on the take card."), because the shot record requires a note.

## Build

### Shot record (`lib/supervised_production.py`)

- Add `decline(root, shot_id, take_id, *, user_note)`: refuses an unknown take, appends
  `{'kind': 'decline', 'take_id', 'user_note'}`. No decision-log entry (nothing was chosen).
- Add `waiting_takes(root, shot_id)` implementing the rule above, so the board and Claude's status
  read one definition.
- `select` is unchanged and is what Approve calls.
- `_source_warnings` keeps returning its current strings for `inspect`; add a structured twin
  (`kind: source|look`, `name`) the board turns into plain words. Source name = the file's title
  from its first heading, else its file name without extension; look name = the cast/place name
  from the roster.

### Board data (`backlot/state.py`, `_shot_entry`)

- Each take gains `declined`, `waiting`, and `source_changes` (structured, from the take's own brief
  revision). Shot gains `waiting_count`.
- `shot_state` gains `waiting` (any waiting take); `has_takes` stays for takes all decided.
- Source checks hash the brief's source files on every state build. Cache by (path, size, mtime) so
  a busy film folder doesn't re-hash unchanged files.

### Ben's buttons (`backlot/server.py`)

- New `POST /api/project/{project_id}/shot/{shot_id}/take/{take_id}/decision`, body
  `{"decision": "approve"|"decline", "note": "..."}`.
- Accepted only with the app's capability token in a request header and an Origin matching the
  server, the same two checks the signing socket uses (`backlot/tty.py:_tty_websocket`). Front Lot's
  Claude can't read the token (its sandbox denies the metadata folder, `claude_settings.py`), so it
  can't press these.
- Refuses with a plain reason when the take is no longer waiting (409), when its file changed on
  disk (`select` already checks), or the project/shot/take is unknown (404). Note capped at 2,000
  characters.
- On success the board's live refresh redraws; the card disappears or shrinks.

### Front Lot's Claude (`backlot/claude_ops.py`, `claude_settings.py`)

- Remove `shot_select`. **Job removed:** Claude choosing a take for Ben. **New owner:** Ben, on the
  card. Claude can still make, attach, inspect and stop shots, and its instructions say: "When a take
  is ready, tell Ben it's waiting under Needs you. Only Ben approves or declines takes."
  (D1 below: Ben's call.)
- `shot_inspect` output includes which takes are waiting, so Claude can say so.

### Screen (`backlot/ui/board.js`, `board.css`)

- `renderLog`: list shot cards in Needs you whether or not the film has image requests (today the
  whole list hides when `hasAuthoredGates` is false). Card row: "Video take", the shot's first
  sentence, "N takes to review".
- New `renderTakeReview(s, shot)` in the viewer: header (shot name, direction), source-change alert
  when any, then a grid of waiting takes (one column at phone width, two or three across on the app
  window). Each tile: video with controls, "Take N · $0.56 · 5 seconds · made Oct 9", note box,
  Approve, Decline. Buttons disable while a press is in flight; a refusal shows its reason on the
  tile.
- Keep the existing per-take `<video>` reuse so a live refresh never restarts a playing take; extend
  it from one node to one per take id.
- Plain words only. The tape reads "Needs you" while waiting.

## Tests

- `tests/tools/test_supervised_production.py`: decline; waiting rule (none, one, several, after an
  approval, after a decline, stopped shot keeps its waiting take, new take after approval reopens).
- `tests/backlot/test_shots_state.py`: new take fields and shot state; source change shown by name.
- `tests/backlot/test_server.py`: decision endpoint refuses without token, with a foreign Origin, on
  a decided take, on a changed file; approve writes a selection, decline writes a decline.
- `tests/backlot/test_claude_ops.py`: `shot_select` is gone.
- `tests/backlot/test_ui_shots.py` (Playwright, fake films): card appears in Needs you on a film
  with no image requests; video plays and seeks; approve clears the card; decline with a note;
  three takes side by side; changed-source wording.

## Proof before calling it done

1. Copy the real test film to a scratch folder, start Front Lot on the copy, and walk the stopped
   continuity shot's card in the app window: play, scrub, decline with a note, then on a second copy
   approve. Screenshots of each.
2. After merge, restart the live server on port 4750 and Front Lot's Claude sessions, open the real
   film, and show Ben the card waiting for him. The real decision is Ben's; Claude does not press it.

## Decisions (Ben said yes to both, 2026-10-20)

- **D1.** Remove Claude's ability to choose a take. Claude still tells him when a take is waiting.
- **D2.** Approving one take passes over the other waiting takes on that shot, so unchosen takes
  don't pile up as open work.

## Out of scope

Trimming a take to a section (`select --start/--end` stays a Claude command), re-generating from the
card, and the wider "Front Lot as a real making app" direction. Those keep their current owners:
trims and re-generation stay with Front Lot's Claude at Ben's direction.
