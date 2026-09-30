/**
 * <SceneView>: FrameState -> SVG. The single renderer used by the browser
 * player, the DSL playground, the headless /render page (export, visual critic)
 * and golden-frame tests (P2). It draws; it never computes animation.
 */
import {
  CANVAS,
  type CompiledScene,
  cameraMatrix,
  type FrameObject,
  type FrameState,
  getTheme,
  type Theme,
} from "@animator/engine";
import { type ReactNode, useMemo } from "react";
import { Background } from "./Background";
import { CaptionBar } from "./Caption";
import { EmphasisBehind, EmphasisFront } from "./Emphasis";
import { FRONT_RENDERERS, RENDERERS } from "./renderers";
import type { SceneNarration } from "./types";
import { fmt, matrixAttr, withAlpha } from "./util";

export interface SceneViewProps {
  compiled: CompiledScene;
  frame: FrameState;
  width?: number | string;
  height?: number | string;
  /** Override the scene's caption setting (player toggle). */
  captions?: boolean;
  /** Enable pointer hover on objects (player inspect mode). */
  interactive?: boolean;
  hoverId?: string | null;
  onHover?: (id: string | null) => void;
  className?: string;
  /** Spoken narration (TTS on): caption words light up exactly when the voice says them. */
  narration?: SceneNarration | null;
}

function SceneDefs({ theme }: { theme: Theme }) {
  return (
    <defs>
      <filter id="fx-shadow" x="-25%" y="-25%" width="150%" height="160%" colorInterpolationFilters="sRGB">
        <feDropShadow
          dx="0"
          dy="7"
          stdDeviation="8"
          floodColor={theme.colors.shadow}
          floodOpacity={theme.effects.shadowOpacity}
        />
      </filter>
      <filter id="fx-glow" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation="14" />
      </filter>
      <filter id="fx-soft" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation="4" />
      </filter>
    </defs>
  );
}

function ObjectView({
  o,
  theme,
  t,
  seed,
  prep,
  interactive,
  hovered,
  onHover,
}: {
  o: FrameObject;
  theme: Theme;
  t: number;
  seed: number;
  prep: unknown;
  interactive: boolean;
  hovered: boolean;
  onHover?: (id: string | null) => void;
}) {
  const render = RENDERERS[o.type];
  if (!render) return null;
  const body: ReactNode = render({ o, theme, t, seed, prep });
  const inner = (
    <g transform={matrixAttr(o.matrix)} opacity={o.alpha < 1 ? fmt(o.alpha, 3) : undefined}>
      {body}
    </g>
  );
  const pointer = interactive
    ? {
        pointerEvents: "bounding-box" as const,
        onPointerEnter: () => onHover?.(o.id),
        onPointerLeave: () => onHover?.(null),
        style: { cursor: "help" },
      }
    : {};
  return (
    <g data-id={o.id} {...pointer}>
      <EmphasisBehind o={o} />
      {o.shadow && theme.effects.shadows ? <g filter="url(#fx-shadow)">{inner}</g> : inner}
      <EmphasisFront o={o} />
      {hovered && (
        <rect
          x={fmt(o.bbox.x - 10)}
          y={fmt(o.bbox.y - 10)}
          width={fmt(o.bbox.w + 20)}
          height={fmt(o.bbox.h + 20)}
          rx={12}
          fill={withAlpha(theme.colors.accent, 0.06)}
          stroke={theme.colors.accent}
          strokeWidth={2.5}
          strokeDasharray="8 6"
          pointerEvents="none"
        />
      )}
    </g>
  );
}

/** Second pass of objects that draw something in front of their contents (e.g. water over a submerged body). */
function ObjectFront({
  o,
  theme,
  t,
  seed,
  prep,
}: {
  o: FrameObject;
  theme: Theme;
  t: number;
  seed: number;
  prep: unknown;
}) {
  const render = FRONT_RENDERERS[o.type];
  if (!render) return null;
  return (
    <g transform={matrixAttr(o.matrix)} opacity={o.alpha < 1 ? fmt(o.alpha, 3) : undefined} pointerEvents="none">
      {render({ o, theme, t, seed, prep })}
    </g>
  );
}

function Overlay({ overlay }: { overlay: NonNullable<FrameState["overlay"]> }) {
  const { style, color, amount } = overlay;
  if (style === "wipe" || style === "wipe_in") {
    const w = style === "wipe_in" ? (1 - amount) * CANVAS.width : amount * CANVAS.width;
    const x = style === "wipe_in" ? CANVAS.width - w : 0;
    return <rect x={fmt(x)} y={0} width={fmt(w)} height={CANVAS.height} fill={color} />;
  }
  return (
    <rect
      width={CANVAS.width}
      height={CANVAS.height}
      fill={style === "flash" ? "#ffffff" : color}
      opacity={fmt(amount, 3)}
    />
  );
}

/** The caption's word timings and the time into the current beat, when the beat is spoken. */
function spokenWords(
  compiled: CompiledScene,
  frame: FrameState,
  narration: SceneNarration | null | undefined,
): { words?: [number, number][]; elapsed?: number } {
  const id = frame.caption?.beatId;
  const speech = id ? narration?.beats.find((b) => b.id === id) : undefined;
  const beat = id ? compiled.beats.find((b) => b.id === id) : undefined;
  if (!speech || !beat) return {};
  return { words: speech.words, elapsed: frame.t - beat.start };
}

export function SceneView({
  compiled,
  frame,
  width = "100%",
  height,
  captions,
  interactive = false,
  hoverId,
  onHover,
  className,
  narration,
}: SceneViewProps) {
  const theme = getTheme(compiled.themeId);
  const prepById = useMemo(() => new Map(compiled.objects.map((o) => [o.id, o.prep])), [compiled]);
  const world = frame.objects.filter((o) => o.layer !== "overlay");
  const behind = world.filter((o) => o.layer !== "foreground");
  const ahead = world.filter((o) => o.layer === "foreground");
  const overlay = frame.objects.filter((o) => o.layer === "overlay");
  const showCaptions = (captions ?? compiled.captions) && frame.caption;
  const front = (o: FrameObject) =>
    FRONT_RENDERERS[o.type] ? (
      <ObjectFront
        key={`${o.id}-front`}
        o={o}
        theme={theme}
        t={frame.t}
        seed={compiled.seed}
        prep={prepById.get(o.id)}
      />
    ) : null;
  const view = (o: FrameObject) => (
    <ObjectView
      key={o.id}
      o={o}
      theme={theme}
      t={frame.t}
      seed={compiled.seed}
      prep={prepById.get(o.id)}
      interactive={interactive}
      hovered={hoverId === o.id}
      onHover={onHover}
    />
  );
  return (
    <svg
      viewBox={`0 0 ${CANVAS.width} ${CANVAS.height}`}
      width={width}
      height={height}
      className={className}
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={compiled.title}
      style={{ display: "block", userSelect: "none" }}
    >
      <SceneDefs theme={theme} />
      <Background style={frame.background.style} color={frame.background.color} theme={theme} />
      <g transform={matrixAttr(cameraMatrix(frame.camera))}>
        {behind.map(view)}
        {behind.map(front)}
        {ahead.map(view)}
        {ahead.map(front)}
      </g>
      {overlay.map(view)}
      {overlay.map(front)}
      {showCaptions && frame.caption && (
        <CaptionBar
          text={frame.caption.text}
          progress={frame.caption.progress}
          theme={theme}
          {...spokenWords(compiled, frame, narration)}
        />
      )}
      {frame.overlay && <Overlay overlay={frame.overlay} />}
    </svg>
  );
}
