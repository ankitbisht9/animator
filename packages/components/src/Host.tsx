/**
 * The host (ADR-069): a presenter in a small round window in the frame's bottom-left corner, beside
 * the captions and outside the stage, so it never covers the picture. It talks as the voice does (its
 * mouth opens on each syllable of the word being said; without a voice, while the caption is read),
 * blinks, breathes, raises its brows when something is highlighted, and looks at what is changing on
 * screen, which draws the learner's eye there too. Everything follows from the frame's time:
 * deterministic, as every frame of a lesson must be (ADR-006).
 */
import { apply, CANVAS, type CompiledScene, cameraMatrix, type FrameState, type Theme } from "@animator/engine";
import type { ReactNode } from "react";
import type { SceneNarration } from "./types";
import { fmt, mixHex, withAlpha } from "./util";

export const HOST_LOOKS = ["asha", "arjun"] as const;
export type HostLook = (typeof HOST_LOOKS)[number];

export function isHostLook(v: unknown): v is HostLook {
  return typeof v === "string" && (HOST_LOOKS as readonly string[]).includes(v);
}

interface Look {
  skin: string;
  shade: string;
  hair: string;
  shirt: string;
  lips: string;
  iris: string;
  long: boolean;
}

const LOOKS: Record<HostLook, Look> = {
  asha: {
    skin: "#c98d66",
    shade: "#a96f4c",
    hair: "#2a1b14",
    shirt: "#2f6f8f",
    lips: "#a24f4a",
    iris: "#3b2618",
    long: true,
  },
  arjun: {
    skin: "#b97f58",
    shade: "#98623f",
    hair: "#1d1410",
    shirt: "#4657b0",
    lips: "#8f4a42",
    iris: "#2e1f14",
    long: false,
  },
};

/** The window: a circle of this diameter, this far from the frame's left and bottom edges. */
export const HOST_SIZE = 128;
const MARGIN = 14;
export const HOST_BOX = { x: MARGIN, y: CANVAS.height - MARGIN - HOST_SIZE, w: HOST_SIZE, h: HOST_SIZE };

/** A word being said: its text (for its vowels) and how far through it the voice is (0..1). */
export interface Saying {
  word: string;
  u: number;
}

const plain = (text: string): string[] =>
  text
    .replace(/\*\*/g, "")
    .split(/\s+/)
    .filter((w) => w.length > 0);

/** The word the voice is saying at this frame, if any. */
export function sayingAt(
  compiled: CompiledScene,
  frame: FrameState,
  narration: SceneNarration | null | undefined,
): Saying | null {
  const cap = frame.caption;
  if (!cap) return null;
  const words = plain(cap.text);
  const speech = narration?.beats.find((b) => b.id === cap.beatId);
  const beat = compiled.beats.find((b) => b.id === cap.beatId);
  if (speech && beat && speech.words.length > 0) {
    const elapsed = frame.t - beat.start;
    for (let i = 0; i < speech.words.length; i++) {
      const [a, z] = speech.words[i] as [number, number];
      if (elapsed >= a && elapsed < z) return { word: words[i] ?? "a", u: (elapsed - a) / Math.max(0.05, z - a) };
    }
    return null;
  }
  // No voice: the caption is read at an even pace, finishing at 88% of the beat (as CaptionBar).
  if (words.length === 0 || cap.progress >= 0.88) return null;
  const at = (cap.progress / 0.88) * words.length;
  const i = Math.floor(at);
  return { word: words[i] ?? "a", u: at - i };
}

type Shape = "a" | "e" | "o";

/** How open the mouth is (0..1) and its shape, from the syllables of the word being said. */
export function mouthOf(saying: Saying | null): { open: number; shape: Shape } {
  if (!saying) return { open: 0, shape: "a" };
  const w = saying.word.toLowerCase().replace(/[^a-zÀ-ɏ]/g, "");
  const groups = w.match(/[aeiouy]+/g) ?? ["a"];
  const n = groups.length;
  const u = Math.max(0, Math.min(0.999, saying.u));
  if (/^[mbp]/.test(w) && u < 0.12) return { open: 0, shape: "a" }; // lips meet first
  const k = Math.floor(u * n);
  const v = (groups[k] ?? "a")[0] ?? "a";
  const shape: Shape = v === "o" || v === "u" ? "o" : v === "e" || v === "i" || v === "y" ? "e" : "a";
  return { open: Math.abs(Math.sin(Math.PI * n * u)) ** 0.7, shape };
}

