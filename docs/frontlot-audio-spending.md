# Audio spending protection

Voice, music and sound effects need Ben's signed approval of each individual request before the protected route contacts a service. A connected account or an approved production budget is not that approval.

The saved request names ElevenLabs, the audio kind, the explicit model, the chosen voice for speech, the exact words or description, and the requested length. It includes a fingerprint of those inputs, the signed production settings, the highest cash charge, the highest replacement value of account credits, and the time that price bound expires. Changed inputs need a new approval. A price estimate without a firm upper bound is refused.

Each request can buy one submission. Signing it twice or confirming it twice cannot buy it again. The production's existing spending lock serializes reservations, so competing requests cannot both claim the last available money. The budget includes every service using the production's existing cost ledger. Cash and replacement-value credits are separate parts of one total.

A reservation is saved before the transport can run. The protected route makes one submission and does not retry or select a different service or account. A timeout, an unclear reply or a failed download leaves the reservation pending. Restarting does not clear it. Existing recovery can finish a saved output and its receipt without submitting again. When the outcome is still unknown, an authorized reconciliation must establish what happened. The existing reconciler identifies audio as needing manual review and leaves its reservation in place. It never polls the footage service with an audio request. This piece adds no automatic audio polling or resubmission.

This is protection for the later audio controls, not those controls themselves. It supplies no live transport, credential lookup, prices or licence clearance. The later audio adapters must use this boundary, supply a single-call ElevenLabs transport, send precisely the approved input, use only the intended server-side account, and obtain the actual cash and replacement-credit charge. They must not wrap it in retries or fallbacks. They must establish a firm account-specific upper bound, including any minimum purchase, before asking for approval. This piece supports text and descriptions only; recorded voice uploads, reference uploads and extra generation settings are not enabled.

The existing audio generators have not been changed or connected to this route yet. Their historical estimates are not authoritative prices for this boundary. There is no new voice, music, sound-effect or import button. No credential is part of the request, generation receipt or browser data. The boundary receives transport functions from the server-side adapter rather than reading a key.

Music assets can record either a generated origin with its spending approval and generation receipt, or an imported origin with the file fingerprint and a licence note. That note records Ben's evidence; it does not establish legal clearance. ElevenLabs music is not cleared here for a real trailer. No import control or commercial licence decision is added.

Older signed production settings retain their existing schemas and behavior. The new settings version adds ElevenLabs and three audio content classes. Existing configurations are not rewritten, re-signed or migrated. General provider consent remains separate from the signature for an individual request.

A signature has the same trust limits as Frontlot's existing gates: it proves integrity under the local signing key and recorded human approval path. It is not proof against every other program running as the owner with access to that key.

## For the tools

The additive signed configuration is version 1.3. Versions 1.0, 1.1 and 1.2 are retained. The new provider is elevenlabs; its content classes are speech_text, music_prompt and effect_prompt.

The protection API is tools/audio/_governed.py: prepare, read_request, check_current and execute. Requests are newly named files under .audio-requests/. The new approval kind is audio_spend, constructed independently in scripts/gate_approve.py, reached through the unchanged scripts/gate_sign.py wrapper and lib/run_common.py invocation.

The later app operation must prepare its existing gate request with the audio request's identifier as entity_id. The signer reloads the saved request, shows the exact material and price components, and rechecks signed settings, expiry and remaining budget before committing. The existing gate receipt signs the complete record.

The transport contract is submit(payload, reservation_id), returning request_id, cash_usd and credits_usd; download(result) returns nonempty bytes. These functions are trusted server adapters, not unrestricted browser callbacks. They must not retry. The payload contains provider, kind, model, voice, prompt and duration. Additional generation options require an explicit extension of this signed contract.

Successful outputs use the existing generation write-ahead recovery and signed receipt ledger. Audio charge fields are additive and absent on older receipts. Uncertainty stays in the existing reservation ledger and blocks new paid calls through resume_check.

## Closing protections

The older ElevenLabs voice and music tools now refuse to submit for a trailer or a production whose current verified settings use the new audio configuration. The refusal is: “Audio for this production goes only through an approved request.” This applies to their public entry points and their internal generation methods. Registry and voice-selector dispatch reach the same refusal. A trailer is still protected if its marker is missing but a signed trailer record or a trailer stage file survives.

Productions that are neither trailers nor signed with the new audio settings keep the older tools' existing behavior. That remains an unapproved audio route for those productions until the later audio integration piece. An unattributed legacy call also retains its old behavior; calls belonging to a production must carry its directory or an output path inside it. The tool can also recognize the production from its current working directory.

The pinned trailer definition is unchanged because editing it would invalidate signatures. Its stage list still names the older music tool. That listing cannot override the tool's refusal. There is no executable general stage runner in this repository; the operating assistant reads the manifest and calls registered tools.

Saved audio requests identify their production and are read without following linked folders or linked files. A copied request from a different production is refused too. Requests saved before this identity field was added need to be prepared and approved afresh; this work does not rewrite their signatures.

Downloaded bytes are saved in a temporary staging file. The recovery record is written before the file can be published to its final place. Existing recovery then publishes it and finishes the receipt. Content provenance uses the generation input fingerprint, while the spending approval retains its own separate identity. Two independently approved calls may therefore produce identical bytes without contradicting one another's provenance.

A zero-total charge bound is always refused for ElevenLabs audio. A credit-funded request can still have no new cash charge, provided its replacement-credit bound is positive. Audio budget arithmetic preserves four-decimal amounts, including the last fraction of a cent, without changing older tools' tracker behavior. The signer displays price expiry as a UTC date and time and keeps the original numerical value in the signed record.

Unresolved audio still freezes every paid request in the production. There is no resolution control yet. The later integration must add an honest account-review and reconciliation flow. The remaining limits also include raw transport errors reaching their caller, reliance on the protected production reservation file for consumption history, and no upper limit on a future adapter's expiry date. These are recorded limits, not new permissions to expose credentials, edit spending history or claim an unverified price.
