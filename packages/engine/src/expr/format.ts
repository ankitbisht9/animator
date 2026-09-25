/**
 * Number formatting for bound values, e.g. `{ "bind": "buoy_01.buoyantForce", "format": "0.0 N" }`.
 *
 * A format is `prefix` + number pattern + `suffix`. The number pattern is the
 * first run of `0`, `#`, `,` and `.`: digits after `.` set the decimals, a `,`
 * turns on thousands grouping. Examples: "0.0 N", "#,##0 kg", "v = 0.00 m/s", "0%".
 * Without a pattern the value is printed with automatic precision.
 */

export interface NumberFormat {
  prefix: string;
  suffix: string;
  decimals: number | null;
  grouping: boolean;
}

const PATTERN = /[#0][#0,]*(?:\.[#0]+)?/;

export function parseFormat(fmt: string | undefined): NumberFormat {
  if (!fmt) return { prefix: "", suffix: "", decimals: null, grouping: false };
  const m = PATTERN.exec(fmt);
  if (!m) return { prefix: "", suffix: fmt.startsWith(" ") ? fmt : ` ${fmt}`, decimals: null, grouping: false };
  const pat = m[0];
  const dot = pat.indexOf(".");
  return {
    prefix: fmt.slice(0, m.index),
    suffix: fmt.slice(m.index + pat.length),
    decimals: dot >= 0 ? pat.length - dot - 1 : 0,
    grouping: pat.includes(","),
  };
}

const MINUS = "−";

function autoDecimals(v: number): number {
  const a = Math.abs(v);
  if (a >= 100) return 0;
  if (a >= 10) return 1;
  return 2;
}

export function formatNumber(v: number, fmt: string | NumberFormat | undefined): string {
  const f = typeof fmt === "object" ? fmt : parseFormat(fmt);
  if (!Number.isFinite(v)) return `${f.prefix}—${f.suffix}`;
  const decimals = f.decimals ?? autoDecimals(v);
  let s = Math.abs(v).toFixed(decimals);
  if (f.decimals === null && s.includes(".")) s = s.replace(/\.?0+$/, "");
  if (f.grouping) {
    const [int, frac] = s.split(".");
    s = (int ?? "").replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (frac !== undefined ? `.${frac}` : "");
  }
  const isZero = Number(s.replace(/,/g, "")) === 0;
  return `${f.prefix}${v < 0 && !isZero ? MINUS : ""}${s}${f.suffix}`;
}
