"""HTTP API with a scripted LLM and the real engine worker: create a lesson, follow its live
progress (SSE) to the end, inspect it, and check the error paths."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from animator.api.app import create_app
from animator.jobs.store import MemoryStore
from animator.llm import FakeLLM, LLMAuthError, LLMRequest
from tests.test_pipeline import SCENES, Script, settings


def app_with(handler: Any, **kw: Any) -> TestClient:
    s = settings(openai_api_key="sk-proj-secret-value-that-must-never-leak", **kw)
    app = create_app(settings=s, llm=FakeLLM(handler), store=MemoryStore(), resume_on_start=False)
    return TestClient(app)


def read_events(client: TestClient, gen_id: str) -> list[dict[str, Any]]:
    events = []
    with client.stream("GET", f"/api/generations/{gen_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_the_last_event_counts_drafts_that_finish_after_the_lesson_is_ready(worker_script: Path) -> None:
    seen: dict[int, int] = {}

    async def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        seen[i] = seen.get(i, 0) + 1
        if seen[i] == 2:
            await asyncio.sleep(0.5)  # the losing draft: still running when the lesson is ready
        return copy.deepcopy(SCENES[i])

    with app_with(Script(scene), animator_scene_drafts=2) as client:
        gen_id = client.post("/api/generations", json={"topic": "Archimedes' principle"}).json()["id"]
        events = read_events(client, gen_id)
        calls = client.get(f"/api/generations/{gen_id}/calls").json()
        assert events[-1].get("final") is True and events[-1]["generation"]["status"] == "complete"
        assert events[-1]["generation"]["metrics"]["llm_calls"] == len(calls) == 3 + 8


def test_create_follow_and_inspect_a_lesson(worker_script: Path) -> None:
    with app_with(Script()) as client:
        cfg = client.get("/api/config").json()
        assert cfg["openai_key_configured"] is True
        assert cfg["stages"]["animation_planner"]["models"] == ["m-scene"]
        assert "secret" not in json.dumps(cfg)

        res = client.post("/api/generations", json={"topic": "Archimedes' principle", "duration_min": 3})
        assert res.status_code == 201
        gen_id = res.json()["id"]

        events = read_events(client, gen_id)
        assert events[0]["type"] == "snapshot"
        assert events[-1].get("final") is True  # the stream ends once the run is fully over
        final = events[-1]["generation"]
        assert final["status"] == "complete" and len(final["scenes"]) == 4
        assert final["metrics"]["llm_calls"] == 7  # every call counted by the last event
        scenes = {s["index"]: s["dsl"] for s in events[0]["scenes"]}
        scenes.update({e["index"]: e["dsl"] for e in events if e["type"] == "scene"})
        assert sorted(scenes) == [0, 1, 2, 3] and all(
            d and d["dsl_version"] == "1.0" for d in scenes.values()
        )

        full = client.get(f"/api/generations/{gen_id}").json()
        assert full["status"] == "complete" and full["plan"]["title"] == "Why things float"
        assert all(s["status"] == "ready" for s in full["scenes"])
        calls = client.get(f"/api/generations/{gen_id}/calls").json()
        assert len(calls) == 7 and {c["stage"] for c in calls} >= {"educational_planner", "storyboard"}
        assert "secret" not in json.dumps(full) + json.dumps(calls)
        assert gen_id in [g["id"] for g in client.get("/api/generations").json()]


def test_error_paths(worker_script: Path) -> None:
    def auth(req: LLMRequest) -> Any:
        return LLMAuthError("OpenAI rejected the API key: deactivated")

    with app_with(auth) as client:
        assert client.post("/api/generations", json={"topic": ""}).status_code == 422
        assert (
            client.post("/api/generations", json={"topic": "x" * 20, "duration_min": 99}).status_code == 422
        )
        assert client.get("/api/generations/g_000000000000").status_code == 404
        assert client.get("/api/generations/../../etc").status_code == 404

        gen_id = client.post("/api/generations", json={"topic": "Bernoulli's principle"}).json()["id"]
        final = read_events(client, gen_id)[-1]["generation"]
        assert final["status"] == "failed" and "deactivated" in final["error"]
        assert client.post(f"/api/generations/{gen_id}/export").status_code == 409
        assert client.post(f"/api/generations/{gen_id}/cancel").json()["status"] == "failed"


@pytest.mark.e2e
def test_a_generated_lesson_exports_to_mp4(worker_script: Path, tmp_path: Path) -> None:
    with app_with(Script(), export_output_dir=tmp_path) as client:
        gen_id = client.post("/api/generations", json={"topic": "Archimedes' principle"}).json()["id"]
        assert read_events(client, gen_id)[-1]["generation"]["status"] == "complete"
        res = client.post(f"/api/generations/{gen_id}/export", json={"width": 320, "height": 180, "fps": 5})
        assert res.status_code == 202
        for _ in range(600):
            job = client.get(f"/api/generations/{gen_id}/export").json()
            if job["state"] != "running":
                break
            time.sleep(0.5)
        assert job["state"] == "done", job
        video = client.get(f"/api/generations/{gen_id}/export/video")
        assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
        assert video.content[4:8] == b"ftyp" and len(video.content) > 10_000
        assert (tmp_path / gen_id / f"{gen_id}.mp4").exists()
