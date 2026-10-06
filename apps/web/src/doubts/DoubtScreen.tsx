/**
 * The doubt screen (ADR-050): the lesson pauses behind it and the tutor talks with the learner.
 *
 * The tutor greets (a fixed line, spoken), the learner types or says the doubt, the tutor says a line
 * while it prepares the answer, then the answer plays on its own stage (the lesson's picture or a
 * board) with its voice and captions. The learner can ask again in the same conversation (each answer
 * simpler than the last when it did not land) or go back to the lesson, which resumes where it was.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { audioUrl, type DoubtAnswer, DoubtApiError, doubtsApi, type Phrase, type Phrases } from "./api";
import { DoubtPlayer } from "./DoubtPlayer";
import { Rich, StudentBubble, ThinkingDots, TutorBubble, TutorFace } from "./parts";
import { useSpeechInput } from "./speech";

interface Turn {
  key: number;
  question: string;
  answer?: DoubtAnswer;
  error?: string;
}

interface Props {
  lessonId: string;
  language: string;
  audience: string;
  durationMin: number;
  sceneIndex: number;
  t: number;
  phrases: Phrases;
  voice: boolean;
  onClose: () => void;
}

const KIND_LABEL: Record<string, string> = {
  related: "A related topic",
  unrelated: "Outside this lesson",
  unclear: "Let's make it clearer",
};

/** The tutor's lines before their audio has arrived (the first time a language is used): shown, not spoken. */
const FALLBACK: Record<"english" | "hinglish", Required<Record<keyof Phrases, string>>> = {
  english: {
    greeting: "Oh, a hand up! What's on your mind?",
    thinking: "Good question. Let me show you.",
    followup: "Does that help? Ask me more, or we can carry on.",
    resume: "Great, let's get back to the lesson!",
  },
  hinglish: {
    greeting: "Arre, haath upar! Batao, kya doubt hai?",
    thinking: "Achha sawaal hai! Chalo, dekhte hain.",
    followup: "Ab clear hua? Nahi toh phir se poochho.",
    resume: "Badhiya! Chalo, lesson pe wapas chalte hain.",
  },
};

function asks(language: string) {
  const hinglish = language.trim().toLowerCase() === "hinglish";
  return {
    simpler: hinglish
      ? "Abhi bhi samajh nahi aaya. Aur simple karke samjhao?"
      : "I still don't get it. Can you explain it more simply?",
    example: hinglish ? "Ek simple example de sakte ho?" : "Can you give me a simple example?",
    placeholder: hinglish ? "Apna doubt likho… ya mic dabake bolo" : "Type your doubt… or tap the mic and say it",
    lang: hinglish ? "en-IN" : "en-US",
  };
}

