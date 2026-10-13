/** Who can watch a lesson, its link, and (for its owner) whether it is in Explore and why. */
import { Check, Compass, Copy, Globe, Link2, Lock, Share2, Star } from "lucide-react";
import { useState } from "react";
import { type Lesson, type Visibility, v1 } from "../api";
import { useAuth } from "../auth";
import { Button, Checkbox } from "../ui/core";
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
  const [optOut, setOptOut] = useState(lesson.explore?.opt_out ?? false);
  // Featured lessons are public ones (ADR-057): sharing it otherwise ends the featuring.
  const [featured, setFeatured] = useState(lesson.featured);
  const url = `${window.location.origin}/lessons/${lesson.id}`;
  const canPublish = can("lesson:publish") && lesson.status === "complete";

  const allowRatings = async (allow: boolean) => {
    setBusy(true);
    try {
      const updated = await v1.patchLesson(lesson.id, { explore_opt_out: !allow });
      setOptOut(updated.explore?.opt_out ?? !allow);
      onChanged?.(updated);
      toast.success(
        allow
          ? "It can join Explore when learners rate it highly."
          : "It will not be listed in Explore because of its ratings.",
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const choose = async (v: Visibility) => {
    if (v === visibility || !lesson.can_edit) return;
    setBusy(true);
    try {
      const updated = await v1.patchLesson(lesson.id, { visibility: v });
      const lost = featured && !updated.featured ? " It is no longer featured." : "";
      setVisibility(updated.visibility);
      setFeatured(updated.featured);
      onChanged?.(updated);
      toast.success(
        v === "private"
          ? `Only you can watch it now.${lost}`
          : v === "unlisted"
            ? `Anyone with the link can watch it.${lost}`
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
      {featured && visibility === "public" && (
        <p className="share-featured">
          <Star size={14} /> Featured on Explore. Sharing it with a link only, or making it private, ends the featuring.
        </p>
      )}
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
      {lesson.mine && lesson.explore && visibility !== "private" && (
        <ExploreNote
          lesson={lesson}
          visibility={visibility}
          optOut={optOut}
          busy={busy}
          onAllow={(v) => void allowRatings(v)}
        />
      )}
    </Modal>
  );
}

/** For the owner: is it in Explore, why (or why not), and whether its ratings may list it. */
function ExploreNote({
  lesson,
  visibility,
  optOut,
  busy,
  onAllow,
}: {
  lesson: Lesson;
  visibility: Visibility;
  optOut: boolean;
  busy: boolean;
  onAllow: (allow: boolean) => void;
}) {
  const ex = lesson.explore;
  if (!ex) return null;
  const rule = ex.rule;
  const ruleText = rule
    ? `at least ${rule.min_raters} people rate it ${rule.min_average.toFixed(1)} or more on average`
    : "learners rate it highly";
  const why = ex.excluded
    ? "An administrator keeps this lesson out of Explore. Anyone with the link can still watch it."
    : lesson.in_explore
      ? ex.source === "owner"
        ? "You published it: everyone can find it under its subject."
        : ex.source === "auto"
          ? "Learners rated it highly, so it is listed under its subject."
          : "An administrator added it to Explore, under its subject."
      : visibility === "unlisted" && rule?.auto && !optOut
        ? `It joins Explore by itself once ${ruleText}.`
        : visibility === "unlisted" && optOut
          ? "You chose not to have it listed because of its ratings."
          : "Only people with the link can find it.";
  return (
    <div className={`share-explore ${lesson.in_explore ? "on" : ""}`}>
      <Compass size={18} />
      <div>
        <b>{lesson.in_explore ? "In Explore" : "Not in Explore"}</b>
        <small>{why}</small>
        {visibility === "unlisted" && rule?.auto && !ex.excluded && (
          <Checkbox checked={!optOut} onChange={(v) => !busy && onAllow(v)}>
            Let learners' ratings bring it into Explore
          </Checkbox>
        )}
      </div>
    </div>
  );
}
