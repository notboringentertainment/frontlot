# Signed footage requests

This work adds signed trailer footage operations to the existing shot notebook
and video transport. It does not introduce a second footage system. Every paid
request is for one clip, with its exact shot, references, approved look, model,
length, signed price figures and remaining ceiling. Accepting a take never
completes a stage.

The work remains in progress. Eleven concrete findings across the footage work
have required corrections: the original spending gap, unsigned decisions treated
as authority, older shots disappearing, an early reservation gap, a stale card
which could not refresh its remaining ceiling, and an unsigned reconciliation
path, separate identities for identical retakes, reattachment after a decline,
honest accounting when the service returns no invoice, preserving later trim
windows, and rejecting edited take metadata. The proportionality decision is to finish the bounded operations and their
readable signing cards, retaining the financial proofs. A full request workbench
and automatic dispatch are deferred. The existing signer and transport remain
the foundation.

The normal service response supplies a clip rather than an invoice. The app
therefore books the entire signed maximum against the ceiling, and says that
it is a budget booking. It never presents this as a confirmed invoice. An
explicit charge report is accepted only within both signed cash and credit
bounds. Missing parts of a charge report, an overcharge or an uncertain outcome
keep the reservation open for a signed account review.

Accepting a take keeps existing source trims, sound and card timing. A take
which is too short for those trims cannot be inserted. The old edit remains
in the same saved version history. A signed decline is final across the whole
history. Restoring an edit cannot reactivate that take: its placement is set
aside, and the current sequence shows a placeholder with a reason.

This half has operations and readable signing cards, not a complete footage
workbench. Preparing a shot brief, preparing its request, dispatching an
approved request and applying a signed take decision remain operations for
the app to call. The disabled shot-card buttons say so. A prepared request can
be reviewed and signed with the existing board signer. Story and look approval
remain outside these panels. Real timed playback and export remain later work.

The board's trailer label is display evidence. A paid request separately proves
that the production has verified signed trailer creation and current signed
settings. This work leaves the accepted display limitation for completely
concealed, unverifiable trailer files unchanged.

## For the tools

The operations live in `lib/footage_requests.py`. The transport remains
`tools/video/kling_reference_video.py`; the shared reservation journal and signed
account resolution are reused. An in-process scoped authorization is created
only after verifying a saved `footage_spend` receipt. Tool input flags cannot
create that authorization. This document will record the final operation
interfaces and limitations when verification is complete.
