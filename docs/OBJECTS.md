# Saved drawings and cost-saver lessons

A **cost-saver** lesson is made like a planned one (ADR-047), with one difference: where a scene shows
exactly a thing the drawing library has (a human heart, a plant cell, a light bulb), the scene writer
places that ready-made drawing instead of drawing it from shapes. The drawing is detailed, checked and
labelled by parts; placing it is one short object in the scene writer's reply instead of dozens of
shapes, and scenes built around it need fewer corrections. Everything else is drawn as usual. Classic and
planned lessons are exactly as before. Decisions and measurements: ADR-055.

The drawings come from two places:
- **The built-in library**: 31 drawings made for the product (biology, chemistry, physics), each passing
  every check, shipped with the code.
- **The warehouse**: drawings the harvest collected from finished lessons (built from shapes by the scene
  writer) and SVG drawings an admin imported. Each is used only once an admin approves it.

## Making a cost-saver lesson

On **Create**, under Advanced options, **Lesson type: Cost saver** (shown when saved drawings are on). The
server's default for lessons that do not choose: `ANIMATOR_LAYOUT_MODE` (`classic`, `planned` or
`costsaver`).

The lesson's visual director is told which drawings the lesson can use (the ones its script names, in
their views), so it plans those scenes around them: the same thing keeps the same drawing in every scene.

## What it saves

Measured on three one-minute lessons with drawings in the library (the heart, photosynthesis, a circuit),
each made as a planned lesson and twice as a cost-saver lesson, voice off:

| | Planned | Cost saver, 1st | Cost saver, 2nd |
|---|---|---|---|
| AI cost, three lessons | $0.242 | $0.190 | $0.185 |
| Saved drawings placed | 0 | 21 | 29 |
| Scenes degraded | 0 | 0 | 0 |

About a quarter less AI cost, mostly from shorter scene replies (a drawing is one short object instead of
dozens of shapes); every cost-saver lesson cost less than the planned one on the same topic. A lesson
whose scenes show nothing in the library costs what a planned one does.

## Only the right drawing, only where it belongs

Before each scene is written, the scene's brief is matched against the approved drawings. A drawing is
offered to the scene only when:
- one of its **names** appears in the brief as a whole word or phrase ("heart", "plant cell"; plurals
  count: "leaves" names a leaf, "nuclei" a nucleus), within one sentence;
- **no phrase that means something else** appears anywhere in the scene or the lesson's title ("heart
  shape", "computer virus", "sodium atom" for a carbon atom): then it is not offered at all, whatever else
  the scene says. These are always phrases, never one everyday word: "idea" alone would hide the light
  bulb from every scene about "the key idea" (the library build refuses one-word ones; the admin page
  warns). A phrase inside the drawing's own name, where the name is said, does not count ("candle" in
  "red comparison candle"); one that extends a name ("heart shape") does;
- the scene or the lesson is about that kind of thing: one of its **context words** appears outside its
  own names ("blood", "pump", "circulat" for a heart; "light" inside "light bulb" does not count). A
  drawing from the warehouse without context words is never offered;
- where two drawings' names overlap, the **longer name** wins ("plant cell" names the plant cell, not a
  cell).

At most six drawings are offered to a scene (`ANIMATOR_OBJECTS_MAX_OFFERS`), each with what it shows,
its parts and its sizes. The scene writer uses one only for exactly the thing it shows, in the view it
shows (the outside of a heart is not its four chambers); when none fits exactly, it draws the thing
itself, however similar a drawing looks. A similar drawing is never used in place of the right one.

In the scene, a placed drawing:
- is **one object**, sized by its width (or height) within its range; it keeps its proportions and always
  fits the stage;
- has its **parts as anchors**: a label points at `heart.left_ventricle` and its leader line ends on the
  left ventricle as drawn (each part's anchor is a point on what can be seen of it, chosen when the
  drawing is built), an arrow can end on `heart.aorta`;
- keeps its own colors, which follow the lesson's light or dark theme;
- can bring **one part into focus** while the narration names it (`focus`, changed by a `transform`): the
  part is ringed in the highlight color and the rest dims;
- appears with `create`; a reply that draws or writes it is given `create` instead, and a label placed by
  a part (`relativeTo: "heart.right_atrium"`) is placed by the drawing with its leader line to that part
  (sure fixes, made without another model call);
- travels with the lesson: the scene carries the drawing, so the lesson plays the same if the drawing is
  later changed, retired or deleted.

A reply that names a drawing the scene was not offered is never accepted with a substitute: it goes back
to the scene writer as an error (use an offered one, or draw it). Correction rounds see the scene as the
writer wrote it (the short reference, not the drawing).

## Reviewing drawings (Administration → Objects Warehouse)

