/** Notes pinned to moments of a lesson: write one at the current time, jump back to it, edit, delete. */
import { Check, NotebookPen, Pencil, Trash, X } from "lucide-react";
import { type FormEvent, forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { type Note, v1 } from "../api";
import { clock } from "../format";
import { invalidate, useResource } from "../hooks";
import { Button, EmptyState, IconButton } from "../ui/core";
import { useToast } from "../ui/overlay";

export interface NotesHandle {
  focus: () => void;
}

export const NotesPanel = forwardRef<NotesHandle, { lessonId: string; now: () => number; seek: (t: number) => void }>(
  function NotesPanel({ lessonId, now, seek }, ref) {
    const toast = useToast();
    const res = useResource(`notes:${lessonId}`, () => v1.notes(lessonId));
    const [text, setText] = useState("");
    const [at, setAt] = useState(0);
    const [busy, setBusy] = useState(false);
    const [editing, setEditing] = useState<string | null>(null);
    const [draft, setDraft] = useState("");
    const input = useRef<HTMLTextAreaElement>(null);

    useImperativeHandle(ref, () => ({
      focus: () => {
        setAt(now());
        window.setTimeout(() => input.current?.focus(), 50);
      },
    }));
    // The time shown follows the lesson until the learner starts typing.
    useEffect(() => {
      if (text) return;
      const h = window.setInterval(() => setAt(now()), 500);
      return () => window.clearInterval(h);
    }, [text, now]);

    const add = async (e?: FormEvent) => {
      e?.preventDefault();
      if (!text.trim()) return;
      setBusy(true);
      try {
        const n = await v1.addNote(lessonId, at, text.trim());
        res.mutate((all) => [...(all ?? []), n].sort((a, b) => a.t_s - b.t_s));
        setText("");
        invalidate("stats:");
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    };

    const save = async (n: Note) => {
      try {
        const u = await v1.editNote(n.id, draft.trim());
        res.mutate((all) => all?.map((x) => (x.id === n.id ? u : x)));
        setEditing(null);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    };

    const remove = async (n: Note) => {
      const before = res.data;
      res.mutate((all) => all?.filter((x) => x.id !== n.id));
      try {
        await v1.deleteNote(n.id);
      } catch (err) {
        res.mutate(() => before);
        toast.error(err instanceof Error ? err.message : String(err));
      }
    };

    const notes = res.data ?? [];
    return (
      <div className="notes">
        <form className="notes-add" onSubmit={add}>
          <span className="notes-at" title="The moment this note is pinned to">
            {clock(at)}
          </span>
          <textarea
            ref={input}
            value={text}
            rows={2}
            maxLength={2000}
            placeholder="Write a note at this moment… (Enter to save)"
            aria-label="New note"
            onChange={(e) => {
              if (!text) setAt(now());
              setText(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void add();
              }
            }}
          />
          <Button type="submit" variant="primary" size="sm" loading={busy} disabled={!text.trim()}>
            Save
          </Button>
        </form>
        {notes.length === 0 && !res.loading ? (
          <EmptyState
            icon={NotebookPen}
            title="No notes yet"
            text="Pause anywhere and write what you want to remember. Press N to start a note."
          />
        ) : (
          <ul className="notes-list">
            {notes.map((n) => (
              <li key={n.id}>
                <button type="button" className="notes-time" onClick={() => seek(n.t_s)} title="Jump to this moment">
                  {clock(n.t_s)}
                </button>
                {editing === n.id ? (
                  <div className="notes-edit">
                    <textarea
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      rows={2}
                      aria-label="Edit note"
                    />
                    <IconButton icon={Check} label="Save" onClick={() => void save(n)} disabled={!draft.trim()} />
                    <IconButton icon={X} label="Cancel" onClick={() => setEditing(null)} />
                  </div>
                ) : (
                  <p>{n.text}</p>
                )}
                {editing !== n.id && (
                  <span className="notes-actions">
                    <IconButton
                      icon={Pencil}
                      label="Edit"
                      size={15}
                      onClick={() => {
                        setEditing(n.id);
                        setDraft(n.text);
                      }}
                    />
                    <IconButton icon={Trash} label="Delete" size={15} onClick={() => void remove(n)} />
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  },
);
