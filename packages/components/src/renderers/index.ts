import type { Renderer } from "../types";
import {
  ArrowR,
  BracketR,
  CalloutR,
  CaptionBlockR,
  DimensionR,
  LabelR,
  MarkerR,
  NoteR,
  VectorArrowR,
} from "./annotation";
import { FigureR } from "./figure";
import { AxesR, EquationR, GraphR, GridR, ReadoutR } from "./math";
import {
  BalanceR,
  BallR,
  BlockR,
  ContainerFrontR,
  ContainerR,
  FlowParticlesR,
  ManometerR,
  ParticlesR,
  PendulumR,
  PipeR,
  PressureGaugeR,
  PulleyR,
  RopeR,
  SpringR,
  SpringScaleR,
} from "./physics";
import { CircleR, EllipseR, GroupR, ImageR, LineR, PathR, PolygonR, RectangleR, TextR } from "./primitives";
import { BadgeR, BurstR, CardR, IconR, SpeechBubbleR, TitleCardR } from "./ui";

/** Renderer per catalog type. A test asserts this covers every registered component spec. */
export const RENDERERS: Record<string, Renderer> = {
  // primitives
  circle: CircleR,
  ellipse: EllipseR,
  rectangle: RectangleR,
  polygon: PolygonR,
  line: LineR,
  path: PathR,
  text: TextR,
  group: GroupR,
  image: ImageR,
  // saved drawings, written by the pipeline (ADR-055)
  figure: FigureR,
  // annotation
  arrow: ArrowR,
  vector_arrow: VectorArrowR,
  dimension: DimensionR,
  bracket: BracketR,
  label: LabelR,
  callout: CalloutR,
  note: NoteR,
  marker: MarkerR,
  caption: CaptionBlockR,
  // math
  equation: EquationR,
  readout: ReadoutR,
  graph: GraphR,
  axes: AxesR,
  grid: GridR,
  // physics
  container: ContainerR,
  block: BlockR,
  ball: BallR,
  rope: RopeR,
  spring: SpringR,
  pulley: PulleyR,
  pendulum: PendulumR,
  balance: BalanceR,
  spring_scale: SpringScaleR,
  pipe: PipeR,
  flow_particles: FlowParticlesR,
  pressure_gauge: PressureGaugeR,
  manometer: ManometerR,
  particles: ParticlesR,
  // engagement
  title_card: TitleCardR,
  card: CardR,
  icon: IconR,
  badge: BadgeR,
  speech_bubble: SpeechBubbleR,
  burst: BurstR,
};

/** Optional second pass drawn in front of the other objects of the same layer (e.g. water in front of a submerged body). */
export const FRONT_RENDERERS: Record<string, Renderer> = {
  container: ContainerFrontR,
};
