"""Vendor open-licensed drawings into a drawing pack (ADR-066, docs/OBJECTS.md).

    uv run python scripts/vendor_drawings.py scan   <src> <work>   # import + check + de-duplicate
    uv run python scripts/vendor_drawings.py thumbs <work>         # a PNG of each (headless Chromium)
    uv run python scripts/vendor_drawings.py label  <work>         # a vision model names each one (cached)
    uv run python scripts/vendor_drawings.py sheets <work>         # contact sheets, to review by eye
    uv run python scripts/vendor_drawings.py write  <work>         # -> objects/packs/<pack>.jsonl.gz

`<src>` is a folder in Bioicons' layout, `<licence>/<category>/<author>/<name>.svg` (the licence, the
subject and the author come from the path; get the files from https://github.com/duerrsimon/bioicons).
`<work>` keeps every step's output, so each step can be re-run alone; the labels are cached by drawing,
so labelling again costs nothing. `<work>/reject.txt` (one id per line) leaves drawings out of the pack.

Nothing here runs in the app: the app reads the written pack.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import dataclasses
import gzip
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from animator import REPO_ROOT
from animator.objects.models import SavedObject, Size, Source
from animator.objects.packs import LICENCES, PACKS_DIR
from animator.objects.quality import check, to_art, with_anchors
from animator.objects.svg import parse_svg

PACK = "bioicons"
PACK_TITLE = "Bioicons"
PACK_URL = "https://bioicons.com"
REPO_URL = "https://github.com/duerrsimon/bioicons"

# Bioicons' categories: the lesson subjects they serve, and the words of the lessons that would use them
# (the labeller adds its own; these are the first draft).
CATEGORIES: dict[str, tuple[list[str], list[str]]] = {
    "Human_physiology": (["biology", "medicine"], ["body", "organ", "human", "anatom", "physiolog"]),
    "Animals": (["biology"], ["animal", "species", "habitat", "mammal", "insect", "creature", "zoo"]),
    "Blood_Immunology": (["biology", "medicine"], ["blood", "immun", "antibod", "infect", "cell", "vaccin"]),
    "General_items": (["general"], []),
    "Lab_apparatus": (["chemistry", "biology"], ["lab", "experiment", "sample", "measur", "scien"]),
    "Microbiology": (["biology"], ["microb", "bacteri", "germ", "cell", "infect", "microscop"]),
    "Intracellular_components": (["biology"], ["cell", "organelle", "protein", "membrane", "molecul"]),
    "Nucleic_acids": (["biology", "chemistry"], ["dna", "rna", "gene", "nucle", "molecul", "base"]),
    "Plants_Algae": (["biology"], ["plant", "leaf", "grow", "photosynth", "root", "seed", "fruit"]),
    "Chemistry": (["chemistry"], ["chemi", "molecul", "atom", "bond", "reaction", "compound"]),
    "Cell_membrane": (["biology"], ["cell", "membrane", "transport", "lipid", "protein"]),
    "Genetics": (["biology"], ["gene", "dna", "chromosom", "inherit", "mutation"]),
    "Parasites": (["biology", "medicine"], ["parasit", "infect", "host", "disease", "worm"]),
    "Tissues": (["biology", "medicine"], ["tissue", "cell", "organ", "body"]),
    "Cell_culture": (["biology"], ["cell", "culture", "lab", "dish", "grow"]),
    "Oncology": (["biology", "medicine"], ["cancer", "tumor", "tumour", "cell"]),
    "Cell_types": (["biology"], ["cell", "stem", "tissue"]),
    "Viruses": (["biology", "medicine"], ["virus", "viral", "infect", "vaccin", "disease"]),
    "Receptors_channels": (["biology"], ["receptor", "channel", "membrane", "signal", "cell"]),
    "People-Other": (["general"], ["person", "people", "patient", "doctor"]),
    "Extracellular_matrix": (["biology"], ["cell", "tissue", "matrix"]),
    "Neuroscience": (["biology"], ["brain", "neuron", "nerve", "signal"]),
    "Nanotechnology": (["chemistry", "physics"], ["nano", "particle", "molecul"]),
}

# A standard public DOCTYPE (no internal subset, no entities) carries nothing: it is removed. Anything
# else with a DOCTYPE or an entity is refused by the importer, as in the app.
_DOCTYPE = re.compile(r"<!DOCTYPE\s+svg\s+PUBLIC\s+\"[^\"\[]*\"\s+\"[^\"\[]*\"\s*>", re.IGNORECASE)
MAX_PATH_CHARS = 60_000  # the warehouse's "compact" check
UNNATURAL = {"blue", "green", "orange", "yellow", "purple", "cyan", "violet"}
COLOURS = UNNATURAL | {"red", "pink", "brown", "gray", "grey", "white", "black", "clear", "light", "dark"}
COLOURS |= {"pale", "teal", "tan", "magenta", "lilac"}


def humanize(name: str) -> str:
    """'falciform-erythrocyte-3' -> 'Falciform erythrocyte', 'MossColony' -> 'Moss colony'."""
    n = re.sub(r"\.svg$", "", name)
    n = re.sub(r"[\s_-]*\(\d+\)$", "", n)
    n = re.sub(r"[-_ ]?\d+$", "", n)
    n = re.sub(r"([a-z])([A-Z])", r"\1 \2", n)
    n = re.sub(r"[-_]+", " ", n).strip()
    n = re.sub(r"\s+", " ", n)
    return (n[:1].upper() + n[1:].lower()) if n else "Drawing"


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "drawing"
    return s if s[0].isalpha() else f"d_{s}"


# ---------------------------------------------------------------------- scan


def scan(src: Path, work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    seen: set[str] = set()
    files = sorted(src.rglob("*.svg"))
    for i, f in enumerate(files):
        rel = f.relative_to(src).parts
        if len(rel) != 4:
            reasons["not in licence/category/author"] = reasons.get("not in licence/category/author", 0) + 1
            continue
        lic, cat, author, name = rel
        if lic not in LICENCES or cat not in CATEGORIES:
            reasons["licence or category"] = reasons.get("licence or category", 0) + 1
            continue
        try:
            text = _DOCTYPE.sub("", f.read_text(encoding="utf-8", errors="replace"))
            imported = parse_svg(text)
            # Ids of a drawing program are not part names a lesson can point at.
            shapes = [dataclasses.replace(s, part=None) for s in imported.shapes]
            art, spots = with_anchors(to_art(shapes, []))
        except ValueError as e:
            key = str(e).split("(")[0][:60]
            reasons[key] = reasons.get(key, 0) + 1
            continue
        if sum(len(s.d) for s in art.shapes) > MAX_PATH_CHARS:
            reasons["too much detail"] = reasons.get("too much detail", 0) + 1
            continue
        fp = art.fingerprint()
        if fp in seen:
            reasons["duplicate"] = reasons.get("duplicate", 0) + 1
            continue
        title = humanize(name)
        oid = f"{slug(title)}_{fp[:6]}"
        label, url = LICENCES[lic]
        subjects, context = CATEGORIES[cat]
        try:
            obj = SavedObject(
                id=oid,
                title=title[:80],
                concept=title.lower()[:80],
                aliases=[title.lower()[:60]],
                context=context,
                subjects=subjects,
                art=art,
                status="candidate",
                origin="library",
                source=Source(
                    kind="library",
                    pack=PACK,
                    file=f"{cat}/{author}/{name}"[:120],
                    author=author.replace("_", " ").replace("--", " ").replace("-", " ").strip()[:120],
                    license=label,
                    license_url=url,
                    url=f"{REPO_URL}/blob/main/static/icons/{lic}/{cat}/{author}/{name}"[:300],
                ),
                fingerprint=fp,
            )
        except ValueError as e:
            reasons["invalid: " + str(e)[:40]] = reasons.get("invalid: " + str(e)[:40], 0) + 1
            continue
        quality = check(obj, spots)
        failed = [c.name for c in quality.checks if not c.ok]
        if failed:
            for c in failed:
                reasons[f"check {c}"] = reasons.get(f"check {c}", 0) + 1
            continue
        seen.add(fp)
        out.append({"obj": obj.model_copy(update={"quality": quality}).model_dump(), "category": cat})
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(files)} read, {len(out)} kept", flush=True)
    (work / "candidates.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8"
    )
    print(f"{len(files)} files, {len(out)} candidates")
    for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  left out: {k}: {v}")


def candidates(work: Path) -> list[dict[str, Any]]:
    path = work / "candidates.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------- thumbnails


def svg_of(art: dict[str, Any], size: int) -> str:
    """The art as a standalone SVG (pack art is in hex colours; a role falls back to grey)."""
    w, h = float(art["w"]), float(art["h"])
    body = []
    for s in art["shapes"]:
        fill = s.get("fill") or "none"
        stroke = s.get("stroke") or "none"
        if not fill.startswith("#") and fill != "none":
            fill = "#8a94a6"
        if not stroke.startswith("#") and stroke != "none":
            stroke = "#1c2541"
        attrs = [f'd="{html.escape(s["d"])}"', f'fill="{fill}"', f'stroke="{stroke}"']
        if s.get("strokeWidth"):
            attrs.append(f'stroke-width="{s["strokeWidth"]}"')
        if s.get("opacity") is not None:
            attrs.append(f'opacity="{s["opacity"]}"')
        if s.get("fillRule"):
            attrs.append(f'fill-rule="{s["fillRule"]}"')
        attrs.append('stroke-linejoin="round"')
        body.append(f"<path {' '.join(attrs)}/>")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{size}" height="{size}" '
        f'preserveAspectRatio="xMidYMid meet">{"".join(body)}</svg>'
    )


async def _thumbs(work: Path, size: int = 256) -> None:
    from playwright.async_api import async_playwright

    out_dir = work / "thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = candidates(work)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": size, "height": size})
        for i, item in enumerate(items):
            obj = item["obj"]
            target = out_dir / f"{obj['id']}.png"
            if target.exists():
                continue
            doc = f"<html><body style='margin:0;background:#ffffff'>{svg_of(obj['art'], size)}</body></html>"
            await page.set_content(doc)
            await page.screenshot(path=str(target), clip={"x": 0, "y": 0, "width": size, "height": size})
            if (i + 1) % 200 == 0:
                print(f"  {i + 1}/{len(items)}", flush=True)
        await browser.close()
    print(f"{len(items)} thumbnails in {out_dir}")


# ---------------------------------------------------------------------- labels


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int = Field(description="The drawing's number in the message")
    keep: bool = Field(description="A clear drawing of one thing (or one small group) a lesson would show")
    why: str = Field(description="A few words: what it is, or why not")
    title: str = Field(description="2 to 5 words, English, capitalised")
    concept: str = Field(description="What it is, in plain words (lower case)")
    description: str = Field(description="What the drawing shows: the thing, its view, its look")
    view: str = Field(description="The view in 2 to 5 words (e.g. 'side view', 'cross-section')")
    aliases: list[str] = Field(description="2 to 8 names a script uses for exactly this thing, lower case")
    context: list[str] = Field(description="4 to 10 word stems a lesson about it contains, lower case")
    not_for: list[str] = Field(description="0 to 4 phrases (2+ words) where its name means something else")
    prefer: list[str] = Field(description="0 to 4 word stems that make it the right one of its kind")
    subjects: list[str] = Field(description="biology, chemistry, physics, medicine, earth_science, general")


class Labels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Label]


LABEL_PROMPT = (Path(__file__).parent / "vendor_drawings.labels.md").read_text(encoding="utf-8")


async def _label(work: Path, batch: int, limit: int | None) -> None:
    from animator.config import get_settings
    from animator.llm import CallContext, ImagePart, LLMRequest, Message, build_llm
    from animator.pipeline.schemas import strict_schema

    settings = get_settings()
    models, _ = settings.stage_model("lightweight")
    llm = build_llm()
    cache_path = work / "labels.jsonl"
    cache: dict[str, dict[str, Any]] = {}
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                cache[rec["fingerprint"]] = rec["label"]
    todo = [c for c in candidates(work) if c["obj"]["fingerprint"] not in cache]
    if limit is not None:
        todo = todo[:limit]
    schema = strict_schema(Labels)
    spent = 0.0
    sem = asyncio.Semaphore(6)

    async def one(group: list[dict[str, Any]]) -> None:
        nonlocal spent
        parts: list[Any] = []
        for k, c in enumerate(group):
            obj = c["obj"]
            png = (work / "thumbs" / f"{obj['id']}.png").read_bytes()
            parts.append(f"Drawing {k + 1}: file '{obj['source']['file']}', folder '{c['category']}'.")
            parts.append(ImagePart(png, "low"))
        req = LLMRequest(
            stage="lightweight",
            prompt_version="vendor-labels.v1",
            instructions=LABEL_PROMPT,
            messages=[Message.user(*parts)],
            schema_name="Labels",
            schema=schema,
            models=models,
            effort="low",
            max_output_tokens=8000,
            generation_id="vendor-drawings",
            label="drawing labels",
            cache_key="animator-vendor-labels.v1",
        )
        async with sem:
            try:
                res = await llm.generate(req, CallContext())
            except Exception as e:  # a failed batch is labelled when this step runs again
                print(f"  batch failed: {e}", flush=True)
                return
        spent += res.cost_usd or 0.0
        got = Labels.model_validate(res.data)
        lines = []
        for item in got.items:
            if 1 <= item.n <= len(group):
                fp = group[item.n - 1]["obj"]["fingerprint"]
                lines.append(json.dumps({"fingerprint": fp, "label": item.model_dump()}, ensure_ascii=False))
        with cache_path.open("a", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in lines))

    groups = [todo[i : i + batch] for i in range(0, len(todo), batch)]
    t0 = time.time()
    for i in range(0, len(groups), 12):
        await asyncio.gather(*(one(g) for g in groups[i : i + 12]))
        done = min(i + 12, len(groups))
        print(f"  {done}/{len(groups)} batches, ${spent:.3f}, {time.time() - t0:.0f}s", flush=True)
    close = getattr(llm, "close", None)
    if callable(close):
        await close()
    print(f"labelled {len(todo)} drawings for ${spent:.3f}")


def labels(work: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    path = work / "labels.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["fingerprint"]] = rec["label"]
    return out


# ---------------------------------------------------------------------- the pack


def _words(values: list[str], least_words: int = 1, limit: int = 12) -> list[str]:
    out: list[str] = []
    for v in values:
        v = " ".join(str(v).lower().split())[:60]
        if v and len(v.split()) >= least_words and v not in out:
            out.append(v)
    return out[:limit]


def assemble(work: Path) -> tuple[list[SavedObject], dict[str, int]]:
    """The pack's drawings: the candidates a label keeps, named by it, approved; minus reject.txt."""
    reject = set()
    rej = work / "reject.txt"
    if rej.exists():
        reject = {line.strip() for line in rej.read_text(encoding="utf-8").splitlines() if line.strip()}
    named = labels(work)
    out: list[SavedObject] = []
    counted = ("candidates", "unlabelled", "not kept", "rejected", "invalid", "repeats", "kept")
    stats = dict.fromkeys(counted, 0)
    used_ids: set[str] = set()
    for c in candidates(work):
        stats["candidates"] += 1
        raw = c["obj"]
        label = named.get(raw["fingerprint"])
        if label is None:
            stats["unlabelled"] += 1
            continue
        if not label.get("keep"):
            stats["not kept"] += 1
            continue
        if raw["id"] in reject:
            stats["rejected"] += 1
            continue
        # Lab-animal colour coding (a blue mouse, a green pig) is not how a lesson shows an animal, and
        # Servier's empty-cell pieces (quarter membranes, blank cells in six colours) are parts to build
        # with, not things a lesson shows.
        first = str(label.get("title") or "").split(" ")[0].lower()
        name = Path(str(raw["source"]["file"])).name.lower()
        if (c["category"] == "Animals" and first in UNNATURAL) or name.startswith("emptycell"):
            stats["rejected"] += 1
            continue
        title = " ".join(str(label.get("title") or raw["title"]).split())[:80] or raw["title"]
        oid = f"{slug(title)}_{raw['fingerprint'][:6]}"
        if oid in used_ids:
            oid = f"{oid}_{len(used_ids)}"
        aliases = _words([*label.get("aliases", []), title.lower()], 1, 12)
        cat_subjects, cat_context = CATEGORIES.get(c["category"], ([], []))
        context = _words([*label.get("context", []), *cat_context], 1, 16)
        not_for = _words(label.get("not_for", []), 2, 8)
        try:
            obj = SavedObject.model_validate(
                {
                    **raw,
                    "id": oid,
                    "title": title,
                    "concept": " ".join(str(label.get("concept") or title).split())[:80],
                    "description": " ".join(str(label.get("description") or "").split())[:300],
                    "view": " ".join(str(label.get("view") or "").split())[:40],
                    "aliases": aliases,
                    "context": context,
                    "not_for": not_for,
                    "prefer": _words(label.get("prefer", []), 1, 8),
                    "subjects": _words([*label.get("subjects", []), *cat_subjects], 1, 4),
                    "size": Size(min=120, default=300, max=640).model_dump(),
                    "status": "approved",
                    "note": f"From {PACK_TITLE} ({raw['source']['license']}).",
                }
            )
        except ValueError as e:
            stats["invalid"] += 1
            print(f"  invalid {raw['id']}: {str(e)[:120]}")
            continue
        used_ids.add(oid)
        out.append(obj)
        stats["kept"] += 1
    # At most two drawings of one name, and three of one thing in different colours (a blue, a green
    # and a pink IgG antibody), the best checked: a scene is offered a choice, not a crowd.
    by_title: dict[str, list[SavedObject]] = {}
    by_thing: dict[str, list[SavedObject]] = {}
    for o in out:
        by_title.setdefault(o.title.lower(), []).append(o)
        words = o.title.lower().split()
        while len(words) > 1 and words[0] in COLOURS:
            words = words[1:]
        by_thing.setdefault(" ".join(words), []).append(o)

    def best(group: list[SavedObject], n: int) -> set[str]:
        group.sort(key=lambda o: (-(o.quality.score if o.quality else 0.0), o.id))
        return {o.id for o in group[:n]}

    keep: set[str] = set()
    for same in by_title.values():
        keep |= best(same, 2)
    for same in by_thing.values():
        keep &= best(same, 3) | {o.id for o in out if o not in same}
    stats["repeats"] = len(out) - len(keep)
    out = [o for o in out if o.id in keep]
    stats["kept"] = len(out)
    return out, stats


