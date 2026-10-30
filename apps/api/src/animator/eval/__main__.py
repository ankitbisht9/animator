"""Run the live eval set, or part of it, and write a report.

    uv run python -m animator.eval                                   # every topic
    uv run python -m animator.eval --only archimedes,photosynthesis --duration-min 1
    uv run python -m animator.eval --name astra --set ANIMATOR_MODEL_ANIMATION_PLANNER=gpt-6-astra
    uv run python -m animator.eval --name flash --trial-model qwen/qwen3.5-flash-02-23   # ADR-056
    uv run python -m animator.eval --only git_integration --critic     # with the visual critic (ADR-061)
    uv run python -m animator.eval --only archimedes --quiz             # and its TeachQuiz (ADR-070)
    uv run python -m animator.eval --score var/live-063b --pictures     # TeachQuiz of lessons already made

Reports go to var/eval/<name>/report.md and report.json; each generation is stored there too and
can be opened with the API's FileStore (and the debug page) by pointing ANIMATOR_DATA_DIR at it.
For a model bake-off, run once per configuration with --set and compare the reports. With
--trial-model (one of ANIMATOR_TRIAL_MODELS) the lessons are made with that OpenRouter model, as an
admin's trial lesson would be (ADR-056). With --critic, keyframes are rendered for the visual critic
as the app does for teaching lessons (ANIMATOR_KEYFRAMES: the built web app and Chromium, or drawn
without a browser); without it, no critic.

TeachQuiz (ADR-070): with --quiz each new lesson is scored (0 to 100: the share of a quiz on what a lesson
of its length must teach that a student answers right from the lesson alone, quoting it); --score DIR
scores the lessons stored in DIR (a data folder holding generations/) without making any. --pictures
shows the student a picture of each scene too. A topic's quiz is written once and kept in
var/eval/quizzes, so lessons made with other settings or models are judged on the same questions.
About a cent a lesson with the default model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from animator.config import Settings, get_settings
from animator.engine_client import EngineClient, pool_from_settings
from animator.eval import generation_metrics, summarize
from animator.eval.quiz import Quiz, questions_for, teachquiz
from animator.export.jobs import keyframes_for
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import FileStore
from animator.llm import LLMClient, ModelCatalog, PriceTable
from animator.llm.openai_client import OpenAILLM
from animator.llm.openrouter_client import OpenRouterLLM
from animator.llm.trials import TrialRouter
from animator.objects.service import ObjectLibrary
from animator.pipeline.generator import KeyframeSource, Pipeline
from animator.pipeline.models import Generation, GenerationRequest
from animator.pipeline.prompts import slug

QUIZZES = Path("var/eval/quizzes")


def parse_sets(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--set expects KEY=VALUE, got '{item}'")
        out[key.strip().lower()] = value.strip()
    return out


def report_md(
    name: str,
    settings: Settings,
    rows: list[dict[str, Any]],
    total: dict[str, Any],
    trial_model: str | None = None,
) -> str:
    models = {
        s: ", ".join(settings.stage_model(s)[0])
        for s in ("educational_planner", "storyboard", "animation_planner", "correction", "correction_final")
    }
    used = (
        f"Trial model (OpenRouter): {trial_model}; the normal models only where it cannot read images"
        if trial_model
        else "Models: " + "; ".join(f"{k} = {v}" for k, v in models.items())
    )
    quiz = any(r.get("teachquiz") is not None for r in rows)
    lines = [
        f"# Eval run `{name}`",
        "",
        used,
        "",
        "| Metric | Value |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in total.items()],
        "",
        "| Topic | Status | Scenes | Video s | First-pass valid | Ready within 3 fixes | Degraded "
        "| TTFP s | Cost $ | $/video min |" + (" TeachQuiz |" if quiz else ""),
        "|---|---|---|---|---|---|---|---|---|---|" + ("---|" if quiz else ""),
    ]
    for r in rows:
        lines.append(
            f"| {r['topic_id']} | {r['status']} | {r['scenes']} | {r['video_s']} | {r['first_pass_valid']} | "
            f"{r['ready_within_3_corrections']} | {r['degraded_rate']} | {r['first_play_s']} | "
            f"{r['cost_usd']} | "
            f"{r['cost_per_video_min']} |" + (f" {r.get('teachquiz')} |" if quiz else "")
        )
    errors = [f"- {r['topic_id']}: {r['error']}" for r in rows if r.get("error")]
    if errors:
        lines += ["", "Errors:", *errors]
    return "\n".join(lines) + "\n"


def quiz_file(settings: Settings, gen: Generation) -> Path:
    """Where a topic's quiz is kept: one per topic, audience and length asked for (and question count)."""
    r = gen.request
    seconds = r.duration_min * 60
    name = f"{slug(r.topic, 48)}-{slug(r.audience, 24)}-{round(seconds)}s-{questions_for(seconds)}q"
    return settings.resolve(QUIZZES) / f"{name}.json"


