import { useEffect, useMemo, useState } from "react";
import Scene from "./Scene.jsx";
import Sidebar from "./Sidebar.jsx";
import WorldsOverview from "./WorldsOverview.jsx";
import { colorFor } from "./palette.js";

/*
  THESIS: routing is attraction, not a scatterplot — posts are particles in a
  vector field, pulled into each world's basin by how confidently they route
  there, refusing the flat dark "ops console" default this category always ships.
  OWN-WORLD: white ground, graphite hairline grid, one saturated accent per
  world confined to basin rings, connector lines, and post markers — never
  fields. IBM Plex Mono for instrument readouts, Space Grotesk for display.
  Instrument-bezel corner brackets and a dot-grid ground unify the shell into
  one enclosure instead of a legend widget floating beside a chart widget.
  STORY: the viewer sees which worlds are gaining data and how confidently
  posts are routing, by orbiting a shared field rather than reading a legend.
  FIRST VIEWPORT: header instrument readout, one bezel enclosing a rotatable
  3D field of world basins on the left and a ranked legend on the right —
  the whole shell fixed to the viewport, no page scroll.
  FORM: Vector Field, assigned direction 5 of 7, seed key b2b870d2.
  FINISH: unreviewed and undocumented is unfinished; this build ends with the
  finish review, the verdict, DESIGN.md, and every shipping raster carrying
  its provenance.
*/

const MAX_RENDERED_POSTS = 4000;
const API = "http://127.0.0.1:8000"; // uvicorn binds IPv4-only by default; "localhost" can resolve to ::1 first and fail