/** Eyes 1 open .. 0 shut: a blink every 3 to 5 seconds, at times fixed by the scene's seed. */
export function eyesOpen(t: number, seed: number): number {
  let open = 1;
  for (let k = Math.floor(t / 4) - 1; k <= Math.floor(t / 4) + 1; k++) {
    const r = Math.sin((k + 1) * 12.9898 + seed * 78.233) * 43758.5453;
    const start = k * 4 + (r - Math.floor(r)) * 1.6;
    const d = t - start;
    if (d >= 0 && d < 0.16) open = Math.min(open, Math.abs(d - 0.08) / 0.08);
  }
  return open;
}

/** Where the host looks and how lively the moment is (0..1): at what is highlighted, else at what is
 * being drawn, written or shown, else at the learner. `gx`, `gy`: the pupils' offset toward it. */
export function gaze(frame: FrameState): { gx: number; gy: number; lively: number } {
  const cam = cameraMatrix(frame.camera);
  let best: { score: number; at: { x: number; y: number }; lively: number } | null = null;
  for (const o of frame.objects) {
    if (o.alpha < 0.05 || o.bbox.w <= 0 || o.bbox.h <= 0) continue;
    const emph = Math.max(o.emph?.amount ?? 0, ...(o.marks ?? []).map((m) => m.amount));
    const changing = o.draw < 1 || o.reveal < 1 || o.presence < 1 ? 0.5 : 0;
    const score = emph > 0 ? 1 + emph : changing;
    if (score <= 0 || (best && best.score >= score)) continue;
    const c = { x: o.bbox.x + o.bbox.w / 2, y: o.bbox.y + o.bbox.h / 2 };
    best = { score, at: o.layer === "overlay" ? c : apply(cam, c), lively: emph };
  }
  if (!best) return { gx: 0, gy: 0, lively: 0 };
  const dx = best.at.x - (HOST_BOX.x + HOST_BOX.w / 2);
  const dy = best.at.y - (HOST_BOX.y + HOST_BOX.h / 2);
  const len = Math.hypot(dx, dy) || 1;
  return { gx: (dx / len) * 2.3, gy: (dy / len) * 1.6, lively: best.lively };
}

export interface HostProps {
  look: HostLook;
  compiled: CompiledScene;
  frame: FrameState;
  narration?: SceneNarration | null;
  theme: Theme;
}