async def pictures_of(gen: Generation, frames: KeyframeSource) -> list[bytes]:
    """One picture of each scene a learner sees, near its end (the whole picture by then)."""
    recs = [s for s in gen.scenes if s.playable and s.dsl is not None]
    dsls = [s.dsl for s in recs if s.dsl is not None]
    out: list[bytes] = []
    for i, rec in enumerate(recs):
        t = max(0.1, float(rec.duration or (rec.dsl or {}).get("duration") or 5) * 0.9)
        out += await frames.keyframes(dsls, i, [t], finish=gen.request.finish or "classic")
    return out


async def quiz_lesson(
    llm: LLMClient, settings: Settings, gen: Generation, frames: KeyframeSource | None
) -> dict[str, Any]:
    """A lesson's TeachQuiz, on its topic's kept quiz (written now when there is none)."""
    path = quiz_file(settings, gen)
    kept = Quiz.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None
    pictures = await pictures_of(gen, frames) if frames is not None else []
    result, quiz = await teachquiz(llm, settings, gen, kept, pictures)
    if kept is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(quiz.model_dump_json(indent=2), encoding="utf-8")
    return result


def make_llm(settings: Settings, trial_model: str | None = None) -> LLMClient:
    prices = PriceTable.load(settings.resolve(settings.animator_pricing_file))
    llm: LLMClient = OpenAILLM(
        settings, prices, ModelCatalog.load(settings.resolve(settings.animator_models_file))
    )
    if trial_model:
        llm = TrialRouter(llm, OpenRouterLLM(settings, prices), settings.trial_models)
    return llm


async def score_stored(
    llm: LLMClient,
    settings: Settings,
    folder: Path,
    frames: KeyframeSource | None,
    only: set[str] | None = None,
) -> list[dict[str, Any]]:
    """TeachQuiz of the finished lessons stored in `folder` (a data folder holding generations/)."""
    store = FileStore(folder)
    rows: list[dict[str, Any]] = []
    for gen in sorted(await store.recent(limit=10_000), key=lambda g: g.created_at):
        if gen.status != "complete" or (only and gen.id not in only):
            continue
        result = await quiz_lesson(llm, settings, gen, frames)
        row = {
            "id": gen.id,
            "topic": gen.request.topic,
            "duration_min": gen.request.duration_min,
            "language": gen.request.language,
            **result,
        }
        rows.append(row)
        print(
            f"[teachquiz] {gen.id} {gen.request.topic[:48]!r}: {row['teachquiz']} "
            f"({row['right_and_supported']}/{row['questions']} right and supported, "
            f"{row['not_taught']} not taught), ${row['cost_usd']}",
            flush=True,
        )
    return rows


