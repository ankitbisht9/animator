/** Actions on a lesson used from several pages. */
import { useCallback } from "react";
import { type Lesson, v1 } from "../api";
import { invalidate } from "../hooks";
import { useToast } from "../ui/overlay";

/** Every cached list that shows lessons (they refetch). */
export function refreshLessonLists(): void {
  for (const k of ["learning", "lessons:", "explore", "library", "lesson:", "stats:"]) invalidate(k);
}

export function useSaveToggle(): (l: Lesson) => void {
  const toast = useToast();
  return useCallback(
    (l: Lesson) => {
      const p = l.saved ? v1.unsave(l.id) : v1.save(l.id);
      p.then(() => {
        refreshLessonLists();
        if (!l.saved) toast.success("Saved to your library.");
      }).catch((e: unknown) => toast.error(e instanceof Error ? e.message : String(e)));
    },
    [toast],
  );
}
