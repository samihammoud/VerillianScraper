import { useEffect, useState } from "react";

const API = "http://127.0.0.1:8000";

function useJson(url) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!url) return;
    setData(null);
    setError(null);
    fetch(url)
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(e.message));
  }, [url]);
  return { data, error };
}

function CoverageStrip({ coverage }) {
  if (!coverage) return null;
  const vlmPct = coverage.n_posts ? Math.round((coverage.n_with_vlm / coverage.n_posts) * 100) : 0;
  return (
    <div
      className="mono"
      style={{
        display: "flex",
        gap: 20,
        flexWrap: "wrap",
        padding: "10px 16px",
        borderBottom: "1px solid var(--border)",
        fontSize: 11,
        color: "var(--text-muted)",
        flex: "0 0 auto",
      }}
    >
      <span>{coverage.n_posts} posts</span>
      <span>{vlmPct}% vlm coverage</span>
      <span>{coverage.n_accounts} accounts</span>
      <span>{Math.round(coverage.world_median_views).toLocaleString()} median views</span>
      {coverage.n_low_conf_excluded > 0 && <span>{coverage.n_low_conf_excluded} low-conf excluded</span>}
      <span>{Math.round(coverage.products_none_visible_rate * 100)}% no product visible</span>
      <span style={{ marginLeft: "auto", color: "var(--text-dim)" }}>
        {coverage.computed_at ? `computed ${new Date(coverage.computed_at).toLocaleString()}` : "not yet computed — run make overview"}
      </span>
    </div>
  );
}

function TermPanel({ slug, facet, label, onSelectTerm }) {
  const [sort, setSort] = useState("lift");
  const { data } = useJson(slug ? `${API}/worlds/${slug}/overview?facet=${facet}&sort=${sort}&limit=25` : null);
  const terms = data?.terms || [];

  return (
    <div
      style={{
        flex: "1 1 0",
        minWidth: 240,
        borderRight: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 14px",
          borderBottom: "1px solid var(--border)",
          flex: "0 0 auto",
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600 }}>{label}</span>
        <div className="mono" style={{ fontSize: 10 }}>
          {["lift", "volume"].map((s) => (
            <span
              key={s}
              onClick={() => setSort(s)}
              style={{
                cursor: "pointer",
                marginLeft: 10,
                color: sort === s ? "var(--accent)" : "var(--text-dim)",
                textDecoration: sort === s ? "underline" : "none",
              }}
            >
              {s.toUpperCase()}
            </span>
          ))}
        </div>
      </div>
      <div style={{ overflowY: "auto", flex: 1 }}>
        {terms.map((t) => (
          <div
            key={t.canon_term}
            title={`variants: ${t.variants.join(", ")}`}
            onClick={() => onSelectTerm({ facet, canon_term: t.canon_term, label })}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "7px 14px",
              borderBottom: "1px solid var(--grid)",
              cursor: "pointer",
            }}
          >
            <span className="mono" style={{ fontSize: 12, color: "var(--accent)", width: 46, flexShrink: 0 }}>
              {t.view_ratio.toFixed(1)}x
            </span>
            <span style={{ fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {t.canon_term}
            </span>
            <span className="mono" style={{ fontSize: 10, color: "var(--text-muted)", flexShrink: 0 }}>
              {t.n_posts}p/{t.n_accounts}a{facet === "product" ? `/${t.n_hero}h` : ""}
            </span>
          </div>
        ))}
        {data && !terms.length && (
          <div className="mono" style={{ padding: 14, fontSize: 11, color: "var(--text-dim)" }}>
            no ranked terms yet
          </div>
        )}
      </div>
    </div>
  );
}

function DrilldownGrid({ slug, selected, onClose }) {
  const url =
    selected && slug
      ? `${API}/worlds/${slug}/terms/${selected.facet}/${encodeURIComponent(selected.canon_term)}/posts?limit=20`
      : null;
  const { data } = useJson(url);
  if (!selected) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "absolute",
        inset: 0,
        background: "rgba(20,23,26,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "relative",
          width: "82%",
          height: "82%",
          background: "var(--panel)",
          border: "1px solid var(--border)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
            flex: "0 0 auto",
          }}
        >
          <div>
            <div className="mono" style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: 1 }}>
              {selected.label.toUpperCase()}
            </div>
            <span style={{ fontSize: 16, fontWeight: 600 }}>{selected.canon_term}</span>
          </div>
          <span className="mono" style={{ cursor: "pointer", color: "var(--text-dim)", fontSize: 12 }} onClick={onClose}>
            CLOSE ✕
          </span>
        </div>
        <div
          style={{
            overflowY: "auto",
            padding: 16,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
            gap: 14,
          }}
        >
          {(data || []).map((p) => (
            <a
              key={p.video_url}
              href={p.video_url}
              target="_blank"
              rel="noreferrer"
              style={{ textDecoration: "none", color: "var(--text)" }}
            >
              <div style={{ width: "100%", aspectRatio: "3/4", background: "var(--grid)", borderRadius: 3, overflow: "hidden" }}>
                {p.thumbnail_url && (
                  <img src={p.thumbnail_url} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                )}
              </div>
              <div className="mono" style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                {(p.views || 0).toLocaleString()} views
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: "var(--text-dim)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {p.caption || p.summary}
              </div>
            </a>
          ))}
          {data && !data.length && (
            <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
              no posts found
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function WorldsOverview() {
  const { data: worlds } = useJson(`${API}/worlds`);
  const [slug, setSlug] = useState(null);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    if (worlds?.length && !slug) setSlug(worlds[0].slug);
  }, [worlds, slug]);

  const { data: coverageSource } = useJson(slug ? `${API}/worlds/${slug}/overview?facet=product&limit=1` : null);

  return (
    <div style={{ position: "relative", display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 16px",
          borderBottom: "1px solid var(--border)",
          flex: "0 0 auto",
        }}
      >
        <span className="mono" style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: 1 }}>
          WORLD
        </span>
        <select
          value={slug || ""}
          onChange={(e) => setSlug(e.target.value)}
          style={{
            background: "var(--panel-2)",
            color: "var(--text)",
            border: "1px solid var(--border)",
            borderRadius: 3,
            padding: "4px 8px",
            fontFamily: "inherit",
            fontSize: 13,
          }}
        >
          {(worlds || []).map((w) => (
            <option key={w.slug} value={w.slug}>
              {w.name}
            </option>
          ))}
        </select>
      </div>

      <CoverageStrip coverage={coverageSource?.coverage} />

      {slug && (
        <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
          <TermPanel slug={slug} facet="product" label="Winning Products" onSelectTerm={setSelected} />
          <TermPanel slug={slug} facet="format" label="Winning Formats" onSelectTerm={setSelected} />
          <TermPanel slug={slug} facet="topic" label="Winning Topics" onSelectTerm={setSelected} />
        </div>
      )}

      <DrilldownGrid slug={slug} selected={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
