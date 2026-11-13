# Voice workbench

The trailer board now has a voice workspace beside its paper cut. It shows saved lines beside protected wording, reads account voices on request, and prepares one spending card at a time. No voice is selected by default. The writer confirms permission to use the selected voice.

The card shows the actual saved words, ElevenLabs, the fixed Multilingual v2 model, the selected account voice, the intended line slot, maximum cash and replacement-credit charges, expiry and remaining production ceiling. The existing signing buttons approve that exact request. A separate button then submits it through the existing protected tool. Reloading or pressing again cannot buy a second call with that approval.

The speech endpoint does not receive pronunciation or delivery notes from this model adapter. Notes stay with the signed request for human review, and the panel explicitly says so. The endpoint determines actual audio length. Listen before keeping a take; neither the paper-cut slot nor a note is a promise of performance or timing.

The writer hears a take and signs keeping or permanent decline. The current line changing makes a kept take need fresh review. Declining a stale take remains possible, because refusing to use it does not approve changed source material. Decline remains final. Another take starts another spending card and requires another signature.

Audio prices and account consent can be prepared and signed without a terminal. Rates have no defaults. The form records speech and music ceilings, the credit replacement value, a voice multiplier of at least one, and a future expiry. It adds speech consent only. Existing production permissions remain part of the full configuration signature. Saving a new draft pauses paid work until signing; the latest signed settings can be explicitly restored from a preserved copy. Audio-only settings do not require a picture judge; picture judging still requires its own complete settings and consent.

Each price field explains what it means and where to look in the ElevenLabs account. The form links to the official billing, pricing, music-cost and voice-multiplier guides. A public plan average or cost estimate is not a confirmed maximum for this account. If the account does not supply a firm ceiling, ask ElevenLabs Support for written confirmation before signing. The form never fills in suggested rates. It does keep the current verified signed figures visible after signing and reloading, and keeps unsaved edits during board refreshes.

“Discard draft and restore signed settings” discards an unsigned file draft. If the saved settings are already signed, it says so and changes nothing. Changing signed rates requires editing and signing new settings. The unresolved-charge form starts at not charged, with zero amounts that cannot be edited until charged is selected. Returning to not charged resets both amounts to zero. The writer must still write what they checked and sign the account outcome.

When no account key is available to the running board, it clearly says ElevenLabs is not connected and disables paid controls. Someone who starts Front Lot must connect the account securely and restart the board, then the writer reloads. This round adds no connection screen. A configured key is described as configured, not as proof of account access, prices or voice rights. An unsigned price setup receives the direct instruction to sign the price settings first, without contacting the account.

An uncertain bill stays held against the ceiling. The board exposes the existing signed account-review operation and applies only its signed outcome. It makes no new submission and creates no missing audio. Cash and replacement-credit audio charges are separated, while the production total includes all services.

This pull request is the permitted voice-first slice. Music generation, the music player and native licensed-file chooser remain for the next pull request. It adds no direction-text synthesis, voice cloning, automatic retries, provider substitution, whole-stage approval, asset-manifest completion or cutting/export control. The later cut must call the existing accepted-take operation again when consuming audio. No older-production compatibility layer was added.

## For the tools

The panel adapter is backlot/voice_panel.py and its view is backlot/ui/voice.js. Every write route uses the board origin and capability check. Paid submission calls ElevenLabsTTS with only project_dir and audio_request_id. Account-list retrieval uses the fixed ElevenLabs voices endpoint, with redirects and retries off, and sends no story. Credentials come only from the server environment and never appear in the page or saved request.

The existing audio_spend, audio_take, audio_resolution and config gate kinds remain the only signature authorities. Price requests use the audio:project_config scope and a digest checked again by the configuration constructor. The existing configuration schema's current audio branch permits omitted qc; a supplied qc block still validates fully. No judge or price is invented. The broker and signer use the running Python environment so the app's own libraries are present.

New notes and the account voice label are kept inside the signed source binding. The paid tool's outgoing speech body remains exactly text and model_id, with voice selected by its approved URL. Content playback is confined to one verified take, refuses links and mismatched bytes, and serves the locally probed audio type. Music controls are deliberately absent even though the safe take reader can serve the first half's audio assets.
