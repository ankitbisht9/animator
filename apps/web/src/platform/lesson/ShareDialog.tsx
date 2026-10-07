/** Who can watch a lesson, and its link. */
import { Check, Copy, Globe, Link2, Lock, Share2 } from "lucide-react";
import { useState } from "react";
import { type Lesson, type Visibility, v1 } from "../api";
import { useAuth } from "../auth";
import { Button } from "../ui/core";
import { Modal, useToast } from "../ui/overlay";

export function ShareDialog({
  lesson,
  onClose,
  onChanged,
}: {
  lesson: Lesson;
  onClose: () => void;
  onChanged?: (l: Lesson) => void;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const [visibility, setVisibility] = useState<Visibility>(lesson.visibility);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const url = `${window.location.origin}/lessons/${lesson.id}`;
  const canPublish = can("lesson:publish") && lesson.status === "complete";

  const choose = async (v: Visibility) => {
    if (v === visibility || !lesson.can_edit) return;
    setBusy(true);
    try {
      const updated = await v1.patchLesson(lesson.id, { visibility: v });
      setVisibility(updated.visibility);
      onChanged?.(updated);
      toast.success(
        v === "private"
          ? "Only you can watch it now."
          : v === "unlisted"
            ? "Anyone with the link can watch it."
            : "Published to Explore.",
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      toast.error("Copying is blocked here. Select the link and copy it.");
    }
  };

  const options = [
    { v: "private" as const, icon: Lock, label: "Only me", hint: "Private to your account" },
    {
      v: "unlisted" as const,
      icon: Link2,
      label: "Anyone with the link",
      hint: "Not listed anywhere; the link opens it",
    },
    {
      v: "public" as const,
      icon: Globe,
      label: "Everyone",
      hint: can("lesson:publish")
        ? lesson.status === "complete"
          ? "Listed in Explore"
          : "Once the lesson is ready"
        : "Educators can publish to Explore",
      disabled: !canPublish,
    },
  ];

  return (
    <Modal open onClose={onClose} title="Share lesson" description={lesson.title}>
      <fieldset className="share-options">
        <legend className="sr-only">Who can watch</legend>
        {options.map((o) => (
          <button
            key={o.v}
            type="button"
            aria-pressed={visibility === o.v}
            disabled={busy || ("disabled" in o && o.disabled) || !lesson.can_edit}
            className={`share-option ${visibility === o.v ? "on" : ""}`}
            onClick={() => void choose(o.v)}
          >
            <o.icon size={18} />
            <span>
              <b>{o.label}</b>
              <small>{o.hint}</small>
            </span>
            {visibility === o.v && <Check size={18} className="share-check" />}
          </button>
        ))}
      </fieldset>
      {visibility !== "private" && (
        <div className="share-link">
          <input readOnly value={url} aria-label="Lesson link" onFocus={(e) => e.currentTarget.select()} />
          <Button variant="primary" icon={copied ? Check : Copy} onClick={() => void copy()}>
            {copied ? "Copied" : "Copy link"}
          </Button>
          {"share" in navigator && (
            <Button
              variant="secondary"
              icon={Share2}
              onClick={() => void navigator.share({ title: lesson.title, url }).catch(() => undefined)}
              aria-label="Share with an app"
            />
          )}
        </div>
      )}
    </Modal>
  );
}
