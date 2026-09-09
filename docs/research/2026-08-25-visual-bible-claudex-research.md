# NBE Studio — claudex-loop web research brief (2026-08-25)

## Key Takeaways
- Reference-image conditioning (multi-reference video models) has displaced LoRA training as the default consistency method. Seedance 2.0/2.5, MiniMax H3, Wan 3.0 Prime, Kling 3.0 Elements all accept character + location refs simultaneously. [vendor]
- Practitioner consensus: lock static frames (character sheets, environment stills, palette) and get them approved BEFORE spending video credits; never mix consistency strategies mid-scene; prefer longer single takes over stitched 8s clips. [practitioner]
- Costs: ~3 gens per usable shot, ~25% of clips reach final cut, $315–$750 per finished minute (invideo, backed by counts) vs $75–$175 per 3-min short (mindstudio, unbacked). Unresolved.
- Character sheet spec: front / three-quarter / profile portraits, full-body default costume, expression sheet, wardrobe ref with negative line, location stills, props.
- Poster typography: Ideogram V3 for pure type; GPT Image 2 / Seedream 5 Pro when text sits in a scene; many pipelines keep titles out of the model and add them in the edit.
- FAL endpoints (verified live on fal.ai/explore 2026-08-25): bytedance/seedance-2.5/reference-to-video, bytedance/seedance-2.0/reference-to-video, minimax/h3/reference-to-video, alibaba/wan-3.0-prime/reference-to-video, fal-ai/kling-video/v3/pro/image-to-video, fal-ai/veo3.1/first-last-frame-to-video, fal-ai/flux-pro/kontext, bytedance/seedream/v5/pro/edit, fal-ai/nano-banana-pro/edit, fal-ai/ideogram/v3, openai/gpt-image-2, fal-ai/flux-lora-portrait-trainer, fal-ai/flux-kontext-trainer, lip sync: fal-ai/bytedance/omnihuman/v1.5, fal-ai/sync-lipsync/v2; finishing: topaz/upscale/video, topaz/interpolate.
- Unconfirmed on FAL: fal-ai/ideogram/character/edit, fal-ai/instant-character. Not on FAL: Runway Gen-4, Higgsfield Soul ID.
- Open-source references: HKUDS/ViMax (12k stars, provider-agnostic, consistency validation); machina-exm/film-studio-skills (new, "asset passports" copied verbatim into every prompt, 22-field shot cards).
- Audience/institutional rejection of visible AI film is current (Tropfest backlash Mar 2026, AMC opt-out, Cannes exclusion).

## Sources
invideo.io/blog/ai-filmmaking-tools · thebrandhopper.com (character consistency is a stack problem) · aiofm.info pulid-vs-instantid · myaiforce.com flux-kontext · higgsfield.ai/blog/sould-id · hackernoon.com ai-filmmaking-checklist · invideo.io/faq budget · mindstudio.ai · masonry.so text-rendering test · abc.net.au 2026-03-05 tropfest · hackernoon cannes · fal.ai/explore/models · github.com/hkuds/vimax · github.com/machina-exm/film-studio-skills
