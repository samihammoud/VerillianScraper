import { useEffect, useMemo, useState } from "react";
import Scene from "./Scene.jsx";
import Sidebar from "./Sidebar.jsx";
import { colorFor } from "./palette.js";

/*
  THESIS: routing is attraction, not a scatterplot — posts are particles in a
  vector field, pulled into each world's basin by how confidently they route
  there, refusing the flat dark "ops console" default this category always ships.
  OWN-WORLD: white ground, graphite hairline grid, one saturated accent per
  world confined to basin rings, connector lines, and post markers — never
  fields. IBM Plex Mono for instrument readouts, Space Grotesk for display.
  STORY: the viewer sees which worlds are gaining data and how confidently
  posts are routing, by orbiting a shared field rather than reading a legend.
  FIRST VIEWPORT: header instrument readout, left a rotatable 3D field of
  world basins with converging post particles, right a ranked legend.
  FORM: Vector Field, assigned direction 5 of 7, seed key b2b870d2.
  FINISH: unreviewed and undocumented is unfinished; this build ends with the
  finish review, the verdict, DESIGN.md, and every shipping raster carrying
  its provenance.
*/

const API = "http://127.0.0.1:8000"; // uvicorn binds IPv4-only by default; "localhost" can resolve to ::1 first and fail

export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [hoveredWorldId, setHoveredWorldId] = useState(null);

  useEffect(() => {
    fetch(`${API}/api/topology`)
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  const colorByWorldId = useMemo(() => {
    if (!data) return {};
    return Object.fromEntries(data.worlds.map((w, i) => [w.id, colorFor(i)]));
  }, [data]);

  const counts = useMemo(() => {
    if (!data) return {};
    const c = {};
    for (const p of data.posts) c[p.world_id] = (c[p.world_id] || 0) + 1;
    return c;
  }, [data]);

  return (
    <div style={{ minHeight: "100%", padding: "20px 28px 40px" }}>
      <header
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          borderBottom: "1px solid var(--border)",
          paddingBottom: 16,
          marginBottom: 20,
        }}
      >
        <div>
          <div className="mono" style={{ fontSize: 10, color: "var(--accent)", letterSpacing: 2, marginBottom: 4 }}>
            VERILLIAN // SEMANTIC TOPOLOGY
          </div>
          <h1 style={{ fontSize: 26, fontWeight: 700, margin: 0, letterSpacing: -0.5 }}>Topology</h1>
        </div>
        {data && (
          <div className="mono" style={{ fontSize: 12, color: "var(--text-muted)", textAlign: "right" }}>
            {data.worlds.length} WORLDS &middot; {data.posts.length} POSTS
          </div>
        )}
      </header>

      {error && <p style={{ color: "#C44545" }}>Failed to load: {error}</p>}
      {!data && !error && <p className="mono" style={{ color: "var(--text-muted)" }}>loading…</p>}

      {data && (
        <div className="topology-layout">
          <div>
            <Scene worlds={data.worlds} posts={data.posts} colorByWorldId={colorByWorldId} hoveredWorldId={hoveredWorldId} onHoverWorld={setHoveredWorldId} />
            <p className="mono" style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 10 }}>
              3D t-SNE projection of 1536-dim post embeddings. Posts are pulled toward their world's basin in proportion to real routing similarity — position is neighbor structure plus confidence, not a literal axis.
            </p>
          </div>
          <Sidebar worlds={data.worlds} counts={counts} colorByWorldId={colorByWorldId} hoveredWorldId={hoveredWorldId} onHoverWorld={setHoveredWorldId} />
        </div>
      )}
    </div>
  );
}
