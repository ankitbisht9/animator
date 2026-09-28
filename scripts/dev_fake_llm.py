"""Dev tool: run the API with a FAKE LLM (no OpenAI calls, no cost) to work on the UI.

Every topic gets the same scripted lesson (the 12 hand-authored Archimedes scenes, in four
chapters), with delays that imitate real generation, so progressive playback, buffering and the
debug page can be exercised. It says nothing about real lesson quality.

    uv run python scripts/dev_fake_llm.py [--port 8000] [--delay 4]

Then run the web app (`pnpm --filter @animator/web dev`) with API_PORT set to the same port.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from typing import Any

import uvicorn

from animator import REPO_ROOT
from animator.api.app import create_app
from animator.llm import FakeLLM, LLMRequest, Usage

SCENES = [
    json.loads(p.read_text(encoding="utf-8"))
    for p in sorted((REPO_ROOT / "fixtures" / "scenes" / "archimedes").glob("[0-9][0-9]-*.json"))
]
CHAPTERS = [SCENES[0:3], SCENES[3:6], SCENES[6:9], SCENES[9:12]]


def brief(scene: dict[str, Any]) -> dict[str, Any]:
    beats = scene.get("narration", {}).get("beats", [])
    return {
        "title": scene["title"],
        "purpose": scene.get("educational_purpose") or scene["title"],
        "duration_s": float(scene["duration"]),
        "layout": scene.get("layout", "stage_with_caption"),
        "transition": (scene.get("transition_in") or {}).get("type", "continue"),
        "visual_plan": scene.get("educational_purpose") or "",
        "objects_new": [f"{o['id']}: {o['type']}" for o in scene.get("objects", [])],
        "objects_kept": [],
        "objects_removed": [],
        "physics": None,
        "beats": [{"narration": b["text"], "on_screen": ""} for b in beats]
        or [{"narration": "...", "on_screen": ""}],
        "engagement": "scripted",
        "checkpoint": None,
    }


def make_handler(delay: float) -> Any:
    async def handler(req: LLMRequest) -> Any:
        await asyncio.sleep(delay)
        if req.stage == "educational_planner":
            return {
                "title": "Archimedes' principle (scripted demo)",
                "subtitle": "FAKE LLM: every topic gets this lesson",
                "subject": "physics",
                "audience": "high-school student",
                "target_duration_s": sum(float(s["duration"]) for s in SCENES),
                "hook": "Why does a steel ship float?",
                "learning_objectives": ["Explain buoyancy"],
                "prerequisites": [],
                "key_terms": [],
                "misconceptions": [],
                "chapters": [
                    {
                        "id": f"c{i + 1}",
                        "title": ch[0]["title"],
                        "goal": "demo",
                        "key_ideas": [],
                        "target_duration_s": sum(float(s["duration"]) for s in ch),
                        "visual_approach": "",
                    }
                    for i, ch in enumerate(CHAPTERS)
                ],
                "summary": [],
            }
        if req.stage == "storyboard":
            text = req.messages[0].parts[0].text  # type: ignore[union-attr]
            k = next(i for i in range(len(CHAPTERS)) if f"chapter now: c{i + 1} " in text)
            return {"chapter_id": f"c{k + 1}", "scenes": [brief(s) for s in CHAPTERS[k]]}
        if req.stage in ("critic", "visual_critic"):
            return {"verdict": "pass", "issues": []}
        index = int(req.label.split()[1]) - 1
        return copy.deepcopy(SCENES[index])

    return handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--delay", type=float, default=4.0, help="seconds per fake LLM call")
    args = ap.parse_args()
    print("*** FAKE LLM: no OpenAI calls; every topic gets the scripted Archimedes lesson ***", flush=True)
    app = create_app(llm=FakeLLM(make_handler(args.delay), usage=Usage(0, 0, 0)))
    uvicorn.run(app, host="127.0.0.1", port=args.port, loop="animator.api.loop:loop_factory")


if __name__ == "__main__":
    main()