def teachquiz_md(name: str, rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# TeachQuiz `{name}`",
        "",
        "| Lesson | Topic | Min | TeachQuiz | Right and supported | Right | Not taught | Pictures | Cost $ |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['topic'][:60]} | {r['duration_min']:g} | {r['teachquiz']} | "
            f"{r['right_and_supported']}/{r['questions']} | {r['right']} | {r['not_taught']} | "
            f"{r['pictures']} | {r['cost_usd']} |"
        )
    if rows:
        mean = sum(r["teachquiz"] for r in rows) / len(rows)
        lines += [
            "",
            f"Mean TeachQuiz: {mean:.1f} over {len(rows)} lessons; "
            f"cost ${sum(r['cost_usd'] for r in rows):.4f}.",
        ]
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> int:
    base = get_settings()
    settings = base.model_copy(update=parse_sets(args.set)) if args.set else base
    if not settings.has_openai_key:
        print("The eval suite calls OpenAI: set OPENAI_API_KEY in .env first.", file=sys.stderr)
        return 1
    if args.score:
        return await run_score(args, settings)
    topics = json.loads(settings.resolve(Path(args.topics)).read_text(encoding="utf-8"))["topics"]
    if args.only:
        wanted = {t.strip() for t in args.only.split(",")}
        topics = [t for t in topics if t["id"] in wanted]
    if args.limit:
        topics = topics[: args.limit]
    if args.trial_model and args.trial_model not in settings.trial_models:
        listed = ", ".join(settings.trial_models) or "none (set OPENROUTER_API_KEY and ANIMATOR_TRIAL_MODELS)"
        print(f"--trial-model must be one of ANIMATOR_TRIAL_MODELS: {listed}", file=sys.stderr)
        return 1
    name = args.name or time.strftime("run-%Y%m%d-%H%M%S")
    out = settings.resolve(Path("var/eval")) / name
    out.mkdir(parents=True, exist_ok=True)
    if args.trial_model:
        # The trial log goes with these lessons (ANIMATOR_DATA_DIR pointed here shows them as trials).
        settings = settings.model_copy(update={"animator_data_dir": out})

    pool = pool_from_settings(settings)
    await pool.start()
    frames = keyframes_for(settings) if args.critic or (args.quiz and args.pictures) else None
    try:
        llm = make_llm(settings, args.trial_model)
        quiz_llm = make_llm(settings)  # the quiz is never the trial model's
        # Saved drawings are read as lessons would; evaluation lessons record no uses (ADR-055).
        objects = ObjectLibrary(settings, record=False) if settings.animator_objects_enabled else None
        pipeline = Pipeline(EngineClient(pool), llm, settings, frames, objects=objects)
        runner = GenerationRunner(FileStore(out), pipeline, settings)
        rows: list[dict[str, Any]] = []
        for t in topics:
            req = GenerationRequest(
                topic=t["topic"],
                audience=t.get("audience", "high-school student"),
                duration_min=args.duration_min or t.get("duration_min", 2),
                trial_model=args.trial_model or None,
            )
            print(f"[eval] {t['id']}: {req.topic} ({req.duration_min} min)", flush=True)
            gen = await runner.start(req)
            await runner._tasks[gen.id]
            final = await runner.get(gen.id)
            assert final is not None
            row = {"topic_id": t["id"], **generation_metrics(final)}
            if args.quiz and final.status == "complete":
                result = await quiz_lesson(quiz_llm, settings, final, frames if args.pictures else None)
                row["teachquiz"] = result["teachquiz"]
                row["teachquiz_cost_usd"] = result["cost_usd"]
            rows.append(row)
            print(
                f"[eval]   {row['status']}: {row['scenes']} scenes, ${row['cost_usd']}, "
                f"first play {row['first_play_s']} s",
                flush=True,
            )
            if final.status == "failed" and final.error and "API key" in final.error:
                print("[eval] stopping: the API key was rejected", file=sys.stderr)
                break
        total = summarize(rows)
        (out / "report.json").write_text(
            json.dumps({"name": name, "summary": total, "topics": rows}, indent=2), encoding="utf-8"
        )
        (out / "report.md").write_text(
            report_md(name, settings, rows, total, args.trial_model), encoding="utf-8"
        )
        print(f"[eval] report: {out / 'report.md'}")
        return 0
    finally:
        if frames is not None:
            await frames.close()
        await pool.close()


async def run_score(args: argparse.Namespace, settings: Settings) -> int:
    folder = settings.resolve(Path(args.score))
    if not (folder / "generations").is_dir():
        print(f"--score expects a data folder holding generations/: {folder}", file=sys.stderr)
        return 1
    name = args.name or time.strftime("teachquiz-%Y%m%d-%H%M%S")
    out = settings.resolve(Path("var/eval")) / name
    out.mkdir(parents=True, exist_ok=True)
    frames = keyframes_for(settings) if args.pictures else None
    try:
        only = {x.strip() for x in args.only.split(",")} if args.only else None
        rows = await score_stored(make_llm(settings), settings, folder, frames, only)
    finally:
        if frames is not None:
            await frames.close()
    (out / "teachquiz.json").write_text(
        json.dumps({"name": name, "lessons": rows}, indent=2), encoding="utf-8"
    )
    (out / "teachquiz.md").write_text(teachquiz_md(name, rows), encoding="utf-8")
    print(f"[teachquiz] report: {out / 'teachquiz.md'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m animator.eval")
    ap.add_argument("--topics", default="config/eval_topics.json")
    ap.add_argument("--only", help="comma-separated topic ids")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--duration-min", type=float, help="override every topic's duration")
    ap.add_argument("--name", help="run name (default: timestamp)")
    ap.add_argument(
        "--set", action="append", default=[], help="setting override, e.g. ANIMATOR_MODEL_STORYBOARD=gpt-5"
    )
    ap.add_argument("--trial-model", help="make the lessons with this OpenRouter model (ADR-056)")
    ap.add_argument(
        "--critic", action="store_true", help="render keyframes for the visual critic, as the app does"
    )
    ap.add_argument("--quiz", action="store_true", help="score each new lesson with TeachQuiz (ADR-070)")
    ap.add_argument("--score", help="score the lessons stored in this data folder with TeachQuiz; make none")
    ap.add_argument(
        "--pictures", action="store_true", help="TeachQuiz: show the student a picture of each scene"
    )
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
