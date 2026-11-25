# Exact trailer cards

The cutting view can save intertitles and an end card alongside the picture and sound. The words belong to the writer. They remain exactly as entered, including spaces, accents, quotation marks and punctuation. The app does not correct, re-case, trim, wrap or shrink them. If a card is too wide or tall, the writer can add their own line breaks or change its letter size, then save and sign that version.

Save the cut before choosing Review and sign beside a card. The review shows its exact words, local lettering, font, size, background and timing. The existing signing screen records the writer's signature for that material. A card signature permits no spending and approves no production stage. Changing the words, font, size, background or timing needs fresh review before playback. An old signature does not clear a newer card. A later whole-cut approval must still bind the saved cut; delivery must still bind the exact final file.

Choose a clean sans-serif face, a clean serif face or the handwritten face beside each card. The supplied Source Sans, Source Serif and Patrick Hand files have full SIL Open Font Licences bundled with them. Those licences permit use in lettering and films. No system font or substitute font is chosen silently. A missing or changed font, a letter the font cannot show, or text beyond the safe area is refused. The safe area is the inner eighty percent of the picture in both directions. Text is white, centred, and can appear on black or over the picture. Cards cannot overlap each other or run past the cut. An end card finishes on the cut's last frame. These limits keep this a small trailer tool rather than a graphics application.

Preview words draws the card in its panel before signing. This is a lettering check, not an approval. Changing the words or face clears that preview until it is drawn again. On the signing screen, the original text is followed by a space guide that makes spaces, non-breaking spaces and line breaks visible. The guide's markers are not part of the card. Timing accepts seconds as well as whole frames, and shows the rounded result back to the writer.

The saved and signed characters stay exactly as entered. For drawing only, a letter followed by separate accent marks is composed into the font's single accented letter. If the combination cannot be composed or the face lacks that letter, the card is refused with the character named. No mark is dropped, substituted or drawn in an uncertain position. A non-breaking space draws as an ordinary space while remaining a distinct character in the signed text.

Each save is another numbered edit revision. The edit records the exact text, a separate fingerprint of its original bytes, the font file and its fingerprint, letter size, presentation and whole-frame timing. The extra text fingerprint matters because the shared signing format treats some equivalent Unicode spellings alike. Here, changing those original bytes still needs a fresh signature. Earlier revisions remain in history. Saving, preparing playback and signing a card never finish a stage automatically.

The existing local title tool makes the lettering from the font file. Remotion places that lettering on the saved frames. FFmpeg combines it with the cutting view's picture and layered sound. No model generates the lettering, no service is called, and no poster generation is required for this rough preview. Existing approved poster files are untouched, and their final pinned requirements still apply.

The preview remains a small local check rather than a final release export. There are no animated titles, font imports, general effects, colour finishing or new stage-signing controls here. The later export work must use the same saved text, font and timing, recheck the signatures and verify its actual output.

Right-to-left lettering remains for later work. The current faces refuse the tested Hebrew and Arabic characters; adding a face with those characters will require an explicit layout check before it can be offered. This piece does not claim right-to-left support.

## For the tools

The font file is ink-theater/assets/patrickhand.ttf, with the full licence in ink-theater/assets/OFL.txt. Its SHA-256 is 0f173b3e6cb6d1af25babf7f0057c5ac4ee11f9992b0469bb817e967ef4ad0fc. The font is used unchanged. The licence does not impose its terms on documents made with the font.

The card gate kind is trailer_card in the existing lib/receipts.py and scripts/gate_approve.py machinery. Card material lives only in timeline.cards in the existing edit_decisions record. The stored text_utf8_sha256 binds exact UTF-8 bytes independently of canonical JSON normalization. Card approval reconstruction reads the saved edit and checks it again before committing a receipt.

The local preview is 640 by 360 pixels at 24 fps. Safe bounds are x from 64 through 576 and y from 36 through 324. Letter size is twelve through seventy-two pixels on that canvas; no automatic size change occurs. The preview does not establish final-resolution typography. Remotion modules normally come from remotion-composer/node_modules; FRONTLOT_REMOTION_MODULES can point to an existing installation. FRONTLOT_REMOTION_BROWSER can name an installed local browser. No runtime is downloaded. Bundles and outputs stay under the production's local cut-previews directory.