async def _sheets(work: Path, per: int = 80) -> None:
    from playwright.async_api import async_playwright

    objs, stats = assemble(work)
    print(stats)
    out_dir = work / "sheets"
    out_dir.mkdir(parents=True, exist_ok=True)
    thumbs = work / "thumbs"
    by_fp = {c["obj"]["fingerprint"]: c["obj"]["id"] for c in candidates(work)}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 1200})
        for s in range(0, len(objs), per):
            cells = []
            for o in objs[s : s + per]:
                src = thumbs / f"{by_fp[o.fingerprint]}.png"
                data = base64.b64encode(src.read_bytes()).decode()
                cells.append(
                    "<div class='c'><img src='data:image/png;base64," + data + "'/>"
                    f"<b>{html.escape(o.title)}</b><i>{html.escape(', '.join(o.aliases[:3]))}</i>"
                    f"<u>{html.escape(by_fp[o.fingerprint])}</u></div>"
                )
            doc = (
                "<html><head><style>body{margin:8px;font:11px sans-serif}"
                ".g{display:grid;grid-template-columns:repeat(10,1fr);gap:6px}"
                ".c{border:1px solid #ddd;padding:4px;display:flex;flex-direction:column}"
                "img{width:100%;aspect-ratio:1;object-fit:contain}b{font-size:12px}i{color:#555}u{color:#999;"
                "text-decoration:none;font-size:9px}</style></head><body><div class='g'>"
                + "".join(cells)
                + "</div></body></html>"
            )
            await page.set_content(doc)
            await page.screenshot(path=str(out_dir / f"sheet-{s // per + 1:02d}.png"), full_page=True)
        await browser.close()
    print(f"{(len(objs) + per - 1) // per} sheets in {out_dir}")


