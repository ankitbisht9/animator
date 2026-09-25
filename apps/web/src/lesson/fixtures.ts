/**
 * Hand-authored lessons from /fixtures/scenes/<lesson>/ (bundled at build time).
 * Each folder has lesson.json ({ title, description, audience }) and NN-*.json scenes.
 */
interface LessonMeta {
  title: string;
  description?: string;
  audience?: string;
}

export interface FixtureLesson {
  slug: string;
  meta: LessonMeta;
  scenes: unknown[];
}

const files = import.meta.glob("../../../../fixtures/scenes/*/*.json", { eager: true, import: "default" }) as Record<
  string,
  unknown
>;

export const FIXTURE_LESSONS: FixtureLesson[] = (() => {
  const bySlug = new Map<string, { meta?: LessonMeta; scenes: [string, unknown][] }>();
  for (const [path, data] of Object.entries(files)) {
    const m = /fixtures\/scenes\/([^/]+)\/([^/]+)\.json$/.exec(path);
    if (!m?.[1] || !m[2]) continue;
    const entry = bySlug.get(m[1]) ?? { scenes: [] };
    if (m[2] === "lesson") entry.meta = data as LessonMeta;
    else entry.scenes.push([m[2], data]);
    bySlug.set(m[1], entry);
  }
  return [...bySlug.entries()]
    .map(([slug, e]) => ({
      slug,
      meta: e.meta ?? { title: slug },
      scenes: e.scenes.sort(([a], [b]) => a.localeCompare(b)).map(([, d]) => d),
    }))
    .sort((a, b) => a.slug.localeCompare(b.slug));
})();

export const fixtureLesson = (slug: string): FixtureLesson | undefined => FIXTURE_LESSONS.find((l) => l.slug === slug);
