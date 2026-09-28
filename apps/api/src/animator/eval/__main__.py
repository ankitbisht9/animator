"""Run the live eval set, or part of it, and write a report.

    uv run python -m animator.eval                                   # every topic
    uv run python -m animator.eval --only archimedes,photosynthesis --duration-min 1
    uv run python -m animator.eval --name astra --set ANIMATOR_MODEL_ANIMATION_PLANNER=gpt-6-astra

Reports go to var/eval/<name>/report.md and report.json; each generation is stored there too and
can be opened with the API's FileStore (and the debug page) by pointing ANIMATOR_DATA_DIR at it.
For a model bake-off, run once per configuration with --set and compare the reports.
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
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import FileStore
from animator.llm import ModelCatalog, PriceTable
from animator.llm.openai_client import OpenAILLM
from animator.pipeline.generator import Pipeline
from animator.pipeline.models import GenerationRequest


def parse_sets(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--set expects KEY=VALUE, got '{item}'")
        out[key.strip().lower()] = value.strip()
    return out


def report_md(name: str, settings: Settings, rows: list[dict[str, Any]], total: dict[str, Any]) -> str:
    models = {
        s: ", ".join(settings.stage_model(s)[0])
        for s in ("educational_planner", "storyboard", "animation_planner", "correction", "correction_final")
    }
    lines = [
        f"# Eval run `{name}`",
        "",
        "Models: " + "; ".join(f"{k} = {v}" for k, v in models.items()),
        "",
        "| Metric | Value |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in total.items()],
        "",
        "| Topic | Status | Scenes | Video s | First-pass valid | Ready within 3 fixes | Degraded "
        "| TTFP s | Cost $ | $/video min |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['topic_id']} | {r['status']} | {r['scenes']} | {r['video_s']} | {r['first_pass_valid']} | "
            f"{r['ready_within_3_corrections']} | {r['degraded_rate']} | {r['first_play_s']} | "
            f"{r['cost_usd']} | "
            f"{r['cost_per_video_min']} |"
        )
    errors = [f"- {r['topic_id']}: {r['error']}" for r in rows if r.get("error")]
    if errors:
        lines += ["", "Errors:", *errors]
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> int:
    base = get_settings()
    settings = base.model_copy(update=parse_sets(args.set)) if args.set else base
    if not settings.has_openai_key:
        print("The eval suite calls OpenAI: set OPENAI_API_KEY in .env first.", file=sys.stderr)
        return 1
    topics = json.loads(settings.resolve(Path(args.topics)).read_text(encoding="utf-8"))["topics"]
    if args.only:
        wanted = {t.strip() for t in args.only.split(",")}
        topics = [t for t in topics if t["id"] in wanted]
    if args.limit:
        topics = topics[: args.limit]
    name = args.name or time.strftime("run-%Y%m%d-%H%M%S")
    out = settings.resolve(Path("var/eval")) / name
    out.mkdir(parents=True, exist_ok=True)

    pool = pool_from_settings(settings)
    await pool.start()
    try:
        llm = OpenAILLM(
            settings,
            PriceTable.load(settings.resolve(settings.animator_pricing_file)),
            ModelCatalog.load(settings.resolve(settings.animator_models_file)),
        )
        runner = GenerationRunner(FileStore(out), Pipeline(EngineClient(pool), llm, settings), settings)
        rows: list[dict[str, Any]] = []
        for t in topics:
            req = GenerationRequest(
                topic=t["topic"],
                audience=t.get("audience", "high-school student"),
                duration_min=args.duration_min or t.get("duration_min", 2),
            )
            print(f"[eval] {t['id']}: {req.topic} ({req.duration_min} min)", flush=True)
            gen = await runner.start(req)
            await runner._tasks[gen.id]
            final = await runner.get(gen.id)
            assert final is not None
            row = {"topic_id": t["id"], **generation_metrics(final)}
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
        (out / "report.md").write_text(report_md(name, settings, rows, total), encoding="utf-8")
        print(f"[eval] report: {out / 'report.md'}")
        return 0
    finally:
        await pool.close()


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
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
