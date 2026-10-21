---
name: Front Lot
description: A flatbed editing table for judging and signing a film's pictures.
colors:
  enamel: "#3a403a"
  enamel-2: "#434a43"
  enamel-3: "#4d554c"
  seam: "#5a6358"
  seam-soft: "#485047"
  surround: "#2f2f2e"
  glass: "#1d1d1c"
  glass-edge: "#3c3c3a"
  ink: "#efece4"
  ink-2: "#c4c3b8"
  ink-3: "#b8bdb0"
  ink-on-glass-3: "#aeaea6"
  tape-green: "#74b46c"
  tape-green-ink: "#10200e"
  tape-red: "#d65d4e"
  tape-red-ink: "#2a0b07"
  tape-white: "#e6e3d8"
  tape-white-ink: "#22211d"
  grease: "#f1cb3c"
  grease-ink: "#241f08"
  script-paper: "#f1eee5"
  script-ink-2: "#6c695e"
typography:
  display:
    fontFamily: "Barlow Condensed, Avenir Next Condensed, Arial Narrow, sans-serif"
    fontSize: "44px"
    fontWeight: 700
    lineHeight: 0.95
    letterSpacing: "0.01em"
  headline:
    fontFamily: "Barlow Condensed, Avenir Next Condensed, Arial Narrow, sans-serif"
    fontSize: "26px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.02em"
  title:
    fontFamily: "Barlow Condensed, Avenir Next Condensed, Arial Narrow, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.06em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Helvetica Neue, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  label:
    fontFamily: "Barlow Condensed, Avenir Next Condensed, Arial Narrow, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.14em"
  screenplay:
    fontFamily: "Courier Prime, Courier New, monospace"
    fontSize: "14.5px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  tab: "1px"
  tape: "2px"
  control: "3px"
  screen: "4px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "18px"
  lg: "26px"
  xl: "34px"
components:
  button-sign:
    backgroundColor: "{colors.grease}"
    textColor: "{colors.grease-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "15px 26px 14px"
  button-sign-disabled:
    backgroundColor: "{colors.enamel-2}"
    textColor: "{colors.ink-3}"
  button-quiet:
    backgroundColor: "{colors.enamel-2}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.tape}"
    padding: "8px 12px"
  button-quiet-hover:
    backgroundColor: "{colors.enamel-3}"
    textColor: "{colors.ink}"
  tape-done:
    backgroundColor: "{colors.tape-green}"
    textColor: "{colors.tape-green-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.tape}"
    padding: "6px 9px 5px"
  tape-waiting:
    backgroundColor: "{colors.grease}"
    textColor: "{colors.grease-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.tape}"
    padding: "6px 9px 5px"
  tape-declined:
    backgroundColor: "{colors.tape-red}"
    textColor: "{colors.tape-red-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.tape}"
    padding: "6px 9px 5px"
  tape-running:
    backgroundColor: "{colors.tape-white}"
    textColor: "{colors.tape-white-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.tape}"
    padding: "6px 9px 5px"
  need-card:
    backgroundColor: "{colors.enamel-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "14px 16px 15px"
  need-card-hover:
    backgroundColor: "{colors.enamel-3}"
  clip:
    textColor: "{colors.ink-2}"
    padding: "9px 18px 9px 14px"
  clip-current:
    backgroundColor: "{colors.enamel-3}"
    textColor: "{colors.ink}"
  screen:
    backgroundColor: "{colors.glass}"
    rounded: "{rounded.screen}"
  library-card:
    backgroundColor: "{colors.enamel-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.screen}"
  script-card:
    backgroundColor: "{colors.script-paper}"
    textColor: "{colors.tape-white-ink}"
    typography: "{typography.screenplay}"
    rounded: "{rounded.tape}"
    padding: "34px 44px 30px"
---

# Design System: Front Lot

## Overview

**Creative North Star: "The Cutting Room"**

Front Lot is a flatbed editing table, not a dashboard. One table runs the full window: a machine bar across the top, the trim bin on the left, a large viewer in the middle, and the log on the right. The picture being judged is the main thing on screen. Everything else is machine enamel, viewer glass, and grease pencil, and stays dim so the picture holds the colour.

The world is built from three materials. The table is grey-green machine enamel, stepped in three tones. The viewer is neutral, dim glass, so colour judgement stays honest over long sittings. The marks are warm off-white grease-pencil ink. State is spoken only in leader-tape colours (green signed, red declined, white in progress), and yellow grease pencil is held back for one meaning: this needs you now. Labels are engraved plates set in a condensed face. Reading text uses the system workhorse sans. Hashes, IDs and monospace stay out of the main view.

Density is calm. Hierarchy comes from scale: one big title plate and one big picture per screen, small engraved labels around them. Signing and spending are kept apart from the rest by space and by the yellow.

