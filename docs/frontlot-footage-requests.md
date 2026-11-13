# Signed footage requests

This work adds signed trailer footage operations to the existing shot notebook
and video transport. It does not introduce a second footage system. Every paid
request is for one clip, with its exact shot, references, approved look, model,
length, signed price figures and remaining ceiling. Taking a result never
completes a stage.

The work remains in progress. Six concrete findings across the footage work
have required corrections: the original spending gap, unsigned decisions treated
as authority, older shots disappearing, an early reservation gap, a stale card
which could not refresh its remaining ceiling, and an unsigned reconciliation
path. The proportionality decision is to finish the bounded operations and their
readable signing cards, retaining the financial proofs. A full request workbench
and automatic dispatch are deferred. The existing signer and transport remain
the foundation.

## For the tools

The operations live in `lib/footage_requests.py`. The transport remains
`tools/video/kling_reference_video.py`; the shared reservation journal and signed
account resolution are reused. An in-process scoped authorization is created
only after verifying a saved `footage_spend` receipt. Tool input flags cannot
create that authorization. This document will record the final operation
interfaces and limitations when verification is complete.
