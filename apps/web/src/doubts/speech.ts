/**
 * Speaking a doubt. The browser's own speech recognition when it has one (Chrome, Edge, Safari): free,
 * and the words appear while they are said. Otherwise the doubt is recorded and the server turns it
 * into text. Either way the learner sees the text and can fix it before sending.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { doubtsApi } from "./api";

interface RecognitionResult {
  isFinal: boolean;
  0: { transcript: string };
}

interface RecognitionEvent {
  resultIndex: number;
  results: ArrayLike<RecognitionResult>;
}

interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export type SpeechState = "idle" | "listening" | "transcribing";

export interface SpeechInput {
  supported: boolean;
  state: SpeechState;
  error: string | null;
  start: () => void;
  stop: () => void;
}

/**
 * `onText(text, final)` receives what was said so far (the whole utterance, interim while it is
 * being said). `lang`: a BCP 47 tag (en-IN for Hinglish lessons).
 */
export function useSpeechInput(
  lang: string,
  language: string,
  onText: (text: string, final: boolean) => void,
): SpeechInput {
  const [state, setState] = useState<SpeechState>("idle");
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<Recognition | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;
  const Ctor = recognitionCtor();
  const canRecord = typeof window !== "undefined" && "MediaRecorder" in window && !!navigator.mediaDevices;

  const stop = useCallback(() => {
    rec.current?.stop();
    if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
  }, []);

  useEffect(
    () => () => {
      rec.current?.abort();
      if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
    },
    [],
  );

  const start = useCallback(() => {
    setError(null);
    if (Ctor) {
      const r = new Ctor();
      r.lang = lang;
      r.interimResults = true;
      r.continuous = false;
      let finalText = "";
      r.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i];
          if (!res) continue;
          if (res.isFinal) finalText += res[0].transcript;
          else interim += res[0].transcript;
        }
        onTextRef.current((finalText + interim).trim(), interim === "");
      };
      r.onerror = (e) => {
        if (e.error === "not-allowed" || e.error === "service-not-allowed") setError("Microphone access was blocked.");
        else if (e.error === "no-speech") setError("I didn't hear anything. Try again?");
        else if (e.error !== "aborted") setError("Speech input stopped. You can type instead.");
      };
      r.onend = () => {
        rec.current = null;
        setState("idle");
      };
      rec.current = r;
      try {
        r.start();
        setState("listening");
      } catch {
        setError("Speech input could not start. You can type instead.");
      }
      return;
    }
    if (!canRecord) {
      setError("This browser cannot take speech. Please type your doubt.");
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
        const chunks: Blob[] = [];
        const mr = new MediaRecorder(stream);
        recorder.current = mr;
        mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
        mr.onstop = () => {
          for (const t of stream.getTracks()) t.stop();
          recorder.current = null;
          const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" });
          if (!blob.size) {
            setState("idle");
            return;
          }
          setState("transcribing");
          doubtsApi
            .transcribe(blob, language)
            .then((text) => onTextRef.current(text, true))
            .catch((err: unknown) =>
              setError(err instanceof Error ? err.message : "Could not understand the recording."),
            )
            .finally(() => setState("idle"));
        };
        mr.start();
        setState("listening");
      })
      .catch(() => setError("Microphone access was blocked."));
  }, [Ctor, canRecord, lang, language]);

  return { supported: !!Ctor || canRecord, state, error, start, stop };
}