**Key Characteristics:**
- Three-column table (bin, viewer, log) under a 64px machine bar; the viewer takes all remaining width.
- Neutral glass behind every image; enamel is never placed directly behind a picture.
- Four leader-tape state colours, and yellow means only "needs you".
- Condensed, uppercase, tracked plate labels; sans for anything you read as a sentence.
- Edges are drawn as 1px inset hairlines, not borders; drop shadows are deep, soft and rare.
- Small radii throughout (1 to 4px): machined, not friendly.

## Colors

Muted grey-green enamel and neutral dark glass, with saturated colour only on the leader tapes and in the pictures.

### Primary
- **Yellow Grease Pencil** (grease): the "needs you now" mark. Used on the signing button, the top edge of needs-you cards, waiting slot tabs, the stage counter tick for an awaiting stage, the "needs" status lamp, and the "needs you" badge on library posters. Its text partner is **Grease Ink** (grease-ink), a near-black olive.

### Secondary
- **Signed Leader Green** (tape-green): signed or canon. Done slots, done stage ticks, done frame leaders, done tapes, selected approval items. Text on it uses **Green Tape Ink** (tape-green-ink).
- **Declined Leader Red** (tape-red): declined, failed, missing or critical. Text on it uses **Red Tape Ink** (tape-red-ink). The same red at 12% fill with a 45% inset hairline, with pale rose text (`#f0c2bb`), makes the alert box.
- **Running Leader White** (tape-white): in progress. Active stage tick, running tape, the running status lamp, the "live" poster badge. Text on it uses **White Tape Ink** (tape-white-ink).

### Neutral
- **Machine Enamel** (enamel): the table body. Page, trim bin and log backgrounds.
- **Raised Enamel** (enamel-2): the machine bar, needs-you cards, quiet buttons, library cards, the docked signing panel.
- **Worn Enamel** (enamel-3): hover and current-selection fill on enamel (current clip, selected counter stage, hovered card).
- **Seam** (seam) and **Soft Seam** (seam-soft): structural dividers and inset hairlines on enamel; soft seam for dividers inside a column and empty ticks.
- **Viewer Surround** (surround): the viewer column's background, a dim neutral grey.
- **Viewer Glass** (glass): behind every picture, thumbnail, code block and drawer; also the stage counter's sunken well.
- **Glass Edge** (glass-edge): the hairline around anything sitting on glass.
- **Grease-Pencil Ink** (ink): primary text, focus ring, current-frame outline.
- **Faded Ink** (ink-2): secondary text and body copy in the viewer and log.
- **Engraving Ink** (ink-3): plate labels and metadata on enamel.
- **Glass Engraving Ink** (ink-on-glass-3): the same role as ink-3 when the ground is the neutral surround or glass.
- **Script Paper** (script-paper) and **Script Margin Ink** (script-ink-2): only for the screenplay page insert and text cards, the one light surface in the world.

### Named Rules
**The Reserved Yellow Rule.** Grease yellow means "this needs you now" and nothing else. If something does not need Ben's decision, it does not get yellow, however important it is.

**The Leader Tape Rule.** State has four colours: green signed, red declined, white running, yellow waiting. A new state maps onto one of these or stays neutral seam grey. No blues, purples or extra hues for status.

**The Honest Glass Rule.** Pictures sit on neutral glass (glass or surround), never on the green enamel, so the enamel cast never skews colour judgement.

## Typography

**Display Font:** Barlow Condensed, 500/600/700, self-hosted from `ui/vendor/fonts` (with Avenir Next Condensed, Arial Narrow)
**Body Font:** the system sans (-apple-system, SF Pro Text, Helvetica Neue)
**Screenplay Font:** Courier Prime (with Courier New), only inside the script page insert

**Character:** an engraved machine-plate face paired with a quiet system sans. The condensed face carries names and labels like stamped metal; the sans carries everything read as a sentence.

### Hierarchy
- **Display** (700, 44px, 0.95, uppercase): the viewer's subject name (a character, a place, a stage). The library's featured film title goes to 52px; the library page title is 40px. Long names drop to 600, 30px, sentence case.
- **Headline** (700, 26px, 1, uppercase): the film title plate in the machine bar; 30px for an approval review heading.
- **Title** (700, 22px, 0.06em, uppercase): section titles inside the viewer (storyboard, renders); 24px for library card titles; 20px for drawer heads.
- **Body** (400, 15px, 1.5, tabular figures): all running text. Log entries and notes run 14px; notes cap at 68ch, sign-bay copy at 60ch.
- **Label** (600, 12 to 15px, 0.08 to 0.14em, uppercase): plate labels, stage names, tapes, frame captions, log section heads (700, 15px), disclosure summaries.
- **Screenplay** (400, 14.5px, 1.55): screenplay formatting on the paper insert only.

