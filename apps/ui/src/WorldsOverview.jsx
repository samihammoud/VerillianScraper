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
          {["lift", "volume", "account"].map((s) => (
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
              {(sort === "volume" ? t.volume_ratio : sort === "account" ? t.account_view_ratio : t.view_ratio).toFixed(1)}x
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

function AccountsPanel({ slug }) {
  const { data } = useJson(slug ? `${API}/worlds/${slug}/accounts` : null);
  const accounts = data || [];
  const [openHandle, setOpenHandle] = useState(null);

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
      {accounts.map((a) => (
        <div key={a.handle} style={{ marginBottom: 18, borderBottom: "1px solid var(--grid)", paddingBottom: 14 }}>
          <div
            onClick={() => setOpenHandle(openHandle === a.handle ? null : a.handle)}
            style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}
          >
            <span style={{ fontSize: 14, fontWeight: 600 }}>@{a.handle}</span>
            <span className="mono" style={{ fontSize: 10, color: "var(--text-muted)" }}>
              {a.n_peaks} peaks / {a.patterns.length} pattern{a.patterns.length === 1 ? "" : "s"}
            </span>
          </div>
          {openHandle === a.handle &&
            a.patterns.map((p, i) => (
              <div key={i} style={{ marginTop: 10, marginLeft: 4 }}>
                <div className="mono" style={{ fontSize: 10, color: "var(--accent)" }}>
                  {p.size} posts · {p.products.slice(0, 4).join(", ") || "no product visible"}
                </div>
                {p.top_topics.length > 0 && (
                  <div className="mono" style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 2 }}>
                    topics: {p.top_topics.map(([t, n]) => `${t} (${n})`).join(", ")}
                  </div>
                )}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 }}>
                  {p.posts.map((post) => (
                    <a
                      key={post.video_url}
                      href={post.video_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mono"
                      style={{ fontSize: 10, color: "var(--text-muted)", textDecoration: "none" }}
                    >
                      {(post.views || 0).toLocaleString()}v ↗
                    </a>
                  ))}
                </div>
              </div>
            ))}
        </div>
      ))}
      {data && !accounts.length && (
        <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
          no accounts with a repeating peak pattern yet
        </div>
      )}
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
              {/* matched_term is the actual field value that put this post under this
                  canon_term (raw_term from post_terms) — shown first since caption/summary
                  are unrelated fields that make an otherwise-correct match look wrong. */}
              {p.matched_term && (
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--accent)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    marginTop: 2,
                  }}
                  title={p.matched_term}
                >
                  “{p.matched_term}”
                </div>
              )}
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

// Worlds whose posts get described under the romance/dialogue VLM schema
// (data/<slug>/crawl/vlm_schema.json) rather than the leaner product schema —
// ai-romance-subworld shares romance's schema (see CLAUDEphase11romance-ai-world),
// so it gets the same relationship/situation panels, not the product-world ones.
const ROMANCE_SCHEMA_WORLDS = ["romance", "ai-romance-subworld"];

// Grouped so the terms view shows one category at a time instead of six
// stacked rows — romanceOnly categories only exist because the romance-schema
// worlds add a dialogue/relationship layer the product worlds don't have (see
// CLAUDE.md's overview section).
const CATEGORIES = [
  {
    key: "core",
    label: "Products & Format",
    panels: [
      { facet: "product", label: "Winning Products" },
      { facet: "format", label: "Winning Formats" },
      { facet: "topic", label: "Winning Topics" },
    ],
  },
  {
    key: "signals",
    label: "Relationship Signals",
    romanceOnly: true,
    panels: [
      { facet: "relationship_conflict", label: "Conflict Type" },
      { facet: "relationship_stage", label: "Relationship Stage" },
      { facet: "characters_dynamic", label: "Character Dynamic" },
      { facet: "pacing_energy_arc", label: "Pacing Arc" },
    ],
  },
  {
    key: "crosstabs",
    label: "Cross-Tabs",
    romanceOnly: true,
    panels: [
      { facet: "conflict_x_register", label: "Conflict x Register" },
      { facet: "register_x_resolution", label: "Register x Resolution" },
      { facet: "register_x_advice_specificity", label: "Register x Advice Specificity" },
      { facet: "punchline_presence", label: "Punchline Presence" },
    ],
  },
  {
    key: "situations",
    label: "Situations",
    romanceOnly: true,
    panels: [
      { facet: "premise_cluster", label: "Situations (premise clusters)", fullWidth: true },
      { facet: "setting_cluster", label: "Settings (setting clusters)", fullWidth: true },
    ],
  },
  {
    key: "hooks",
    label: "Hooks & Punchlines",
    romanceOnly: true,
    panels: [
      { facet: "hook_cluster", label: "Hook Library", fullWidth: true },
      { facet: "punchline_cluster", label: "Punchline Moves", fullWidth: true },
    ],
  },
];

export default function WorldsOverview() {
  const { data: worlds } = useJson(`${API}/worlds`);
  const [slug, setSlug] = useState(null);
  const [selected, setSelected] = useState(null);
  const [view, setView] = useState("terms");
  const [category, setCategory] = useState("core");

  useEffect(() => {
    if (worlds?.length && !slug) setSlug(worlds[0].slug);
  }, [worlds, slug]);

  const availableCategories = CATEGORIES.filter((c) => !c.romanceOnly || ROMANCE_SCHEMA_WORLDS.includes(slug));

  useEffect(() => {
    if (!availableCategories.some((c) => c.key === category)) setCategory("core");
  }, [slug]); // eslint-disable-line react-hooks/exhaustive-deps -- only world switches should reset the category

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

        {view === "terms" && (
          <>
            <span className="mono" style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: 1, marginLeft: 8 }}>
              VIEW
            </span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
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
              {availableCategories.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
          </>
        )}

        <div className="mono" style={{ fontSize: 10, marginLeft: "auto" }}>
          {["terms", "accounts"].map((v) => (
            <span
              key={v}
              onClick={() => setView(v)}
              style={{
                cursor: "pointer",
                marginLeft: 10,
                color: view === v ? "var(--accent)" : "var(--text-dim)",
                textDecoration: view === v ? "underline" : "none",
              }}
            >
              {v.toUpperCase()}
            </span>
          ))}
        </div>
      </div>

      <CoverageStrip coverage={coverageSource?.coverage} />

      {slug && view === "terms" && (() => {
        const activeCategory = availableCategories.find((c) => c.key === category) || availableCategories[0];
        return (
          <div style={{ display: "flex", flex: 1, minHeight: 0, flexDirection: "column" }}>
            {activeCategory.panels[0]?.fullWidth ? (
              activeCategory.panels.map((p, i) => (
                <div
                  key={p.facet}
                  style={{ display: "flex", flex: 1, minHeight: 0, borderTop: i > 0 ? "1px solid var(--border)" : "none" }}
                >
                  <TermPanel slug={slug} facet={p.facet} label={p.label} onSelectTerm={setSelected} />
                </div>
              ))
            ) : (
              <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
                {activeCategory.panels.map((p) => (
                  <TermPanel key={p.facet} slug={slug} facet={p.facet} label={p.label} onSelectTerm={setSelected} />
                ))}
              </div>
            )}
          </div>
        );
      })()}

      {slug && view === "accounts" && <AccountsPanel slug={slug} />}

      <DrilldownGrid slug={slug} selected={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
