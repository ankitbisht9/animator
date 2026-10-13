/** The doubt screen's small parts: the raise-hand button, the tutor's face, and chat bubbles. */
import { Fragment, type ReactNode } from "react";

export function HandRaise({ onRaise, raised }: { onRaise: () => void; raised: boolean }) {
  return (
    <button
      type="button"
      className={`hand-raise ${raised ? "raised" : ""}`}
      onClick={onRaise}
      aria-label="Raise your hand to ask a doubt (D)"
      title="Got a doubt? Raise your hand (D)"
    >
      <span className="hand-raise-ring" aria-hidden="true" />
      <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" className="hand-raise-icon">
        <path
          d="M18 11V6.5a1.5 1.5 0 0 0-3 0V11M15 10.5V4.5a1.5 1.5 0 0 0-3 0v6M12 10.5V5.5a1.5 1.5 0 0 0-3 0V12M9 12V8.5a1.5 1.5 0 0 0-3 0V14a7 7 0 0 0 7 7h1a6 6 0 0 0 6-6v-3.5a1.5 1.5 0 0 0-3 0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <span className="hand-raise-label">Doubt?</span>
    </button>
  );
}

export function TutorFace({ speaking, thinking }: { speaking: boolean; thinking: boolean }) {
  return (
    <div className={`tutor-face ${speaking ? "speaking" : ""} ${thinking ? "thinking" : ""}`} aria-hidden="true">
      <span className="tutor-ring r1" />
      <span className="tutor-ring r2" />
      <svg viewBox="0 0 64 64" width="56" height="56" aria-hidden="true">
        <defs>
          <linearGradient id="tutor-g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" style={{ stopColor: "var(--brand-solid-400)" }} />
            <stop offset="1" style={{ stopColor: "var(--brand-solid-600)" }} />
          </linearGradient>
        </defs>
        <circle cx="32" cy="32" r="30" fill="url(#tutor-g)" />
        <circle cx="23" cy="27" r="3.6" fill="#fff" className="tutor-eye" />
        <circle cx="41" cy="27" r="3.6" fill="#fff" className="tutor-eye" />
        <path
          d="M21 39q11 9 22 0"
          stroke="#fff"
          strokeWidth="3.4"
          fill="none"
          strokeLinecap="round"
          className="tutor-mouth"
        />
        <path
          d="M14 20q4-7 11-8M50 20q-4-7-11-8"
          stroke="#ffd166"
          strokeWidth="2.4"
          fill="none"
          strokeLinecap="round"
        />
      </svg>
    </div>
  );
}

/** **term** as bold; the rest as text. */
export function Rich({ text }: { text: string }) {
  const parts = text.split("**");
  return (
    <>
      {parts.map((p, i) =>
        // biome-ignore lint/suspicious/noArrayIndexKey: the parts of one sentence never reorder
        i % 2 === 1 ? <b key={i}>{p}</b> : <Fragment key={i}>{p}</Fragment>,
      )}
    </>
  );
}

export function TutorBubble({
  children,
  tone = "plain",
}: {
  children: ReactNode;
  tone?: "plain" | "thinking" | "error" | "answer" | "active" | "followup";
}) {
  return (
    <div className={`doubt-msg tutor ${tone}`}>
      <div className="doubt-bubble">{children}</div>
    </div>
  );
}

export function StudentBubble({ text }: { text: string }) {
  return (
    <div className="doubt-msg student">
      <div className="doubt-bubble">{text}</div>
    </div>
  );
}

export function ThinkingDots() {
  return (
    <span className="doubt-dots" role="img" aria-label="The tutor is thinking">
      <i />
      <i />
      <i />
    </span>
  );
}