### Named Rules
**The Engraved Plate Rule.** Barlow Condensed is always uppercase and tracked (0.02em at display sizes, 0.08 to 0.14em at label sizes). Sentences are never set in it.

**The No Machine Lines Rule.** Monospace never appears in the main view. Raw packet text and commands appear only inside a collapsed "more" disclosure or a drawer, set in the system mono on glass.

## Layout

The board is a CSS grid table: a 64px machine bar across the top, then the trim bin (252px), the viewer (fluid), and the log (392px). When a film has no roster the bin column drops out; when there is no log the viewer takes everything. The viewer's content caps at 1400px, padded 26px 34px. The screen (main image) fills the height that is left: `clamp(320px, 100dvh - bar - 330px, 860px)`, with the picture `object-fit: contain`. A frame strip of 168 by 112px frames runs under it, then notes in a 1.4fr/1fr two-column split, then the sign bay, set apart by 40px and a glass-edge rule.

Rhythm runs in steps of about 6, 10, 18, 26 and 34px: 18px column insets in the bin, 20px in the log, 26 to 34px between viewer sections.

Responsive behaviour: at 1280px the columns narrow (bin 220px, log 340px) and the notes stack. At 1060px the bin becomes a horizontal strip under the bar, with its group labels turned vertical. At 760px the table becomes a single scrolling column with a sticky bar, the stage counter wraps to its own full-width row, and the screen switches to 4:3.

The library ("Your films") uses the same bar over a 1320px auto-fill grid of cards (at least 280px, three across). The film that needs Ben, or the latest one, leads as a full-width featured card split 1.6fr/1fr.

The log column is the session's place. Today it holds the needs-you queue, the log, two disclosures, and the signing panel docked at its foot. **Open (phase 2):** an embedded Claude Code session will live in this column. It is not built yet, and its visual treatment is not recorded here.

## Elevation & Depth

Mostly flat and tonal. Depth comes from enamel stepping lighter (enamel to enamel-3) and from glass sinking darker. Edges are 1px inset hairlines (`inset 0 0 0 1px` in seam or glass-edge) rather than CSS borders. Real drop shadows are deep, soft and negatively spread, and appear on only a few surfaces: the main screen, the script page, library cards, the machine bar and the signing button.

### Shadow Vocabulary
- **Hairline edge** (`box-shadow: inset 0 0 0 1px var(--seam)` on enamel, `var(--glass-edge)` on glass): the default way to draw an edge.
- **Screen drop** (`box-shadow: 0 0 0 1px var(--glass-edge), 0 24px 60px -30px rgba(0,0,0,.9)`): the main viewer screen.
- **Paper drop** (`box-shadow: 0 24px 50px -28px rgba(0,0,0,.9)`): the screenplay page insert.
- **Card drop** (`box-shadow: inset 0 0 0 1px var(--seam), 0 18px 40px -26px rgba(0,0,0,.8)`): library cards; on hover it deepens and lifts 3px.
- **Bar ledge** (`box-shadow: 0 1px 0 rgba(255,255,255,.04) inset, 0 6px 18px -10px rgba(0,0,0,.6)`): the machine bar over the table.
- **Sunken well** (`box-shadow: 0 1px 0 rgba(255,255,255,.05), inset 0 1px 3px rgba(0,0,0,.7)`): the stage counter.
- **Key cap** (`box-shadow: 0 1px 0 rgba(255,255,255,.35) inset, 0 6px 14px -8px rgba(0,0,0,.7)`): the yellow signing button.

### Named Rules
**The Hairline Not Border Rule.** Draw edges as inset 1px hairlines in seam (on enamel) or glass-edge (on glass). Drop shadows are soft, negatively spread, and kept for the screen, paper, cards and the signing key.

## Shapes

Machined, nearly square corners in four steps: 1px for slot tabs, log marks and take thumbnails; 2px for tapes, chips, frame thumbnails and quiet buttons; 3px for the signing button, needs-you cards, drawers and panels; 4px for the main screen, the stage counter and library cards. Nothing is pill-shaped except status lamps (8px circles). The recurring silhouette is the leader tape: a thin coloured bar (3 to 6px) at the edge of a frame, the top of a card, or as a log mark.

## Components

### Buttons
- **Shape:** squared key (3px) for signing; 2px for quiet buttons.
- **Sign / Primary:** grease yellow with grease ink, Barlow Condensed 700 17px uppercase at 0.1em, padding 15px 26px 14px, key-cap shadow. It is the only yellow button, and it appears only where Ben signs or spends.
- **Hover / Focus:** brightness 1.06 and a 1px lift, eased over 0.2s; the focus ring is a 2px ink outline offset 2px.
- **Disabled:** goes flat to raised enamel with engraving ink and a seam hairline.
- **Quiet:** raised enamel, ink-2 13px sans, seam hairline; on hover the fill goes to worn enamel and the text to full ink. The replay button is a quiet variant on glass (glass-edge hairline, plate label type).