The page was called Saved drawings until ADR-057 renamed it.

Admins (permissions `objects:read` and `objects:curate`) see every drawing with its checks, where it came
from and how many scenes placed it.

- **Waiting for review**: harvested and imported drawings. Open one to see it (light or dark), where each
  label would point, its checks, and the lesson it came from.
  - **Approve** only what is exactly right. A drawing that is not built-in needs **context words** first
    (for a traffic light: traffic, road, car): without them it would be offered wherever its name
    appears. Every required check must pass.
  - Give it the **names** scene briefs use for it, **never-for phrases**, part labels and sizes.
  - **Reject** what is wrong or not worth keeping (a love-heart drawn as an organ, a chart).
  - **Reject several at once**: each card has a checkbox here (shift-click chooses a range, or choose
    all on the page), then **Reject** in the bar that appears, with an optional review note. Approving
    stays one at a time: each drawing needs its words checked.
- **In use** and **Built-in**: change their words, **Retire** one (no longer offered), **Restore** it;
  built-in drawings can be **reset** to how they ship, never deleted.
- **Try a scene**: paste a scene brief ("heart: the human heart, big in the centre" lines are its
  objects) to see which drawings it would be offered, and which waiting ones would also match once
  approved.
- **Import SVG**: shapes only (text, images, scripts and links are left out; a file with a DOCTYPE is
  refused). `data-part="left_ventricle"` names a part, `data-fill="red.dark"` / `data-stroke` give a
  theme color, `data-anchor="apex"` on a circle marks a named point.
- **Collect from lessons**: reads finished lessons not read before.

## The harvest

In the background (`ANIMATOR_OBJECTS_HARVEST`, every `ANIMATOR_OBJECTS_HARVEST_EVERY_S`), and only while
no lesson is being made, finished lessons are read once each. A drawing is collected when:
- it is a group of plain shapes, or shapes sharing an id prefix (the planned flow's convention for one
  drawing), with at least three shapes and no text, icons or components in it;
- the scene's brief names it (`grape_cluster: a bunch of purple grapes`), and the name is of a thing, not
  of a chart, map, road, diagram or scene;
- its shapes form one connected drawing at least 60 px wide and tall, and it passes the checks.

The engine draws the scene at a moment the whole drawing is on screen; the shapes are taken exactly as
drawn (moves, scales and groups applied), with the scene's theme colors. At most three drawings of one
thing wait for review (the best ones). Drawings turned down are remembered and not checked again. No
lesson is ever changed.

## The checks

Every drawing, built-in or not: valid path data and known colors; it fills its box (nothing scattered,
no empty margins); every part can be seen and has its anchor on it; anchors inside the drawing;
proportions between 1:5 and 5:1; something filled; enough detail; every part labelled; compact path data.
Harvested drawings also: from an accepted scene. The score (0-1) orders the review queue.

## The built-in library

`apps/api/src/animator/objects/library/src/` holds each drawing as an SVG (with the `data-*` names
above) and a JSON file (its names, context words, never-for phrases, part labels, sizes). The built files
in `library/` are what lessons use:

```
uv run python -m animator.objects build   # rebuild after changing a drawing; fails on any failed check
uv run python -m animator.objects check   # the built files match their sources (the tests check too)
uv run python -m animator.objects list    # what lessons can use now
```

Admins' changes to built-in drawings (words, retiring) are kept beside them in
`var/objects/overrides/`; the drawings themselves never change outside the build.

## Settings

| Setting | Default | |
|---|---|---|
| `ANIMATOR_OBJECTS_ENABLED` | true | false: cost-saver lessons are made as planned ones; nothing is offered or harvested |
| `ANIMATOR_OBJECTS_DIR` | var/objects | the warehouse, admins' decisions, the harvest's progress, uses |
| `ANIMATOR_OBJECTS_HARVEST` | true | collect from finished lessons in the background (false: only when an admin asks) |
| `ANIMATOR_OBJECTS_HARVEST_EVERY_S` | 900 | how often the background harvest looks |
| `ANIMATOR_OBJECTS_MAX_OFFERS` | 6 | drawings offered to one scene at most |

## Where things are

- Engine: `packages/engine/src/catalog/figure.ts` (the `figure` object: hidden from the model's schema,
  catalog and docs), `packages/components/src/renderers/figure.tsx`.
- API: `apps/api/src/animator/objects/` (geometry, SVG import, checks, matching, placing, the store, the
  harvest, the library), `pipeline/prompts/animation-costsaver.v1.md` (the cost-saver scene writer),
  `platform/api/objects.py` (the admin API).
- Web: `apps/web/src/platform/pages/AdminObjects.tsx`, Create's lesson type.
