# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences: (1) the internal team, validating that scraped posts are routing into the correct semantic "worlds"; (2) future customers/stakeholders, who will eventually view this surface as a product deliverable, not just a dev tool.

## Product Purpose

Verillian scrapes TikTok/Instagram accounts, classifies each post into one of a small set of pre-defined semantic "worlds," and this dashboard visualizes that resulting topology. It serves two jobs equally: routing-quality QA (are posts landing in the semantically correct world, are there misrouted clusters that reveal embedding or world-definition problems) and coverage/scale monitoring (how much data has been ingested, how it's distributed across worlds).

## Positioning

Worlds are fixed, manually-defined routing targets (not discovered via clustering), and each post is routed individually by comparing its own embedding against every world's reference embedding — there is no single "account vector." The topology view exposes this per-post routing structure directly (a t-SNE projection of post embeddings, colored by world), rather than aggregating to account- or platform-level stats.

## Operating Context

Reads from the backend's `/api/topology` endpoint (`http://127.0.0.1:8000` in local dev), which returns the current worlds and posts with their routed `world_id`. The dashboard is a read-only view: a scatter/projection chart of posts plus a sidebar listing worlds with post counts. Currently local-only (no auth, no deploy target confirmed).

## Capabilities and Constraints

- Built with Vite + React (no CSS framework/component library yet — hand-rolled inline styles + CSS custom properties).
- Data shape: `worlds` (id, name) and `posts` (world_id + embedding-derived position), consumed via `/api/topology`.
- No routing/filtering/drill-down UI yet — single view, hover-to-highlight a world.
- Undecided: deployment target and auth model for the stakeholder-facing audience; whether historical/time-series views are needed for coverage monitoring.

## Brand Commitments

Product name: **Verillian**.

## Evidence on Hand

No real customer-facing copy, testimonials, or case studies exist yet. Existing implementation (`apps/ui/src`) has a dark, terminal/technical-monospace visual identity (near-black background, mint-green accent, IBM Plex Mono for data labels, Space Grotesk for display type) — treated as incumbent visual evidence, not yet documented in DESIGN.md.

## Product Principles

- Show routing structure honestly: position encodes neighbor structure from embeddings, not a literal scale or axis — never imply false precision.
- Favor legibility of the underlying data (world assignment, density, counts) over decoration, even as the surface becomes stakeholder-facing.
- Worlds and routing are fixed/manual by design in this phase — the UI should reflect that stability, not suggest autonomous or dynamic world discovery.
- As this becomes stakeholder-visible, it must read as a deliberate product surface, not a raw debug dump — without inventing data or metrics that don't exist.
