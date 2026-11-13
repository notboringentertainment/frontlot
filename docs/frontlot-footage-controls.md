# Footage controls

The writer will work on the existing board, one paper-cut shot at a time.
Each shot shows its description, reference pictures and active approved looks.
The writer chooses the supported model and clip length, reviews the dollar
bound and remaining ceiling, and creates a request. The existing signing
drawer records the signature. After signing, the board starts that exact
request through the existing operation and watches its result. A take plays
in the existing player. Keeping or declining it opens the same signing drawer;
the signed decision is then applied to the saved cut. A retake has a new
request and its own signature.

There is one supported model on this base. The selector will say so rather
than promise an unavailable choice. No other service, retry or spending
implementation is added. On restart, an approved unused request can be started
explicitly; an uncertain used request stays blocked for a signed account review.
The browser never treats a successful signing message as spending authority:
the existing operations verify every signature again.

The implementation will first prove the authenticated routes with counting
stand-ins, then connect the board and exercise the whole one-shot journey in
a browser. Refusal tests cover missing story, looks, pictures and signed prices,
changed sources, invalid length/model, absent signatures, duplicate starts,
uncertain outcomes, and a final decline. Screenshots and complete-suite results
will be recorded after the controls work. No production material is used.

The scope decision is to reuse the existing cards, signer, player and money
formatter. A separate application, transport, ledger or approval system would
duplicate working authority and is outside this work. The prior financial work
has seventeen recorded corrective findings. This controls task begins with
that count preserved; its first checkpoint is a complete one-shot journey.

The one-shot browser journey now works. During implementation, five corrective
issues prompted the proportionality check: refresh starvation, the test socket
pathname, unreadable inherited colours, missing settings hiding the panel, and
the need to recover a signed keep after interruption. The decision is to finish
the bounded refusal and recovery controls and test them. No new request system,
provider adapter, approval rule or compatibility layer is needed. The running
count is twenty-two, including the preceding financial work.

## For the tools

Base: `62ed230d63e5d7b418336959e1409a3c84f04747` on
`trailer/04b-footage-requests`. Work only on `trailer/04c-footage-controls`.
Do not push the base branch while it is in review.

The adapter and authenticated routes belong in `backlot/footage_controls.py`,
installed by `backlot/server.py`. They call `supervised_production.prepare`
and `footage_requests.{quote,prepare_request,request_approval,execute,
request_decision,apply_decision}`, plus the shared signed account review.
`backlot/ui/footage.js` owns the focused panel; `board.js` supplies its existing
gate selection, look cards, take player and refresh. A completed signer tag
calls the adapter, which rechecks the signed operation rather than trusting
the tag. Browser polling reads state and never initiates a purchase.

Tests: `tests/backlot/test_footage_controls.py` and
`tests/backlot/test_footage_controls_browser.py`. Prove failures on the base,
then the controls, followed by the full suite against this stacked base.
Use the repository guard for every test. The read-only metadata baseline is
`/tmp/frontlot-controls-proof-l35ti_19/before.json`.