def write(work: Path) -> None:
    objs, stats = assemble(work)
    print(stats)
    PACKS_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": PACK,
        "title": PACK_TITLE,
        "url": PACK_URL,
        "source": REPO_URL,
        "licences": sorted({o.source.license or "" for o in objs}),
        "built": time.strftime("%Y-%m-%d"),
    }
    lines = [json.dumps({"pack": meta}, ensure_ascii=False, separators=(",", ":"))]
    lines += [o.model_dump_json() for o in objs]
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    target = PACKS_DIR / f"{PACK}.jsonl.gz"
    target.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    (PACKS_DIR / f"{PACK}.json.gz").unlink(missing_ok=True)
    print(f"{len(objs)} drawings -> {target.relative_to(REPO_ROOT)} ({target.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["scan", "thumbs", "label", "sheets", "write"])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.step == "scan":
        scan(Path(a.paths[0]), Path(a.paths[1]))
    elif a.step == "thumbs":
        asyncio.run(_thumbs(Path(a.paths[0])))
    elif a.step == "label":
        asyncio.run(_label(Path(a.paths[0]), a.batch, a.limit))
    elif a.step == "sheets":
        asyncio.run(_sheets(Path(a.paths[0])))
    else:
        write(Path(a.paths[0]))


if __name__ == "__main__":
    sys.exit(main())
