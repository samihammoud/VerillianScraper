# Design

<!-- impeccable:design-schema 1 -->

## World

**Vector Field.** Routing is attraction, not a scatterplot: posts are particles in a vector field, pulled into each world's basin in proportion to how confidently they route there. Assigned direction 5 of 7 grounded candidates, seed key `b2b870d2`, chosen over the roll's own pick (Orrery) after a structured decision round with the user.

## Palette

Restrained, instrument-register. White ground (`--bg: #f4f5f3`, panels `#ffffff`), graphite hairlines (`--border: #dcdfd9`, `--grid: #e3e6e0`), near-black ink (`--text: #14171a`). One saturated accent per world (`palette.js` `CHANNELS`), confined to basin rings, connector lines, and post markers — never fields:

```
#2F6FED field blue      #E0692F basin orange   #1F9E6E signal green   #B0399B magenta
#C99A1E amber           #5B58D6 violet         #1C8C9E cyan           #C44545 red
```

## Type

Space Grotesk (display, inherited from the prior identity) / IBM Plex Mono (instrument labels: header kicker, counts, coordinates, in-scene world labels). Tracking +1–2 on mono labels, tabular numerals throughout (`.mono` sets `font-variant-numeric: tabular-nums`).

## Composition

One shared rotatable 3D scene (`@react-three/fiber` + `drei`), not per-world small multiples. Each world is a fixed anchor in the shared t-SNE space with a floor-projected ring ("basin"), a dashed connector, a small emissive marker, and a billboarded mono label. Posts are an `instancedMesh` of small spheres; on load they animate (ease-out cubic, 1.4s) from their raw t-SNE position into a position pulled toward their world's anchor, pull strength scaling with the post's real cosine-similarity to that world's reference embedding (normalized across the current dataset) — a confident route converges deeper into the basin than a marginal one. Grid floor plane (drei `Grid`) gives the depth cue the brief asked for; camera orbits via `OrbitControls` (drag to rotate, scroll to zoom, gentle auto-rotate until the first interaction).

Sidebar legend (right, 260px) lists worlds ranked by count with a mini bar and swatch; hovering a row or a 3D basin cross-highlights the other (shared `hoveredWorldId` state), and dims non-matching posts/basins in the scene. Hovering a post shows a caption tooltip and highlights faint streamlines from every post in that world to its basin.

## Components

- `Scene.jsx` — the 3D field: `PostField` (instanced posts + convergence animation), `WorldBasin` (ring + connector + marker + billboarded label), `Streamlines` (hover-only), `AutoRotate`.
- `Sidebar.jsx` — ranked world legend, restyled to the white/graphite palette; unchanged structure from the prior version.
- `App.jsx` — header instrument readout + responsive two-column layout (`.topology-layout`, stacks to one column ≤760px).

## Backend contract

`/api/topology` now returns 3D coordinates (`x, y, z`, 3-component t-SNE, not 2) and a per-post `similarity` (real cosine similarity to the post's routed world's `reference_embedding`) — both consumed directly by the convergence animation. No fabricated metrics: `similarity` is computed server-side from the same vectors the router uses.

## Known gaps

- Mobile layout stacks correctly (CSS verified) but the 3D scene itself isn't tuned for touch/small viewports — orbit controls are mouse-drag only.
- No color-blind-safe verification on the 8-world accent palette; distinguishing worlds currently leans on the legend's text labels as the accessible fallback.
- `Space Grotesk` is a generically common face (flagged by `detect.mjs`); kept as-is since it was the prior identity's inherited voice and the brief scoped this redesign to layout/dimensionality/color, not typography.