### Leader Tapes (state chips)
- **Style:** Barlow Condensed 600 12.5px uppercase at 0.1em, padding 6px 9px 5px, 2px corners.
- **State:** unfilled tapes are ink-2 text with a glass-edge hairline. Done, waiting, declined and running fill solid with their tape colour and its dark ink. The same vocabulary is used for slot tabs (6px tall, up to 34px wide), the stage-counter ticks (3px), frame leaders (5px left edge), log marks (6 by 20px) and the library mini-rail (4px).

### Cards / Containers
- **Corner Style:** 3px (panels, needs-you cards, drawers); 4px (library cards).
- **Background:** enamel-2 on the table; glass inside the viewer.
- **Shadow Strategy:** hairline edge; library cards add the card drop.
- **Internal Padding:** 14 to 16px for cards, 18 to 24px for drawers and review panels.

### Inputs / Fields
The board has almost no form fields. The replay scrubber is a native range input tinted with ink-2. Signing happens in the docked terminal panel: an xterm mount on glass with a glass-edge hairline and a 220px minimum height, under a heading plate and a state tape (ready = green, error = red, warning = worn enamel).

### Navigation
- **Machine bar:** raised enamel, 64px tall. From left: the maker mark (a drawn reel SVG) with the "Front Lot" plate, a seam-divided title plate, then pushed right the stage counter, the spend meter (700 22px figure over a 12px caption), and the status lamp.
- **Stage counter:** a sunken glass well of cells. Each cell is a 12px plate name over a 3px tick in its tape colour. Hover fills the cell with enamel; the selected stage takes worn enamel.
- **Trim bin:** grouped lists (Characters, Places) under plate labels. Each clip is a 44px square face on glass, the name in 600 14px sans, and three slot tabs (look, headshot, sheet). Hover goes to enamel-2, current to enamel-3. A clip with nothing made yet is quiet: 500 weight, ink-3, face at 55% opacity, initial shown in a plate letter.

### The Viewer and Frame Strip (signature)
The screen is glass with the screen drop, and the picture fills it as large as fits. Choosing a frame threads it in. The picture registers with a short sprocket pull: it drops in from 7% above at 55% brightness and settles with a 0.8% overshoot over 0.42s (`cubic-bezier(.2,.9,.25,1)`). Under the screen, frames sit in a dashed-rule strip. Each frame carries a 5px leader in its tape colour, lifts 2px on hover, and when current gets a 2px ink ring and a full-ink caption. A plate-label frame tag sits bottom-left on the screen over 78% dark glass.

### Needs-You Card
Raised enamel with a seam hairline and a 3px grease tape along the top edge, inset 16px from each side. It holds the request text (14.5px ink, clamped to three lines) and a "who" line in 13px. Hover goes to worn enamel; current gets a 2px ink inset ring. This is the only place grease yellow appears at card size.

### Log Entry
Grid of a 6 by 20px tape mark and text: 14px ink-2 text, with a 12.5px ink-3 small line under it. Entries are divided by soft-seam rules. Entries that can be opened are unstyled buttons that underline in seam on hover.

### Script Page Insert
The one light surface: script-paper with white-tape ink, Courier Prime, standard screenplay indents (action 42px, parentheticals 84px), the paper drop, and a status tape pinned top-right. Clicking opens it full-size in a modal over 86% dark glass.

## Do's and Don'ts

### Do:
- **Do** put every picture, thumbnail and preview on glass (glass) or the surround (surround), with a glass-edge hairline.
- **Do** say state only with the four leader-tape colours, as fills, ticks, leaders or marks, each with its paired dark ink for text.
- **Do** set names and labels in Barlow Condensed, uppercase and tracked; set sentences in the system sans at 14 to 15px.
- **Do** give each screen one dominant thing: the viewer's subject and its picture, or the library's featured film.
- **Do** keep signing and spending apart, behind 40px of space and a rule, with the yellow key as the only control there.
- **Do** honour reduced motion: all animation and transitions switch off.

### Don't:
- **Don't** use grease yellow for anything except "needs you now".
- **Don't** add status hues beyond green, red, white and yellow.
- **Don't** show hashes, IDs or monospace machine output in the main view; keep them inside a collapsed disclosure or drawer.
- **Don't** set sentence text in the condensed plate face, or plate labels in sentence case.
- **Don't** build the board as a grid of equal dark panels with tiny mono labels.
- **Don't** place pictures directly on the green enamel.
- **Do** keep engraving ink (ink-3, #b8bdb0) for small text on enamel and raised enamel; it clears 4.5:1 on both after the 2026-10-07 contrast raise.
