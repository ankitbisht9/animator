/** The brand mark, subjects (icon and tint), lesson covers and status badges. */
import {
  Atom,
  BookText,
  ChartColumn,
  Cpu,
  Dna,
  Earth,
  FlaskConical,
  Landmark,
  Lightbulb,
  type LucideIcon,
  Sigma,
} from "lucide-react";
import type { Lesson } from "../api";
import { Badge } from "./core";

export const BRAND = "Animator";

export function Logo({
  size = 30,
  withName = true,
  light = false,
}: {
  size?: number;
  withName?: boolean;
  light?: boolean;
}) {
  return (
    <span className={`brand-logo ${light ? "light" : ""}`}>
      <svg viewBox="0 0 40 40" width={size} height={size} aria-hidden="true">
        <rect x="1" y="1" width="38" height="38" rx="11" fill="#d90429" />
        <path
          d="M8 29 C 14 13, 22 11, 32 11"
          stroke="#fff"
          strokeOpacity="0.35"
          strokeWidth="2"
          fill="none"
          strokeLinecap="round"
        />
        <circle cx="13" cy="25" r="5" fill="#fff" />
        <path d="M19.5 15.5 L29 21 L19.5 26.5 Z" fill="#fff" />
      </svg>
      {withName && <span className="brand-name">{BRAND}</span>}
    </span>
  );
}

interface SubjectMeta {
  label: string;
  icon: LucideIcon;
  tint: string;
  ink: string;
}

export const SUBJECTS: Record<string, SubjectMeta> = {
  physics: { label: "Physics", icon: Atom, tint: "#fdebed", ink: "#c1121f" },
  chemistry: { label: "Chemistry", icon: FlaskConical, tint: "#fff0e5", ink: "#c2410c" },
  biology: { label: "Biology", icon: Dna, tint: "#e7f5ec", ink: "#15803d" },
  mathematics: { label: "Mathematics", icon: Sigma, tint: "#eceffb", ink: "#3949ab" },
  "computer-science": { label: "Computer Science", icon: Cpu, tint: "#e5f3f7", ink: "#0e7490" },
  "earth-space": { label: "Earth & Space", icon: Earth, tint: "#e8f0fc", ink: "#1d4ed8" },
  economics: { label: "Economics", icon: ChartColumn, tint: "#fbf4df", ink: "#a16207" },
  history: { label: "History & Society", icon: Landmark, tint: "#f4ece4", ink: "#8d5524" },
  "language-arts": { label: "Language & Arts", icon: BookText, tint: "#f6eaf5", ink: "#a21caf" },
  general: { label: "General", icon: Lightbulb, tint: "#f0f1f5", ink: "#4b5563" },
};

export function subject(key: string): SubjectMeta {
  return SUBJECTS[key] ?? (SUBJECTS.general as SubjectMeta);
}

/** A cover drawn from the subject (no image to load): tint, pattern and the subject's icon. */
export function LessonCover({
  lesson,
  compact = false,
}: {
  lesson: Pick<Lesson, "subject" | "id" | "status">;
  compact?: boolean;
}) {
  const s = subject(lesson.subject);
  const seed = [...lesson.id].reduce((a, ch) => a + ch.charCodeAt(0), 0);
  const variant = seed % 3;
  return (
    <div
      className={`lesson-cover v${variant} ${compact ? "compact" : ""}`}
      style={{ background: s.tint, color: s.ink }}
      aria-hidden="true"
    >
      <svg className="lesson-cover-art" viewBox="0 0 200 112" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        {variant === 0 && (
          <>
            <circle cx="160" cy="20" r="46" fill="currentColor" opacity="0.08" />
            <circle cx="30" cy="100" r="30" fill="currentColor" opacity="0.06" />
            <path
              d="M0 80 Q 60 40 120 70 T 200 50"
              stroke="currentColor"
              strokeOpacity="0.18"
              strokeWidth="2"
              fill="none"
            />
          </>
        )}
        {variant === 1 &&
          Array.from({ length: 8 }, (_, i) => (
            <line
              // biome-ignore lint/suspicious/noArrayIndexKey: static decoration
              key={i}
              x1={i * 28}
              y1="112"
              x2={i * 28 + 60}
              y2="0"
              stroke="currentColor"
              strokeOpacity="0.07"
              strokeWidth="10"
            />
          ))}
        {variant === 2 && (
          <>
            {Array.from({ length: 6 }, (_, r) =>
              Array.from({ length: 11 }, (_, c) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: static decoration
                <circle key={`${r}-${c}`} cx={c * 20 + 6} cy={r * 20 + 6} r="1.8" fill="currentColor" opacity="0.18" />
              )),
            )}
            <rect
              x="120"
              y="30"
              width="56"
              height="56"
              rx="14"
              fill="currentColor"
              opacity="0.07"
              transform="rotate(14 148 58)"
            />
          </>
        )}
      </svg>
      <s.icon className="lesson-cover-icon" size={compact ? 28 : 40} strokeWidth={1.6} />
    </div>
  );
}

const STATUS: Record<string, { label: string; tone: "neutral" | "brand" | "success" | "warning" | "danger" | "info" }> =
  {
    queued: { label: "Queued", tone: "info" },
    planning: { label: "Planning", tone: "info" },
    generating: { label: "Creating", tone: "info" },
    complete: { label: "Ready", tone: "success" },
    failed: { label: "Stopped", tone: "danger" },
    cancelled: { label: "Cancelled", tone: "neutral" },
    paused: { label: "Paused", tone: "warning" },
  };

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "neutral" as const };
  const live = status === "queued" || status === "planning" || status === "generating";
  return (
    <Badge tone={s.tone} className={live ? "live" : ""}>
      {live && <span className="live-dot" />}
      {s.label}
    </Badge>
  );
}

export const AUDIENCES = [
  { value: "primary-school child", label: "Primary school", hint: "Ages 6–10" },
  { value: "middle-school student", label: "Middle school", hint: "Ages 11–13" },
  { value: "high-school student", label: "High school", hint: "Ages 14–18" },
  { value: "university student", label: "University", hint: "Undergraduate" },
  { value: "curious adult", label: "Curious adult", hint: "No background needed" },
];

export function audienceLabel(a: string): string {
  return AUDIENCES.find((x) => x.value === a)?.label ?? a.replace(/\b\w/g, (c) => c.toUpperCase());
}
