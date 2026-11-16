# Footage workspace: the first half

This is a nonspending foundation for the footage workspace. It does not provide
the finished footage controls. The existing shot notebook can now keep a brief
bound to one saved paper-cut shot. Its description and reference paths must match
that shot exactly. It retains the shot's timing and difficulty as well, so a later
change cannot quietly inherit the earlier brief. Changing a different beat does
not change the shot binding.

The existing inspection operation reports whether the paper shot needs a fresh
look. Its source warnings also reach the existing shot board. Removing a shot
does not erase its notebook or its earlier takes. A trailer take can be accepted
or declined only through a verifiable signed decision. That signing control is
not available in this half, so both buttons are unavailable. Handwritten chosen
or declined rows are kept as history and ignored when showing a take's standing.
Older productions retain their existing selection behaviour.

Trailer footage spending and take decisions are deliberately unavailable in
this first half. The existing conversational spending approval is insufficient
for the trailer's required signed footage request. Every image, video and avatar
generation tool refuses a trailer before entering its provider code. The
reservation boundary checks independently, even when no shot or brief is named.
A missing, conflicting, linked or unverifiable production also refuses generation.
An imported reference
or old notebook entry cannot be accepted as trailer footage. Paper-cut
placeholders stay placeholders, and no stage is completed.

The next half must add the signed request with its exact outgoing material,
service, model, duration, price and remaining ceiling. It must prove one purchase
per signature across concurrent presses, restarts and crashes, including unknown
billing outcomes, before enabling spending. It must then add receipt-backed take
acceptance, the paper-cut footage display and the visible request panels. Story
and look approval stay outside those panels. None of that is claimed ready here.

## For the tools

The existing `shot_prepare` operation and `scripts.supervised_shot prepare` accept
a brief containing `paper_shot_id`. For trailers this must equal `shot_id`, name
a shot in `artifacts/edit_decisions.json`, use that shot's `purpose` as `direction`,
and list the shot's `references` as the ordered `reference_manifest` paths.
The existing reference hash and look checks remain. `inspect_shot` adds
`paper_binding` only for trailers. No second timeline or take store is introduced.

The full user design is in the housekeeping trailer plan. This first half has
one review/fix round with three demonstrated defects: an incomplete spending
refusal, unsigned take history treated as authority, and loss of older shots when
their signatures cannot be verified. The
proportionality decision is to close generation at the common tool entry and
reservation boundaries and keep display evidence separate from spending
permission. No new provider or approval system is introduced in this half.
