# Backlot Board — A Plain-Language Guide

Backlot is a web page that shows you what your film production is doing,
live, in your browser. Think of it as a window into the project folder:
the pipeline writes files as it works, and the board reads those files and
draws them as a picture. You watch; the board never changes anything.

## Opening the board

The easiest way: **ask Claude.** Say "open the board" (or "open the board
for bloodless") in any Claude session in this project, and it will start
the board and open your browser to the right page. Claude also opens the
board automatically at the start of every production run.

If you ever want to do it yourself, open the Terminal app and run:

```bash
cd ~/Projects/OpenMontage
.venv/bin/python -m backlot open              # the library (all projects)
.venv/bin/python -m backlot open bloodless    # one project's board
```

The first line moves into the OpenMontage folder; the second starts the
board server if it isn't already running and opens your browser. If the
browser doesn't open, the command prints the address — copy it into your
browser yourself.

The board runs only on your own machine. Nothing is on the internet.

## What you're looking at

**The stage rail** — the row of pipeline stages (script, scene plan,
assets, and so on). Each one lights up as the production reaches it, so
you can see at a glance how far along the run is.

**The script card** — the screenplay. Click it to read the full script in
a pop-up.

**The filmstrip** — the scene plan, one card per scene, filling in with
images and clips as they're generated. A shimmer on a card means that
scene is being generated right now.

**The Gates panel** — the approval queue. Anything the pipeline is not
allowed to do without your sign-off shows up here as a "gate request":
approving the project budget, locking in a character's look, accepting a
headshot, and so on. Pending requests (waiting on you) are at the top;
everything already answered is in the archive below.

**The cost meter** — how much money the run has spent so far.

**The activity feed** — which tools are running at this moment.

Clicking anything opens a closer look. None of it changes the project.

## Answering a gate (the one interactive part)

When the pipeline needs your approval, a request appears in the Gates
panel. The routine:

1. **Click the request.** You'll see the evidence: the images, config, or
   sheet being judged, what it costs, and what approving it means.
2. **Look at the evidence.** This is the whole point of the board — you
   judge with your eyes before you sign.
3. **Press "Start signing."** A small terminal opens inside the page and
   runs the real signing program. It asks you questions one at a time —
   approve or decline, pick a candidate, give a reason. Type your answers
   and press Enter.

That's it. The terminal on the board is the exact same signing program
you could run in the Terminal app — the request detail shows the command
if you'd rather do it there. Same questions, same result, your choice.

A few rules the signer enforces so you don't have to remember them:

- Only one signing session can be open per project at a time.
- You can't sign while the pipeline is actively running that project —
  finish or stop the run first.
- Nothing answers for you. If you walk away, close the page, or the
  server stops, the signer just waits. It never assumes a "yes."

## What the board can NOT do

- It can't start a production run. Runs are started from a Claude
  session, and the board watches.
- It can't approve anything by itself. There is no Approve button on the
  page — approval only happens by you typing answers into the signer.
- It can't edit, delete, or generate anything. It only reads.

So you can click around freely. The worst you can do on the board is
open a signing session and answer its questions.

## If something looks wrong

- **Page won't load** — the server probably isn't running. Ask Claude to
  open the board again (or re-run the Terminal command above).
- **A gate shows "packet_error" or "hash mismatch"** — a file the request
  refers to is missing or has changed since the request was made. Don't
  approve it; ask your Claude session to investigate.
- **Rows marked "unverified"** — normal for archived items. It means the
  board is showing you the raw record without re-checking signatures;
  the real enforcement lives in the signer, not the board.

For the technical version of all this, see `backlot/README.md`.