export function Host({ look, compiled, frame, narration, theme }: HostProps) {
  const L = LOOKS[look];
  const t = frame.t;
  const { open, shape } = mouthOf(sayingAt(compiled, frame, narration));
  const eyes = Math.max(0.06, eyesOpen(t, compiled.seed));
  // The pupils move toward what is happening; the head turns a little toward it.
  const { gx, gy, lively } = gaze(frame);
  const talking = open > 0 ? 1 : 0;
  const bob = 1.3 * Math.sin((2 * Math.PI * t) / 3.6) + talking * 0.6 * Math.sin(2 * Math.PI * t * 2.1);
  const tilt = 1.6 * Math.sin((2 * Math.PI * t) / 6.1) + gx * 1.1;
  const brow = Math.min(1, lively) * 3;
  const k = HOST_SIZE / 200;
  const bg = theme.mode === "dark" ? mixHex(theme.colors.surface, "#ffffff", 0.06) : theme.colors.surface;
  const ring = withAlpha(theme.colors.accent, 0.55);

  const eye = (ex: number): ReactNode => (
    <g transform={`translate(${ex} 92) scale(1 ${fmt(eyes, 3)})`}>
      <ellipse rx={6.4} ry={4.6} fill="#fbf7f2" />
      <circle cx={fmt(gx)} cy={fmt(gy * 0.6)} r={3.4} fill={L.iris} />
      <circle cx={fmt(gx)} cy={fmt(gy * 0.6)} r={1.7} fill="#120c09" />
      <circle cx={fmt(gx + 1.1)} cy={fmt(gy * 0.6 - 1.2)} r={0.9} fill="#ffffff" />
      <ellipse rx={6.4} ry={4.6} fill="none" stroke={L.shade} strokeWidth={0.9} />
    </g>
  );

  let mouth: ReactNode;
  if (open < 0.08) {
    mouth = <path d="M92 116 Q100 121 108 116" fill="none" stroke={L.lips} strokeWidth={2.3} strokeLinecap="round" />;
  } else {
    const w = shape === "o" ? 5.8 : shape === "e" ? 10.5 : 8.5;
    const h = (shape === "e" ? 4.4 : shape === "o" ? 7.6 : 8.8) * open + 1.4;
    const top = 116 - h * 0.45;
    const bottom = 116 + h * 0.85;
    const outline = `M${fmt(100 - w)} 116 Q100 ${fmt(top - 1.5)} ${fmt(100 + w)} 116 Q100 ${fmt(bottom + 2)} ${fmt(100 - w)} 116 Z`;
    mouth = (
      <g>
        <path d={outline} fill="#5a1d1d" stroke={L.lips} strokeWidth={1.6} strokeLinejoin="round" />
        <path
          d={`M${fmt(100 - w * 0.7)} ${fmt(top + 0.6)} Q100 ${fmt(top - 0.4)} ${fmt(100 + w * 0.7)} ${fmt(top + 0.6)}`}
          stroke="#f7f1ea"
          strokeWidth={Math.min(2.2, h * 0.3)}
          fill="none"
          strokeLinecap="round"
        />
        {h > 4 && (
          <ellipse cx={100} cy={fmt(bottom - 1.2)} rx={fmt(w * 0.5)} ry={fmt(Math.min(2.2, h * 0.22))} fill="#c4665d" />
        )}
      </g>
    );
  }

  const hairBack = L.long ? (
    <path d="M60 92 C54 48 146 48 140 92 L146 152 C130 164 70 164 54 152 Z" fill={L.hair} />
  ) : null;
  const hairFront = L.long ? (
    <path
      d="M66 88 C64 58 86 46 104 48 C123 50 137 63 134 88 C128 74 117 65 101 66 C88 67 76 74 66 88 Z"
      fill={L.hair}
    />
  ) : (
    <path
      d="M66 86 C63 57 84 44 102 45 C122 46 139 58 134 86 C131 74 123 66 113 63 C102 68 85 65 73 73 C70 77 68 81 66 86 Z"
      fill={L.hair}
    />
  );

  return (
    <g transform={`translate(${fmt(HOST_BOX.x)} ${fmt(HOST_BOX.y)}) scale(${fmt(k, 4)})`} data-host={look}>
      <defs>
        <clipPath id="host-window">
          <circle cx={100} cy={100} r={97} />
        </clipPath>
        <radialGradient id="host-bg" cx="50%" cy="38%" r="70%">
          <stop offset="0%" stopColor={mixHex(bg, theme.colors.accent, 0.1)} />
          <stop offset="100%" stopColor={mixHex(bg, theme.colors.accent, 0.22)} />
        </radialGradient>
      </defs>
      <circle cx={100} cy={100} r={99} fill={bg} filter="url(#fx-shadow)" />
      <g clipPath="url(#host-window)">
        <circle cx={100} cy={100} r={99} fill="url(#host-bg)" />
        {/* The presenter, zoomed so the face fills the window (head and shoulders). */}
        <g transform="translate(100 126) scale(1.24) translate(-100 -126)">
          <g transform={`translate(0 ${fmt(bob * 0.35)})`}>
            <path d="M20 204 C24 170 52 152 82 148 L118 148 C148 152 176 170 180 204 Z" fill={L.shirt} />
            <path d="M82 148 L118 148 C114 162 86 162 82 148 Z" fill={mixHex(L.shirt, "#000000", 0.18)} />
          </g>
          <g transform={`translate(0 ${fmt(bob)}) rotate(${fmt(tilt, 2)} 100 140)`}>
            {hairBack}
            <path d="M88 118 L88 150 Q100 158 112 150 L112 118 Z" fill={L.shade} />
            <ellipse cx={66.5} cy={93} rx={5.5} ry={8.5} fill={L.shade} />
            <ellipse cx={133.5} cy={93} rx={5.5} ry={8.5} fill={L.shade} />
            <path
              d="M67 88 C67 62 82 50 100 50 C118 50 133 62 133 88 C133 112 120 131 100 133 C80 131 67 112 67 88 Z"
              fill={L.skin}
            />
            <circle cx={80} cy={106} r={6} fill="#e07a6a" opacity={0.16} />
            <circle cx={120} cy={106} r={6} fill="#e07a6a" opacity={0.16} />
            {eye(87)}
            {eye(113)}
            <g transform={`translate(0 ${fmt(-brow)})`}>
              <path d="M79 83 Q87 78.5 95 81.5" fill="none" stroke={L.hair} strokeWidth={2.6} strokeLinecap="round" />
              <path
                d="M105 81.5 Q113 78.5 121 83"
                fill="none"
                stroke={L.hair}
                strokeWidth={2.6}
                strokeLinecap="round"
              />
            </g>
            <path
              d="M100 95 Q98.5 103 96.5 105.5 Q99.5 107.5 103 105.5"
              fill="none"
              stroke={L.shade}
              strokeWidth={1.5}
              strokeLinecap="round"
            />
            {mouth}
            {hairFront}
          </g>
        </g>
      </g>
      <circle cx={100} cy={100} r={98} fill="none" stroke={ring} strokeWidth={4} />
    </g>
  );
}