export function DoubtScreen(props: Props) {
  const { lessonId, language, sceneIndex, t, phrases, onClose } = props;
  const words = useMemo(() => asks(language), [language]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [current, setCurrent] = useState<DoubtAnswer | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [finished, setFinished] = useState(false);
  const [lines, setLines] = useState<{ greeting?: Phrase; thinking?: Phrase; followup?: Phrase }>({});
  const [phraseSpeaking, setPhraseSpeaking] = useState(false);
  const [answerSpeaking, setAnswerSpeaking] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [suggested, setSuggested] = useState<Record<string, string>>({});
  const audio = useRef<HTMLAudioElement | null>(null);
  const last = useRef<Record<string, string>>({});
  const input = useRef<HTMLTextAreaElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  const keyRef = useRef(0);
  const base = useRef("");

  // A fixed line, spoken: a random one, never the same twice in a row.
  const say = useCallback(
    (kind: keyof Phrases, then?: () => void): Phrase | undefined => {
      const known = phrases[kind] ?? [];
      const fallback = FALLBACK[language.trim().toLowerCase() === "hinglish" ? "hinglish" : "english"][kind];
      const pool: Phrase[] = known.length ? known : [{ text: fallback, audio: null }];
      const options = pool.length > 1 ? pool.filter((p) => p.text !== last.current[kind]) : pool;
      const p = options[Math.floor(Math.random() * options.length)];
      if (!p) {
        then?.();
        return undefined;
      }
      last.current[kind] = p.text;
      audio.current?.pause();
      if (props.voice && p.audio) {
        const el = new Audio(audioUrl(p.audio));
        audio.current = el;
        setPhraseSpeaking(true);
        const done = () => {
          if (audio.current === el) setPhraseSpeaking(false);
          then?.();
        };
        el.onended = done;
        el.onerror = done;
        el.play().catch(done);
      } else {
        then?.();
      }
      return p;
    },
    [phrases, props.voice, language],
  );
  const hush = useCallback(() => {
    audio.current?.pause();
    audio.current = null;
    setPhraseSpeaking(false);
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: greet once, when the screen opens
  useEffect(() => {
    alive.current = true;
    setLines({ greeting: say("greeting") });
    input.current?.focus();
    return () => {
      alive.current = false;
      audio.current?.pause();
    };
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll down whenever the conversation grows
  useEffect(() => {
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: "smooth" });
  }, [turns, finished, busy]);

  const speech = useSpeechInput(words.lang, language, (heard) => {
    setText(`${base.current}${base.current && heard ? " " : ""}${heard}`);
  });

  const leave = useCallback(() => {
    if (leaving) return;
    setLeaving(true);
    speech.stop();
    let left = false;
    const go = () => {
      if (left) return;
      left = true;
      onClose();
    };
    say("resume", go);
    window.setTimeout(go, 3000);
  }, [leaving, onClose, say, speech]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        leave();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [leave]);

  const send = async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;
    speech.stop();
    setText("");
    base.current = "";
    setBusy(true);
    setFinished(false);
    const key = ++keyRef.current;
    setTurns((ts) => [...ts, { key, question: q }]);
    setLines((l) => ({ ...l, thinking: say("thinking") }));
    try {
      const answer = await doubtsApi.ask(lessonId, { question: q, scene_index: sceneIndex, t, session_id: session });
      if (!alive.current) return;
      hush();
      setSession(answer.session_id);
      setTurns((ts) => ts.map((x) => (x.key === key ? { ...x, answer } : x)));
      setCurrent(answer);
    } catch (err) {
      if (!alive.current) return;
      hush();
      const message = err instanceof DoubtApiError ? err.message : "Something went wrong.";
      setTurns((ts) => ts.map((x) => (x.key === key ? { ...x, error: message } : x)));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const onAnswerEnd = useCallback(() => {
    setFinished(true);
    setLines((l) => ({ ...l, followup: say("followup") }));
  }, [say]);

  const suggest = async (topic: string) => {
    setSuggested((s) => ({ ...s, [topic]: "making" }));
    try {
      const g = await api.create({
        topic,
        language,
        audience: props.audience,
        duration_min: props.durationMin,
      });
      window.open(`/g/${g.id}`, "_blank", "noopener");
      setSuggested((s) => ({ ...s, [topic]: "made" }));
    } catch {
      setSuggested((s) => ({ ...s, [topic]: "failed" }));
    }
  };

  const listening = speech.state === "listening";
  const wide = current !== null;
  return (
    <div className={`doubt-layer ${leaving ? "leaving" : ""}`} role="dialog" aria-modal="true" aria-label="Ask a doubt">
      <div className={`doubt-sheet ${wide ? "wide" : ""}`}>
        <header className="doubt-head">
          <TutorFace speaking={phraseSpeaking || answerSpeaking} thinking={busy} />
          <div className="doubt-head-text">
            <div className="doubt-kicker">
              <span className="doubt-live" aria-hidden="true" />
              Doubt time · the lesson is paused
            </div>
            <div className="doubt-headline">{current?.title ?? "What's your doubt?"}</div>
          </div>
          <button type="button" className="doubt-back" onClick={leave} aria-label="Back to the lesson">
            Back to lesson
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M8 5v14l11-7z" fill="currentColor" />
            </svg>
          </button>
        </header>

        <div className="doubt-body">
          {current && (
            <section className="doubt-visual" aria-label="The tutor's explanation">
              <DoubtPlayer
                key={`${current.session_id}-${current.n}`}
                answer={current}
                hold={listening}
                onEnd={onAnswerEnd}
                onSpeaking={setAnswerSpeaking}
              />
            </section>
          )}

          <section className="doubt-chat">
            <div className="doubt-thread" ref={thread}>
              {lines.greeting && <TutorBubble>{lines.greeting.text}</TutorBubble>}
              {turns.map((turn, i) => (
                <div key={turn.key} className="doubt-turn">
                  <StudentBubble text={turn.question} />
                  {turn.answer ? (
                    <TutorBubble tone={current === turn.answer ? "active" : "answer"}>
                      {KIND_LABEL[turn.answer.kind] && <div className="doubt-tag">{KIND_LABEL[turn.answer.kind]}</div>}
                      <div className="doubt-answer-title">{turn.answer.title}</div>
                      <div className="doubt-answer-text">
                        {turn.answer.sentences.map((s) => (
                          <span key={s}>
                            <Rich text={s} />{" "}
                          </span>
                        ))}
                      </div>
                      {turn.answer.suggestion && (
                        <div className="doubt-suggest">
                          <span>
                            Want a full lesson on <b>{turn.answer.suggestion}</b>?
                          </span>
                          <button
                            type="button"
                            className="doubt-chip strong"
                            disabled={!!suggested[turn.answer.suggestion]}
                            onClick={() => void suggest(turn.answer?.suggestion ?? "")}
                          >
                            {suggested[turn.answer.suggestion] === "made"
                              ? "Opened in a new tab ✓"
                              : suggested[turn.answer.suggestion] === "making"
                                ? "Creating…"
                                : suggested[turn.answer.suggestion] === "failed"
                                  ? "Could not create it"
                                  : "Create that lesson"}
                          </button>
                        </div>
                      )}
                      {current !== turn.answer && (
                        <button type="button" className="doubt-link" onClick={() => setCurrent(turn.answer ?? null)}>
                          ▶ Show this again
                        </button>
                      )}
                    </TutorBubble>
                  ) : turn.error ? (
                    <TutorBubble tone="error">
                      Sorry, I couldn't answer that just now. {turn.error}
                      <button
                        type="button"
                        className="doubt-link"
                        onClick={() => void send(turn.question)}
                        disabled={busy}
                      >
                        Try again
                      </button>
                    </TutorBubble>
                  ) : (
                    <TutorBubble tone="thinking">
                      {i === turns.length - 1 && lines.thinking ? <span>{lines.thinking.text} </span> : null}
                      <ThinkingDots />
                    </TutorBubble>
                  )}
                </div>
              ))}
              {finished && lines.followup && <TutorBubble tone="followup">{lines.followup.text}</TutorBubble>}
            </div>

            {current && !busy && (
              <div className="doubt-chips">
                <button type="button" className="doubt-chip" onClick={() => void send(words.simpler)}>
                  😕 Still not clear
                </button>
                <button type="button" className="doubt-chip" onClick={() => void send(words.example)}>
                  💡 Give an example
                </button>
                <button type="button" className="doubt-chip strong" onClick={leave}>
                  ✓ Got it, continue
                </button>
              </div>
            )}

            <form
              className={`doubt-input ${listening ? "listening" : ""}`}
              onSubmit={(e) => {
                e.preventDefault();
                void send(text);
              }}
            >
              {speech.supported && (
                <button
                  type="button"
                  className={`doubt-mic ${listening ? "on" : ""}`}
                  onClick={() => {
                    if (listening) speech.stop();
                    else {
                      hush();
                      base.current = text.trim();
                      speech.start();
                    }
                  }}
                  disabled={busy || speech.state === "transcribing"}
                  aria-label={listening ? "Stop listening" : "Say your doubt"}
                  title={listening ? "Stop" : "Say it"}
                >
                  <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                    <rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" />
                    <path
                      d="M6 11a6 6 0 0 0 12 0M12 17v4M8.5 21h7"
                      stroke="currentColor"
                      strokeWidth="2"
                      fill="none"
                      strokeLinecap="round"
                    />
                  </svg>
                </button>
              )}
              <textarea
                ref={input}
                value={text}
                rows={2}
                maxLength={1000}
                placeholder={
                  listening
                    ? "Listening…"
                    : speech.state === "transcribing"
                      ? "Writing down what you said…"
                      : words.placeholder
                }
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(text);
                  }
                }}
                aria-label="Your doubt"
              />
              <button type="submit" className="doubt-send" disabled={busy || !text.trim()} aria-label="Ask">
                {busy ? (
                  <ThinkingDots />
                ) : (
                  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                    <path d="M4 12l16-8-6 16-3-6z" fill="currentColor" />
                  </svg>
                )}
              </button>
            </form>
            <div className="doubt-hint">
              {speech.error ?? "Enter to ask · Shift+Enter for a new line · Esc to go back to the lesson"}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
