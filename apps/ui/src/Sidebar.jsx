export default function Sidebar({ worlds, counts, colorByWorldId, hoveredWorldId, onHoverWorld }) {
  const maxCount = Math.max(1, ...worlds.map((w) => counts[w.id] || 0));

  return (
    <div
      style={{
        flex: "0 0 260px",
        padding: 16,
        overflowY: "auto",
      }}
    >
      <div className="mono" style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: 1, marginBottom: 12 }}>
        WORLDS &middot; BASINS
      </div>

      {worlds
        .slice()
        .sort((a, b) => (counts[b.id] || 0) - (counts[a.id] || 0))
        .map((w) => {
          const count = counts[w.id] || 0;
          const color = colorByWorldId[w.id];
          const active = hoveredWorldId === w.id;
          return (
            <div
              key={w.id}
              onMouseEnter={() => onHoverWorld(w.id)}
              onMouseLeave={() => onHoverWorld(null)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "7px 6px",
                borderRadius: 3,
                cursor: "default",
                background: active ? "var(--panel-2)" : "transparent",
                boxShadow: active ? `inset 2px 0 0 ${color}` : "inset 2px 0 0 transparent",
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: 2, background: color, flexShrink: 0 }} />
              <span style={{ fontSize: 13, flex: 1 }}>{w.name}</span>
              <div style={{ width: 40, height: 4, background: "var(--grid)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{ width: `${(count / maxCount) * 100}%`, height: "100%", background: color }} />
              </div>
              <span className="mono" style={{ fontSize: 11, color: "var(--text-muted)", width: 22, textAlign: "right" }}>
                {count}
              </span>
            </div>
          );
        })}
    </div>
  );
}
