# Front Lot status

Last rewritten: 2026-10-21. Rewrite this page in place when work lands; don't add to the bottom.

Front Lot is the film-making app in this repository: it turns approved canon into pictures
and shots.

## Live

Front Lot runs on this Mac (port 4750) from the `authored-film` branch.

- **The Cutting Room:** the app's design. Cast and places on the left, a large viewer with a
  frame strip in the middle, and a column for what needs you, the log, and signing.
- **Front Lot's own Claude, inside the app.** It can do what you can, shows a spend card before
  anything costs money, and asks before it sends, signs or deletes anything through your
  connected apps.
- **Reads today's canon** from Story-drive and finds each film's WriterOS project on its own.
  When the two disagree, it says what to do next.
- **Looks:** you sign with buttons on a signing screen. Signing a WriterOS look closes its
  appeal. Paid work waits while a canon appeal is open. A change upstream is a heads-up at
  signing, not a block.
- **Cast:** the approved trailer cast can be changed with a signed receipt. Front Lot lists and
  talks about the trailer cast only.
- **Location pictures** come from your own images: add them, then one signature per place.
- **Supervised shots:** each shot is a conversation with a saved brief and a spending limit.
  Each generate is exactly one paid call, with no automatic retries. Takes play in the viewer.
- **Video take approval:** each new video take waits under Needs you. You watch the full take
  and approve or decline it there, with an optional note. Approving picks it for the shot.
  Front Lot's Claude no longer picks takes for you.
- **Status check** is free and always available.

## Open

- Front Lot's Claude sometimes needs a page reload before its reply shows. Not yet traced;
  logging is in place, so note the time when it happens.
- The one-signature flow for location pictures hasn't been used for real yet.
- Some steps still need Claude to run a command for you. The goal is a full app you run
  yourself, with no terminal. Not scheduled.
- Set aside on purpose, not abandoned: the location-generation run and the shot-ready rule.
  Their branches stay.
- Two pull requests are open. #2 is a narrow review copy marked "do not merge". #1 proposes
  the whole pipeline into the repository's `main` branch; no decision has been made on it.

## Next

Nothing is scheduled. The stated direction is the full app above: promote in WriterOS,
then carry on in Front Lot yourself.
