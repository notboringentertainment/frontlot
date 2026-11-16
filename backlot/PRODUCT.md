# Product

<!-- impeccable:product-schema 1 -->

Scope: Front Lot only (the app in `backlot/`, internal code name "backlot").
The rest of the OpenMontage repo is out of scope for this record.

## Platform

web

Served by a local Python server and shown in its own app window (no browser
tabs or address bar), like a native Mac app. Confirmed by Ben 2026-10-07.

## Users

Ben, a writer-producer at Not Boring, is the only user today. He is not a
professional engineer and should never need a terminal command to use it.
Hoped-for future: other people enjoy it too, so nothing should depend on
knowing pipeline internals.

## Product Purpose

Front Lot is where a film's visuals get made. Each of Ben's apps has one job:
Story-drive drives the beat sheet, WriterOS defines what the images should
look like (looks Ben promotes there), and Front Lot creates the images.

Today Front Lot only shows the work: a separate Claude Code session in a
terminal makes the image decisions and runs generation, and Front Lot draws
the results from the project folder. Success means Ben runs the whole image
session inside Front Lot, with Claude in the app, and watches the results
arrive beside the conversation.

## Positioning

A supervised film workshop: an AI session does the making, Ben sees every
result with his own eyes and signs every approval and spend himself.

## Operating Context

- Projects live under `projects/<id>/`; the board reads pipeline files
  (checkpoints, scene plan, asset manifest, events, cost log, gate requests).
- WriterOS promotes looks; Front Lot acts on what was promoted.
- Approvals ("gates") are signed by Ben through the real signing program;
  the board today runs it in an in-page terminal (`backlot/tty.py`).
- Story-drive's live session screen is the reference pattern for running
  Claude inside an app: Claude Code runs hidden, a plugin reports session
  events, the app draws its own session view, raw terminal as fallback.
  Inspiration only; Front Lot must not look like Story-drive.

## Capabilities and Constraints

- Paid generation is supervised: one entity at a time, never batched ahead of
  Ben's approval; spending needs his yes.
- Nothing may approve or sign on Ben's behalf.
- Name rule: the app is "Front Lot" in anything Ben sees; "backlot" is the
  internal code name only.
- Front Lot is the product; any single film (e.g. a test project) is just
  content. Features must work for every project.
- Open: whether the board itself can start runs (old rule said no; Ben's
  2026-10-06 direction says Front Lot must be a fully working app).

## Brand Commitments

Name: Front Lot, by Not Boring. The current look is rejected by Ben ("ugly");
Story-drive's look is also rejected as a model.

## Evidence on Hand

Real project data under `projects/` (images, sheets, gate requests, costs).
No testimonials or external users exist; do not invent any.

## Product Principles

1. The making happens here: no step requires leaving the app for a terminal.
2. Ben judges with his eyes: images are the main event, everything else
   supports judging them.
3. Supervised, never autopilot: every spend and approval is Ben's, visibly.
4. Plain language for a non-engineer: no hashes, IDs, or machine lines in
   the main view.
