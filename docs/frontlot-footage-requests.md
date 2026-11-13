# Signed footage requests

This work adds signed trailer footage operations to the existing shot notebook
and video transport. It does not introduce a second footage system. Every paid
request is for one clip, with its exact shot, references, approved look, model,
length, signed price figures and remaining ceiling. Accepting a take never
completes a stage.

The bounded implementation is complete. Thirteen concrete findings across the footage work
have required corrections: the original spending gap, unsigned decisions treated
as authority, older shots disappearing, an early reservation gap, a stale card
which could not refresh its remaining ceiling, and an unsigned reconciliation
path, separate identities for identical retakes, reattachment after a decline,
honest accounting when the service returns no invoice, preserving later trim
windows, rejecting edited take metadata, and readable nested look facts on the
new signing card, and keeping account resolution exclusive while a purchase is
active. The proportionality decision is to finish the bounded operations and their
readable signing cards, retaining the financial proofs. A full request workbench
and automatic dispatch are deferred. The existing signer and transport remain
the foundation.

A final proportionality check found that repeated integrated test starts were
adding avoidable work. The remaining change was a browser test waiting for the
page to finish loading, rather than asserting before it had loaded. The decision
was to freeze the implementation after that correction, finish one complete
comparison and report its raw results. No additional interface work or recovery
mechanism was added.

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

The voice-and-music work is now on the main line and has been merged here.
Sound and footage use one shared purchase lock, budget calculation, price
comparison, signing-request writer, decision-history check and signed account
resolution. Their price calculations still describe their actual units: sound
is priced in credits, while footage is priced by clip length. Both must agree
with current signed figures before a call can begin.

This integration found one additional financial gap: the low-level sound
operation could accept a signed request containing only typed price limits.
It now checks signed figures every time. That brings the concrete finding
count to fourteen. The proportionality decision remains to finish the shared
operations and their evidence, without request controls, another workbench or
a compatibility project. The special local-audio exception for older
productions and the separate reservation ordering for older calls have been
removed. Tests now check the current refusal and the single crash-safe order.
The copied settings format has also been removed. The current sound settings
now hold footage figures alongside sound figures, without a separate version
or a conversion path.

## For the tools

The operations live in `lib/footage_requests.py`. The transport remains
`tools/video/kling_reference_video.py`; the shared reservation journal and signed
account resolution are reused. An in-process scoped authorization is created
only after verifying a saved `footage_spend` receipt. Tool input flags cannot
create that authorization. This document will record the final operation
interfaces below.

`prepare_request(root, shot_id, duration_seconds=..., model=..., retake_of=...)`
creates a free, immutable request. `request_approval(root, request_id)` creates
its existing board signing request. `execute(root, request_id)` dispatches the
verified one-use purchase. `request_decision(root, shot_id, take_id, decision)`
creates a signed accept/decline request, and `apply_decision(...)` applies its
verified decision to the single EDL. Preview/export consumers must use
`validate_cut_sources(root, edit)` and `require_accepted(...)`, never a notebook
label. `media_spending.request_resolution` and `resolve_charge` serve both media
families. The sound workspace imports those same operations for its current
callers. The shared implementation lives in `lib/media_spending.py`.
