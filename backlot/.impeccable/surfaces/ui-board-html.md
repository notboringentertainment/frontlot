---
version: 1
slug: "ui-board-html"
primary_target: "ui/board.html"
related_targets: ["ui/index.html"]
---

## Scope

Front Lot film board (`backlot/ui/board.html`) and the library (`backlot/ui/index.html`). Visitor mode: Operate. Ben, sole user, long making sittings plus quick check-ins. Images take most of the screen; the Claude session sits beside them as a narrower column. Must not feel like a dev tool, a generic app, or busy.

Phase 1 (this build): the new world plus the session column's place in the layout (needs-you queue, log, signing). Phase 2: a real Claude Code session in that column, modelled on Story-drive's live mod; own app window.

## Direction contract

THESIS: Front Lot is a flatbed editing table, not a dashboard. One big viewer holds the picture being judged; everything else serves that act. Refuses the category default of a grid of equal dark panels with tiny mono labels.

OWN-WORLD: Machine enamel grey-green frame (#3d433c family), a dim neutral grey viewer surround (#2f2f2e) so colour judgement is honest; dim rather than 18% mid-grey because Ben confirmed long sittings, and a bright surround tires the eye, warm off-white grease-pencil ink for marks. Leader-tape colours are the only state vocabulary: green = signed/canon, red = declined, white = in progress, and yellow grease pencil reserved solely for "needs you now". Workhorse sans for reading, a condensed machine-plate face for engraved labels; no monospace in the main view, no hashes.

STORY: Ben opens a film, sees at once what needs him (yellow), picks a character or place from the trim bin, judges it big in the viewer, reads the assistant editor's log beside it, signs when ready.

FIRST VIEWPORT: Top machine bar: film title as a plate, stage counter, spend meter. Left trim bin (~240px): roster of every character and location, each a hanging clip with three slots (look, headshot, sheet). Centre: the viewer, image at maximum size, with the frame strip of that entity's images beneath. Right (~380px): the log column; needs-you cards at top, log below, signing bay and later the Claude session docked here.

FORM: The Cutting Room, candidate 7 of 7 on the grounded list; seed key 391d43a6. Raises: scale-only hierarchy; fixed roster slots; spend/sign actions isolated by space; one reserved "needs you" colour; one dominant thing per screen.

SIGNATURE: Selecting a frame threads it into the viewer: the strip advances and the frame registers with a short sprocket-pull motion; signed frames carry a green leader tab, waiting ones a yellow grease mark.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
