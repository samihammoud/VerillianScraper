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

// One PUT endpoint backs both views (topology.annotations, keyed by text) —
// patterns key on the anchor post id, terms on slug:facet:canon_term.
function saveAnnotation(key, patch) {
  return fetch(`${API}/worlds/annotations/${encodeURIComponent(key)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

// Reorder = rewrite sort_order for the whole visible list from its new index
// order, rather than swapping two values — same number of round trips in
// practice and it can't drift out of a total order.
function reorder(items, index, delta, done) {
  const next = [...items];
  const [moved] = next.splice(index, 1);
  next.splice(index + delta, 0, moved);
  return Promise.all(next.map((it, i) => saveAnnotation(it.key, { sort_order: i }))).then(done);
}

const iconStyle = (on) => ({ cursor: "pointer", userSelect: "none", flexShrink: 0, color: on ? "var(--accent)" : "var(--text-dim)" });

function Star({ on, onClick }) {
  return (
    <span title={on ? "unfavorite" : "favorite"} onClick={onClick} style={{ ...iconStyle(on), fontSize: 13 }}>
      {on ? "★" : "☆"}
    </span>
  );
}

function Check({ on, onClick }) {
  return (
    <span
      title={on ? "mark unreviewed" : "mark reviewed"}
      onClick={onClick}
      className="mono"
      style={{ ...iconStyle(on), fontSize: 12, opacity: on ? 1 : 0.45 }}
    >
      ✓
    </span>
  );
}

function MoveArrows({ index, count, onMove }) {
  return (
    <span className="mono" style={{ fontSize: 10, display: "inline-flex", gap: 6, flexShrink: 0 }}>
      <span onClick={() => index > 0 && onMove(-1)} style={iconStyle(index > 0)}>↑</span>
      <span onClick={() => index < count - 1 && onMove(1)} style={iconStyle(index < count - 1)}>↓</span>
    </span>
  );
}

// Explicit save, not commit-on-blur: blur never fires when React unmounts the
// textarea (switching world/tab/category), which silently dropped the edit.
// The button doubles as the dirty indicator, so an untouched box never PUTs.
function NoteBox({ value, saved, onChange, onSave, rows = 2 }) {
  const dirty = value !== saved;
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "flex-start", width: "100%" }} onClick={(e) => e.stopPropagation()}>
      <textarea
        value={value}
        placeholder="note…"
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") onSave();
        }}
        rows={rows}
        className="mono"
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 11,
          resize: "vertical",
          background: "transparent",
          color: "var(--text)",
          border: `1px solid ${dirty ? "var(--accent)" : "var(--border)"}`,
          padding: 6,
        }}
      />
      <button
        onClick={onSave}
        disabled={!dirty}
        title={dirty ? "save note (⌘↵)" : "no unsaved changes"}
        className="mono"
        style={{
          flexShrink: 0,
          fontSize: 10,
          letterSpacing: 1,
          padding: "5px 9px",
          borderRadius: 3,
          cursor: dirty ? "pointer" : "default",
          background: dirty ? "var(--accent)" : "transparent",
          color: dirty ? "var(--accent-ink)" : "var(--text-dim)",
          border: `1px solid ${dirty ? "var(--accent)" : "var(--border)"}`,
        }}
      >
        {dirty ? "SAVE" : "SAVED"}
      </button>
    </div>
  );
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

// 200 is the API's own ceiling (worlds.py's Query(..., le=200)); raising this
// further means raising that too. 25 hid all but the loudest terms — romance
// alone has ~500 topic clusters clearing the support floor, so the tail that
// makes a facet worth browsing was never reachable.
const TERM_LIMIT = 200;

// Cluster-size bands. MIN_POSTS=3 is the server's floor, so "emerging" (3-9) is
// a term several creators reached for that hasn't spread yet, and "established"
// (25+) is a settled pattern. Applied server-side via min_posts/max_posts so the
// band composes with sorting and paging — filtering the page client-side would
// silently shrink a page of 200 to whatever happened to match.
const SIZE_BANDS = [
  { key: "all", label: "ALL" },
  { key: "emerging", label: "3-9", max: 9 },
  { key: "growing", label: "10-24", min: 10, max: 24 },
  { key: "established", label: "25+", min: 25 },
];

// Accumulating pager over /overview. Pages are appended rather than replaced, so
// "load more" grows one list instead of swapping pages — the panel is a ranked
// list you scan top-down, and paging it like a table would break that reading.
// Correctness rests on the endpoint's total ordering (worlds.py): without a
// tiebreaker, tied rows drift between queries and offset paging duplicates and
// drops terms.
function usePagedJson(baseUrl, pageSize) {
  const [pages, setPages] = useState([]);
  const [total, setTotal] = useState(0);
  const [coverage, setCoverage] = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchPage = (offset) => {
    if (!baseUrl) return;
    setLoading(true);
    fetch(`${baseUrl}&limit=${pageSize}&offset=${offset}&_=${Date.now()}`)
      .then((r) => r.json())
      .then((d) => {
        setTotal(d.total ?? 0);
        setCoverage(d.coverage ?? null);
        // Index by offset so an out-of-order response can't interleave rows,
        // and always merge — refresh() re-fetches page 0 alongside the others,
        // so clearing here would race the in-flight later pages away.
        setPages((prev) => {
          const next = [...prev];
          next[offset / pageSize] = d.terms || [];
          return next;
        });
      })
      .finally(() => setLoading(false));
  };

  // baseUrl carries facet/sort/favorites, so any of them changing restarts the
  // list from page 0 rather than appending onto a stale ordering. This is the
  // only place pages are cleared.
  useEffect(() => {
    setPages([]);
    setTotal(0);
    fetchPage(0);
  }, [baseUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  const terms = pages.flat().filter(Boolean);
  return {
    terms,
    total,
    coverage,
    loading,
    hasMore: terms.length < total,
    loadMore: () => fetchPage(terms.length),
    loadAll: () => {
      for (let o = terms.length; o < total; o += pageSize) fetchPage(o);
    },
    // Re-fetch the pages already on screen, in place. Starring a term has to
    // re-read the server's ordering (favorites pin to the top), but scrolled-in
    // pages must not vanish underneath the click — so this reloads what is
    // loaded instead of resetting to page 0.
    refresh: () => {
      const loadedPages = Math.max(pages.length, 1);
      for (let i = 0; i < loadedPages; i += 1) fetchPage(i * pageSize);
    },
  };
}

function TermPanel({ slug, facet, label, onSelectTerm }) {
  const [sort, setSort] = useState("lift");
  // Which multiplier the rows show. Tracks the sort, except VARIANTS — that's a
  // review ordering, not a metric, so it leaves the last metric on screen
  // instead of silently snapping the number back to lift.
  const [metric, setMetric] = useState("lift");
  const [band, setBand] = useState("all");
  const [notes, setNotes] = useState({}); // local edits, keyed by term key
  const [openNote, setOpenNote] = useState(null);
  const [favOnly, setFavOnly] = useState(false);
  const activeBand = SIZE_BANDS.find((b) => b.key === band) || SIZE_BANDS[0];
  // favorites_only filters server-side, so a starred term that has fallen below
  // the first page by lift still shows up here.
  const {
    terms, total, loading, hasMore, loadMore, loadAll, refresh,
  } = usePagedJson(
    slug
      ? `${API}/worlds/${slug}/overview?facet=${facet}&sort=${sort}&favorites_only=${favOnly}` +
        (activeBand.min ? `&min_posts=${activeBand.min}` : "") +
        (activeBand.max ? `&max_posts=${activeBand.max}` : "")
      : null,
    TERM_LIMIT
  );
  // Lift on a 3-post cluster is a noisy estimate of a real thing, not a
  // measurement — the small bands are leads to open, which is why every row
  // still links straight through to its posts.
  const refetch = refresh;

  // Sort order comes from the server (favorites first, then manual sort_order,
  // then the chosen metric) so a starred term can't fall out of the top TERM_LIMIT.
  const save = (key, patch) => saveAnnotation(key, patch).then(refetch);
  const saveNote = (key, note) =>
    saveAnnotation(key, { note }).then(() => {
      setNotes((n) => {
        const { [key]: _dropped, ...rest } = n; // server value is now authoritative
        return rest;
      });
      refetch();
    });

  return (
    <div
      style={{
        minWidth: 0,
        borderRight: "1px solid var(--border)",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        overflow: "hidden",
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
          gap: 8,
          // fixed height so a label that wraps to two lines doesn't push its
          // panel's rows out of alignment with its neighbours in the grid
          minHeight: 46,
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, minWidth: 0 }}>
          {label}
          <span className="mono" style={{ fontSize: 10, color: "var(--text-dim)", fontWeight: 400, marginLeft: 8 }}>
            {activeBand.key !== "all" && `${activeBand.label} posts · `}
            {terms.length}
            {total > terms.length ? ` / ${total}` : ""}
          </span>
        </span>
        <div className="mono" style={{ fontSize: 10, display: "flex", alignItems: "center", gap: 10 }}>
          <span onClick={() => setFavOnly(!favOnly)} style={{ ...iconStyle(favOnly), letterSpacing: 1 }}>
            {favOnly ? "★ ONLY" : "☆ ALL"}
          </span>
          {["lift", "volume", "account", "variants"].map((s) => (
            <span
              key={s}
              onClick={() => { setSort(s); if (s !== "variants") setMetric(s); }}
              style={{
                cursor: "pointer",
                marginLeft: 10,
                // underline = what the list is ordered by, accent = what the
                // multiplier column is showing. Same control, two roles, and
                // under VARIANTS they land on different words.
                color: sort === s || metric === s ? "var(--accent)" : "var(--text-dim)",
                textDecoration: sort === s ? "underline" : "none",
              }}
            >
              {s.toUpperCase()}
            </span>
          ))}
        </div>
      </div>
      <div
        className="mono"
        style={{
          display: "flex", gap: 10, alignItems: "center", padding: "5px 14px",
          borderBottom: "1px solid var(--border)", fontSize: 10, flex: "0 0 auto",
          color: "var(--text-dim)",
        }}
      >
        <span style={{ letterSpacing: 1 }}>SIZE</span>
        {SIZE_BANDS.map((b) => (
          <span
            key={b.key}
            onClick={() => setBand(b.key)}
            style={{
              cursor: "pointer",
              color: band === b.key ? "var(--accent)" : "var(--text-dim)",
              textDecoration: band === b.key ? "underline" : "none",
            }}
          >
            {b.label}
          </span>
        ))}
      </div>
      <div style={{ overflowY: "auto", flex: 1 }}>
        {terms.map((t, i) => {
          const editing = openNote === t.key;
          const primary = metric === "volume" ? t.volume_ratio : metric === "account" ? t.account_view_ratio : t.view_ratio;
          return (
            <div key={t.key} style={{ borderBottom: "1px solid var(--grid)", opacity: t.reviewed ? 0.6 : 1 }}>
              <div
                title={`variants: ${t.variants.join(", ")}`}
                onClick={() => onSelectTerm({ facet, canon_term: t.canon_term, label })}
                style={{ display: "flex", alignItems: "center", gap: 6, padding: "7px 12px", cursor: "pointer", minWidth: 0 }}
              >
                <Star on={t.favorite} onClick={(e) => { e.stopPropagation(); save(t.key, { favorite: !t.favorite }); }} />
                <Check on={t.reviewed} onClick={(e) => { e.stopPropagation(); save(t.key, { reviewed: !t.reviewed }); }} />
                <span className="mono" style={{ fontSize: 12, color: "var(--accent)", flexShrink: 0 }}>
                  {primary.toFixed(1)}x
                </span>
                <span style={{ fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {t.canon_term}
                </span>
                {/* per-account multiplier stays visible whatever the sort is — it's
                    the number that says the term travels across creators rather
                    than riding one account's outlier. */}
                <span
                  className="mono"
                  title="per-account multiplier (account_view_ratio)"
                  style={{ fontSize: 10, color: "var(--text-muted)", flexShrink: 0 }}
                >
                  {t.account_view_ratio.toFixed(1)}x/acct
                </span>
                <span className="mono" style={{ fontSize: 10, color: "var(--text-dim)", flexShrink: 0 }}>
                  {t.n_posts}p/{t.n_accounts}a{facet === "product" ? `/${t.n_hero}h` : ""}
                  {/* variant count: how many raw phrasings this label absorbed.
                      A big number is worth a look — single-link clustering
                      chains loosely related phrasings under one canon term. */}
                  {t.variants.length > 1 && (
                    <span style={{ color: t.variants.length >= 20 ? "var(--accent)" : "var(--text-dim)" }}>
                      {" "}·{t.variants.length}v
                    </span>
                  )}
                </span>
                <span
                  title={editing ? "close note" : "edit note"}
                  onClick={(e) => { e.stopPropagation(); setOpenNote(editing ? null : t.key); }}
                  style={{ ...iconStyle(editing || !!t.note), fontSize: 11 }}
                >
                  ✎
                </span>
                <MoveArrows index={i} count={terms.length} onMove={(d) => reorder(terms, i, d, refetch)} />
              </div>
              {editing ? (
                <div style={{ padding: "0 14px 8px" }}>
                  <NoteBox
                    value={notes[t.key] ?? t.note}
                    saved={t.note}
                    onChange={(v) => setNotes({ ...notes, [t.key]: v })}
                    onSave={() => saveNote(t.key, notes[t.key] ?? t.note)}
                    rows={2}
                  />
                </div>
              ) : (
                /* read-only one-liner so a note is legible while scrolling without
                   costing a textarea's worth of height per row */
                (notes[t.key] ?? t.note).trim() && (
                  <div
                    className="mono"
                    title={notes[t.key] ?? t.note}
                    onClick={(e) => { e.stopPropagation(); setOpenNote(t.key); }}
                    style={{
                      padding: "0 14px 7px 36px",
                      fontSize: 10,
                      color: "var(--text-muted)",
                      cursor: "pointer",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    ✎ {(notes[t.key] ?? t.note).trim()}
                    {notes[t.key] !== undefined && notes[t.key] !== t.note && (
                      <span style={{ color: "var(--accent)" }}> · UNSAVED</span>
                    )}
                  </div>
                )
              )}
            </div>
          );
        })}
        {!loading && !terms.length && (
          <div className="mono" style={{ padding: 14, fontSize: 11, color: "var(--text-dim)" }}>
            {favOnly
              ? "nothing favorited yet — star a term to collect it here"
              : activeBand.key !== "all"
                ? `no clusters in the ${activeBand.label} band`
                : "no ranked terms yet"}
          </div>
        )}
      </div>
      {hasMore && (
        <div
          className="mono"
          style={{
            display: "flex", gap: 14, justifyContent: "center", alignItems: "center",
            padding: "8px 12px", borderTop: "1px solid var(--border)", fontSize: 10,
            letterSpacing: 1, flex: "0 0 auto",
          }}
        >
          <span onClick={loading ? undefined : loadMore} style={iconStyle(!loading)}>
            {loading ? "LOADING…" : `MORE (${total - terms.length} LEFT)`}
          </span>
          <span onClick={loading ? undefined : loadAll} style={iconStyle(!loading)}>ALL</span>
        </div>
      )}
    </div>
  );
}

const notesOf = (account) =>
  account.patterns
    .map((p) => p.note.trim())
    .filter(Boolean)
    .join(" · ");

function AccountsPanel({ slug }) {
  const [rev, setRev] = useState(0);
  const { data } = useJson(slug ? `${API}/worlds/${slug}/accounts?r=${rev}` : null);
  const accounts = data || [];
  const [openHandle, setOpenHandle] = useState(null);
  const [showHidden, setShowHidden] = useState(false);
  const [favOnly, setFavOnly] = useState(false);
  const [notes, setNotes] = useState({}); // local edits, keyed by pattern key

  const save = (key, patch) => saveAnnotation(key, patch).then(() => setRev((r) => r + 1));
  const saveNote = (key, note) =>
    saveAnnotation(key, { note }).then(() => {
      setNotes((n) => {
        const { [key]: _dropped, ...rest } = n; // server value is now authoritative
        return rest;
      });
      setRev((r) => r + 1);
    });

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
      <div className="mono" style={{ fontSize: 10, display: "flex", gap: 16, marginBottom: 12 }}>
        <span onClick={() => setFavOnly(!favOnly)} style={{ ...iconStyle(favOnly), letterSpacing: 1 }}>
          {favOnly ? "★ ONLY" : "☆ ALL"}
        </span>
        <span
          onClick={() => setShowHidden(!showHidden)}
          style={{ ...iconStyle(showHidden), letterSpacing: 1 }}
        >
          {showHidden ? "HIDING NOTHING" : "SHOW DELETED"}
        </span>
      </div>
      {accounts.map((a) => {
        const visible = a.patterns.filter((p) => (showHidden || !p.hidden) && (!favOnly || p.favorite));
        if (favOnly && !visible.length) return null;
        return (
          <div key={a.handle} style={{ marginBottom: 18, borderBottom: "1px solid var(--grid)", paddingBottom: 14 }}>
            <div
              onClick={() => setOpenHandle(openHandle === a.handle ? null : a.handle)}
              style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}
            >
              <span style={{ fontSize: 14, fontWeight: 600 }}>@{a.handle}</span>
              <span className="mono" style={{ fontSize: 10, color: "var(--text-muted)" }}>
                {a.n_peaks} peaks / {visible.length} pattern{visible.length === 1 ? "" : "s"}
              </span>
              {a.patterns.some((p) => p.favorite) && (
                <span style={{ color: "var(--accent)", fontSize: 12 }} title="has a favorited pattern">
                  ★
                </span>
              )}
              {/* notes read in the scroll list without expanding the account */}
              {notesOf(a) && (
                <span
                  className="mono"
                  title={notesOf(a)}
                  style={{
                    fontSize: 10,
                    color: "var(--text-muted)",
                    flex: 1,
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  ✎ {notesOf(a)}
                </span>
              )}
            </div>
            {openHandle === a.handle &&
              visible.map((p, i) => (
                <div
                  key={p.key}
                  style={{
                    marginTop: 10,
                    marginLeft: 4,
                    display: "flex",
                    gap: 12,
                    opacity: p.hidden ? 0.4 : 1,
                  }}
                >
                  <div style={{ flex: "1 1 0", minWidth: 0 }}>
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
                  <div style={{ flex: "0 0 260px", display: "flex", flexDirection: "column", gap: 6 }}>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.4 }}>
                      {p.about || "no description available"}
                    </div>
                    <NoteBox
                      value={notes[p.key] ?? p.note}
                      saved={p.note}
                      onChange={(v) => setNotes({ ...notes, [p.key]: v })}
                      onSave={() => saveNote(p.key, notes[p.key] ?? p.note)}
                    />
                    <div className="mono" style={{ fontSize: 10, display: "flex", gap: 10, alignItems: "center" }}>
                      <Star on={p.favorite} onClick={() => save(p.key, { favorite: !p.favorite })} />
                      <Check on={p.reviewed} onClick={() => save(p.key, { reviewed: !p.reviewed })} />
                      <MoveArrows index={i} count={visible.length} onMove={(d) => reorder(visible, i, d, () => setRev((r) => r + 1))} />
                      <span
                        onClick={() => save(p.key, { hidden: !p.hidden })}
                        style={{ cursor: "pointer", color: "var(--text-dim)", marginLeft: "auto" }}
                      >
                        {p.hidden ? "RESTORE" : "DELETE"}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
          </div>
        );
      })}
      {data && !accounts.length && (
        <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
          no accounts with a repeating peak pattern yet
        </div>
      )}
      {data && accounts.length > 0 && favOnly && !accounts.some((a) => a.patterns.some((p) => p.favorite)) && (
        <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
          nothing favorited yet — star a pattern to collect it here
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
    // Phase 15 — the same ranking restricted to product instances the creator
    // framed as a deal (products[].value_signal.acquisition_cost). Not gated to
    // discount-shopping: routing is topical, so discount posts land in
    // fashion/food/beauty/... too, and these facets are populated wherever
    // value_signal is. Worlds described under a schema without it just show
    // "no ranked terms yet".
    key: "discounts",
    label: "Discount Stories",
    panels: [
      { facet: "discount_product", label: "Discounted Products" },
      { facet: "discount_format", label: "Formats Showcasing Them" },
      { facet: "discount_format_trait", label: "Format Traits" },
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
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))",
                  gridAutoRows: "minmax(0, 1fr)",
                  flex: 1,
                  minHeight: 0,
                }}
              >
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