export default function App() {
  const [data, setData] = useState(null);
  const [postTotal, setPostTotal] = useState(0);
  const [counts, setCounts] = useState({}); // from the FULL post set, before sampling
  const [error, setError] = useState(null);
  const [hoveredWorldId, setHoveredWorldId] = useState(null);
  const [view, setView] = useState("topology"); // "topology" | "worlds" — client-side toggle, no router lib installed

  useEffect(() => {
    fetch(`${API}/api/topology`)
      .then((r) => r.json())
      .then((d) => {
        setPostTotal(d.posts.length);
        const c = {};
        for (const p of d.posts) c[p.world_id] = (c[p.world_id] || 0) + 1;
        setCounts(c);
        // ponytail: the field is decorative — a fixed sample reads identically at
        // this dot size and keeps the scene at a constant cost no matter how many
        // posts route. Raise MAX_RENDERED_POSTS if the density ever looks thin.
        const step = Math.ceil(d.posts.length / MAX_RENDERED_POSTS);
        setData({ ...d, posts: step > 1 ? d.posts.filter((_, i) => i % step === 0) : d.posts });
      })
      .catch((e) => setError(e.message));
  }, []);

  const colorByWorldId = useMemo(() => {
    if (!data) return {};
    return Object.fromEntries(data.worlds.map((w, i) => [w.id, colorFor(i)]));
  }, [data]);

  return (
    <div className="app-shell">
      <header
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          paddingBottom: 16,
          marginBottom: 20,
          flex: "0 0 auto",
        }}
      >
        <div>
          <div className="mono" style={{ fontSize: 10, color: "var(--accent)", letterSpacing: 2, marginBottom: 4 }}>
            <span className="live-dot" />
            VERILLIAN // SEMANTIC TOPOLOGY
          </div>
          <h1 style={{ fontSize: 26, fontWeight: 700, margin: 0, letterSpacing: -0.5 }}>
            {view === "topology" ? "Topology" : "Worlds Overview"}
          </h1>
        </div>

        <div className="mono" style={{ fontSize: 11, display: "flex", gap: 4, alignItems: "center" }}>
          {[
            { key: "topology", label: "TOPOLOGY" },
            { key: "worlds", label: "WORLDS" },
          ].map((tab) => (
            <span
              key={tab.key}
              onClick={() => setView(tab.key)}
              style={{
                cursor: "pointer",
                padding: "4px 10px",
                borderRadius: 3,
                letterSpacing: 1,
                color: view === tab.key ? "var(--accent-ink)" : "var(--text-muted)",
                background: view === tab.key ? "var(--accent)" : "transparent",
              }}
            >
              {tab.label}
            </span>
          ))}
        </div>

        {view === "topology" && data && (
          <div className="mono" style={{ fontSize: 12, color: "var(--text-muted)", textAlign: "right" }}>
            {data.worlds.length} WORLDS &middot; {postTotal} POSTS
          </div>
        )}
      </header>

      {view === "topology" && error && <p style={{ color: "#C44545" }}>Failed to load: {error}</p>}
      {view === "topology" && !data && !error && <p className="mono" style={{ color: "var(--text-muted)" }}>loading…</p>}

      {view === "worlds" && (
        <div className="app-panel">
          <span className="corner-bracket tl" />
          <span className="corner-bracket tr" />
          <span className="corner-bracket bl" />
          <span className="corner-bracket br" />
          <WorldsOverview />
        </div>
      )}

      {view === "topology" && data && (
        <div className="app-panel">
          <span className="corner-bracket tl" />
          <span className="corner-bracket tr" />
          <span className="corner-bracket bl" />
          <span className="corner-bracket br" />

          <Scene worlds={data.worlds} posts={data.posts} colorByWorldId={colorByWorldId} hoveredWorldId={hoveredWorldId} onHoverWorld={setHoveredWorldId} />

          <div className="panel-divider" />

          <div style={{ flex: "0 0 260px", display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div
              data-impeccable-variant="1"
              style={{
                "--p-glow": 0.5,
                padding: "16px 16px 14px",
                borderBottom: "1px solid var(--border)",
              }}
              data-impeccable-params='[{"id":"glow","kind":"range","min":0,"max":1,"step":0.05,"default":0.5,"label":"Signal glow"}]'
            >
              <div className="mono" style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: 1, marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
                <span className="live-dot" />
                LIVE SIGNAL
              </div>
              <div
                className="mono"
                style={{
                  fontSize: 34,
                  fontWeight: 700,
                  fontFamily: '"Space Grotesk", system-ui, sans-serif',
                  color: "var(--text)",
                  lineHeight: 1,
                  fontVariantNumeric: "tabular-nums",
                  textShadow: "0 0 calc(var(--p-glow, 0.5) * 16px) var(--accent)",
                }}
              >
                {postTotal}
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: 0.5, marginTop: 4 }}>
                POSTS ROUTED &middot; {data.worlds.length} WORLDS
              </div>
            </div>

            <div
              data-impeccable-variant="2"
              style={{ display: "none", "--p-gap": 1, padding: "16px 16px 14px", borderBottom: "1px solid var(--border)" }}
              data-impeccable-params='[{"id":"gap","kind":"range","min":0,"max":4,"step":1,"default":1,"label":"Segment gap"}]'
            >
              <div className="mono" style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: 1, marginBottom: 8 }}>
                COVERAGE SPECTRUM
              </div>
              <div style={{ display: "flex", height: 10, borderRadius: 2, overflow: "hidden", gap: "calc(var(--p-gap, 1) * 1px)" }}>
                {[...data.worlds]
                  .sort((a, b) => (counts[b.id] || 0) - (counts[a.id] || 0))
                  .filter((w) => counts[w.id])
                  .map((w) => (
                    <div
                      key={w.id}
                      title={`${w.name} — ${counts[w.id]}`}
                      style={{ flex: counts[w.id] || 0, background: colorByWorldId[w.id], minWidth: 3 }}
                    />
                  ))}
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
                {data.posts.length} POSTS &middot; {Object.values(counts).filter(Boolean).length} ACTIVE WORLDS
              </div>
            </div>

            <div
              data-impeccable-variant="3"
              style={{ display: "none", "--p-ticks": 1, padding: 16, borderBottom: "1px solid var(--border)" }}
              data-impeccable-params='[{"id":"ticks","kind":"toggle","default":true,"label":"Corner ticks"}]'
            >
              <div
                style={{
                  position: "relative",
                  border: "1px solid var(--border)",
                  borderRadius: 2,
                  padding: "10px 12px",
                  backgroundImage:
                    "repeating-linear-gradient(135deg, var(--grid) 0, var(--grid) 1px, transparent 1px, transparent 10px)",
                  backgroundColor: "var(--panel-2)",
                }}
              >
                {[
                  { top: -1, left: -1, borderWidth: "2px 0 0 2px" },
                  { top: -1, right: -1, borderWidth: "2px 2px 0 0" },
                  { bottom: -1, left: -1, borderWidth: "0 0 2px 2px" },
                  { bottom: -1, right: -1, borderWidth: "0 2px 2px 0" },
                ].map((pos, i) => (
                  <span
                    key={i}
                    style={{
                      opacity: "var(--p-ticks, 1)",
                      position: "absolute",
                      width: 8,
                      height: 8,
                      borderColor: "var(--accent)",
                      borderStyle: "solid",
                      ...pos,
                    }}
                  />
                ))}
                <div className="mono" style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: 1 }}>
                  SCAN &middot; {data.worlds.length} WORLDS &middot; {data.posts.length} UNITS
                </div>
              </div>
            </div>

            <Sidebar worlds={data.worlds} counts={counts} colorByWorldId={colorByWorldId} hoveredWorldId={hoveredWorldId} onHoverWorld={setHoveredWorldId} />
          </div>
        </div>
      )}
    </div>
  );
}
