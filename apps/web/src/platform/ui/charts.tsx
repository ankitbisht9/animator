/** Small SVG charts (no library): bars, an activity calendar, a donut. Bars grow in on first paint. */

export function BarChart({
  data,
  height = 160,
  unit = "",
  label,
}: {
  data: { label: string; value: number; hint?: string }[];
  height?: number;
  unit?: string;
  label: string;
}) {
  const max = Math.max(1, ...data.map((d) => d.value));
  const w = 100 / Math.max(1, data.length);
  return (
    <figure className="ui-bars" aria-label={label}>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" role="img" aria-label={label} style={{ height }}>
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} x1="0" x2="100" y1={height * g} y2={height * g} className="ui-bars-grid" />
        ))}
        {data.map((d, i) => {
          const h = (d.value / max) * (height - 6);
          return (
            <rect
              key={d.label}
              x={i * w + w * 0.18}
              y={height - h}
              width={w * 0.64}
              height={Math.max(h, d.value > 0 ? 2 : 0)}
              rx="1.2"
              className="ui-bars-bar"
              style={{ animationDelay: `${i * 25}ms` }}
            >
              <title>{`${d.hint ?? d.label}: ${d.value}${unit}`}</title>
            </rect>
          );
        })}
      </svg>
      <figcaption className="ui-bars-axis">
        {data.map((d, i) => (
          <span key={d.label} style={{ visibility: data.length > 10 && i % 2 ? "hidden" : "visible" }}>
            {d.label}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

/** Weeks as columns (Monday at the top), the last `days` days; darker red for more minutes. */
export function Heatmap({ days, label }: { days: { day: string; minutes: number }[]; label: string }) {
  const first = days[0] ? new Date(`${days[0].day}T00:00:00`) : new Date();
  const offset = (first.getDay() + 6) % 7; // Monday = 0
  const cells = [...Array(offset).fill(null), ...days] as ({ day: string; minutes: number } | null)[];
  const weeks: (typeof cells)[] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  const level = (m: number) => (m <= 0 ? 0 : m < 5 ? 1 : m < 15 ? 2 : m < 30 ? 3 : 4);
  const size = 13;
  const gap = 3;
  return (
    <div className="ui-heatmap">
      <svg
        width={weeks.length * (size + gap)}
        height={7 * (size + gap)}
        role="img"
        aria-label={label}
        viewBox={`0 0 ${weeks.length * (size + gap)} ${7 * (size + gap)}`}
      >
        {weeks.map((week, wi) =>
          week.map((c, di) =>
            c ? (
              <rect
                key={c.day}
                x={wi * (size + gap)}
                y={di * (size + gap)}
                width={size}
                height={size}
                rx="3"
                className={`hm-${level(c.minutes)}`}
              >
                <title>{`${c.day}: ${c.minutes} min`}</title>
              </rect>
            ) : null,
          ),
        )}
      </svg>
      <div className="ui-heatmap-legend" aria-hidden="true">
        Less
        {[0, 1, 2, 3, 4].map((l) => (
          <span key={l} className={`hm-${l}`} />
        ))}
        More
      </div>
    </div>
  );
}

export function Donut({
  parts,
  size = 140,
  label,
}: {
  parts: { label: string; value: number; color: string }[];
  size?: number;
  label: string;
}) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  const r = size / 2 - 12;
  const c = 2 * Math.PI * r;
  let acc = 0;
  return (
    <div className="ui-donut">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth="18" className="ui-ring-track" />
        {parts.map((p) => {
          const len = (p.value / total) * c;
          const el = (
            <circle
              key={p.label}
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={p.color}
              strokeWidth="18"
              strokeDasharray={`${len} ${c - len}`}
              strokeDashoffset={-acc}
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
            >
              <title>{`${p.label}: ${p.value}`}</title>
            </circle>
          );
          acc += len;
          return el;
        })}
      </svg>
      <ul>
        {parts.map((p) => (
          <li key={p.label}>
            <span style={{ background: p.color }} />
            {p.label}
            <b>{p.value}</b>
          </li>
        ))}
      </ul>
    </div>
  );
}
