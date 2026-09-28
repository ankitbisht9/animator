"""Live evaluation harness (spec Sections 19 and 20, Phase 9): run a topic set through the real
pipeline and report quality, speed and cost; run it with different model settings for a
bake-off. Needs a working OPENAI_API_KEY; never part of the offline test suite."""

from __future__ import annotations

from typing import Any

from animator.pipeline.models import Generation


def generation_metrics(gen: Generation) -> dict[str, Any]:
    scenes = gen.scenes
    n = len(scenes) or 1
    first_pass = sum(1 for s in scenes if s.attempts and s.attempts[0].ok)
    within = sum(1 for s in scenes if s.status == "ready" and len(s.attempts) <= 4)
    duration = sum(s.duration or 0.0 for s in scenes if s.playable)
    layout_warn = sum(len([w for w in s.warnings if w.get("source") == "layout"]) for s in scenes)
    ratios = [
        (s.finished_at - s.started_at) / s.duration
        for s in scenes
        if s.started_at and s.finished_at and s.duration
    ]
    return {
        "id": gen.id,
        "status": gen.status,
        "error": gen.error,
        "scenes": len(scenes),
        "video_s": round(duration, 1),
        "first_pass_valid": round(first_pass / n, 3),
        "ready_within_3_corrections": round(within / n, 3),
        "corrections_per_scene": round(sum(max(0, len(s.attempts) - 1) for s in scenes) / n, 2),
        "degraded_rate": round(sum(1 for s in scenes if s.status == "degraded") / n, 3),
        "failed_scenes": sum(1 for s in scenes if s.status == "failed"),
        "layout_warnings_per_scene": round(layout_warn / n, 2),
        "first_play_s": gen.metrics.first_play_s,
        "generation_s": gen.metrics.generation_s,
        "gen_to_play_ratio_max": round(max(ratios), 2) if ratios else None,
        "cost_usd": round(gen.metrics.cost_usd, 4),
        "cost_per_video_min": round(gen.metrics.cost_usd / (duration / 60), 4) if duration else None,
        "llm_calls": gen.metrics.llm_calls,
        "tokens": {
            "input": gen.metrics.input_tokens,
            "cached_input": gen.metrics.cached_input_tokens,
            "output": gen.metrics.output_tokens,
            "reasoning": gen.metrics.reasoning_tokens,
        },
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in rows if r["scenes"]]
    scenes = sum(r["scenes"] for r in done) or 1

    def weighted(key: str) -> float:
        return round(float(sum(r[key] * r["scenes"] for r in done)) / scenes, 3)

    video_min = sum(r["video_s"] for r in done) / 60
    cost = sum(r["cost_usd"] for r in rows)
    ttfp = sorted(r["first_play_s"] for r in done if r["first_play_s"] is not None)
    return {
        "topics": len(rows),
        "completed": sum(1 for r in rows if r["status"] == "complete"),
        "scenes": scenes if done else 0,
        "first_pass_valid": weighted("first_pass_valid") if done else None,
        "ready_within_3_corrections": weighted("ready_within_3_corrections") if done else None,
        "degraded_rate": weighted("degraded_rate") if done else None,
        "corrections_per_scene": weighted("corrections_per_scene") if done else None,
        "layout_warnings_per_scene": weighted("layout_warnings_per_scene") if done else None,
        "median_first_play_s": ttfp[len(ttfp) // 2] if ttfp else None,
        "cost_usd": round(cost, 4),
        "cost_per_video_min": round(cost / video_min, 4) if video_min else None,
    }
