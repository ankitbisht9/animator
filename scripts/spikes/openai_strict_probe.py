"""Spike (c), live half: does OpenAI accept the LLM-facing scene schema in strict mode?

Sends one small Responses API request with `contracts/dsl.llm.schema.json` as a
strict JSON schema and reports: accepted/rejected, refusal, truncation, usage.
Manual only (never in CI). Skips cleanly when OPENAI_API_KEY is not set.

    uv run python scripts/spikes/openai_strict_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from openai import AsyncOpenAI, BadRequestError

from animator.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "dsl.llm.schema.json"


async def main() -> int:
    settings = get_settings()
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not key:
        print("SKIPPED: OPENAI_API_KEY is not set (fill it in .env to run the live probe).")
        return 0
    model = settings.animator_model_animation_planner
    if not model:
        print("ERROR: ANIMATOR_MODEL_ANIMATION_PLANNER is not configured.")
        return 2
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    schema.pop("$schema", None)
    client = AsyncOpenAI(
        api_key=key, base_url=settings.openai_base_url or None, timeout=settings.openai_timeout_s
    )
    try:
        resp = await client.responses.create(
            model=model,
            reasoning={"effort": "low"},
            max_output_tokens=8000,
            store=settings.openai_store,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You write animation scenes as JSON matching the schema. "
                        "Use null for any field you want to leave at its default."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "A 6 second scene: a blue circle appears in the center and a caption says hello."
                    ),
                },
            ],
            text={"format": {"type": "json_schema", "name": "scene", "schema": schema, "strict": True}},
        )
    except BadRequestError as exc:
        print(f"REJECTED by the API: {exc}")
        return 1
    refusal = next(
        (c for item in resp.output for c in getattr(item, "content", None) or [] if c.type == "refusal"), None
    )
    report = {
        "model": model,
        "status": resp.status,
        "incomplete": resp.incomplete_details.reason if resp.incomplete_details else None,
        "refusal": getattr(refusal, "refusal", None),
        "usage": resp.usage.model_dump() if resp.usage else None,
        "parsed_ok": False,
    }
    if resp.status == "completed" and not refusal:
        try:
            json.loads(resp.output_text)
            report["parsed_ok"] = True
        except json.JSONDecodeError as exc:
            report["parse_error"] = str(exc)
    print(json.dumps(report, indent=2))
    return 0 if report["parsed_ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
