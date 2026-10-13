"""Model trials (ADR-056): lessons an admin makes with another model, through OpenRouter, to compare
it with the normal setup. Testing only.

`TrialRouter` wraps the normal client. A lesson made with a trial model is assigned to it when it
starts (or resumes), and the trial log remembers it after a restart; every call of that lesson (scenes,
corrections, critics, the voice's script, its doubts) then goes to OpenRouter with that model, and every
other call to the normal client exactly as before. A step that sends images (the visual critic) stays
on the normal client when the trial model cannot read images, so the lesson is still made in full.

`TrialLog` remembers which lessons were trials and with which model (a small file next to the
lessons), for the admin pages.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from animator.llm.openrouter_client import OpenRouterLLM
from animator.llm.types import CallContext, ImagePart, LLMClient, LLMRequest, LLMResult

log = logging.getLogger("animator.llm.trials")


def _has_images(request: LLMRequest) -> bool:
    return any(isinstance(p, ImagePart) for m in request.messages for p in m.parts)


class TrialRouter:
    def __init__(
        self, default: LLMClient, trial: OpenRouterLLM, models: list[str], log: TrialLog | None = None
    ) -> None:
        self.default = default
        self.trial = trial
        self.models = list(models)
        self.log = log
        self._lessons: dict[str, str] = {}

    def assign(self, gen_id: str, model: str | None) -> None:
        """This lesson's calls go to `model` (a configured trial model), or to the normal client."""
        if model and model in self.models:
            self._lessons[gen_id] = model
        else:
            if model:
                log.warning(
                    "lesson %s asks for %s, which is not a trial model: normal models used", gen_id, model
                )
            self._lessons.pop(gen_id, None)

    def model_for(self, gen_id: str | None) -> str | None:
        if not gen_id:
            return None
        if gen_id in self._lessons:
            return self._lessons[gen_id]
        # A trial lesson made before a restart (its doubts): still its model, if that is still offered.
        model = self.log.models().get(gen_id) if self.log is not None else None
        return model if model in self.models else None

    async def generate(self, request: LLMRequest, ctx: CallContext | None = None) -> LLMResult:
        model = self.model_for(request.generation_id)
        if model is None:
            return await self.default.generate(request, ctx)
        if _has_images(request) and not await self.trial.sees_images(model):
            return await self.default.generate(request, ctx)
        return await self.trial.generate(dataclasses.replace(request, models=[model]), ctx)

    def __getattr__(self, name: str) -> Any:
        # Anything else (the normal client's own attributes) is the normal client's.
        return getattr(self.default, name)


class TrialLog:
    """Which lessons were made with a trial model: `{generation id: model}` in one small file."""

    RECHECK_S = 2.0  # the file is written by this process; another writer is seen within this

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._cache: tuple[float, dict[str, str]] | None = None
        self._checked = 0.0

    def record(self, gen_id: str, model: str) -> None:
        with self._lock:
            data = self._read()
            data[gen_id] = model
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(self.path)
            self._cache = None

    def models(self) -> dict[str, str]:
        with self._lock:
            now = time.monotonic()
            if self._cache is not None and now - self._checked < self.RECHECK_S:
                return self._cache[1]
            self._checked = now
            try:
                stamp = self.path.stat().st_mtime
            except FileNotFoundError:
                self._cache = (0.0, {})
                return {}
            if self._cache is None or self._cache[0] != stamp:
                self._cache = (stamp, self._read())
            return self._cache[1]

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


_LOGS: dict[Path, TrialLog] = {}


def trial_log(data_dir: Path) -> TrialLog:
    """The trial log of a data folder (one instance per folder in the process)."""
    path = data_dir / "model_trials.json"
    if path not in _LOGS:
        _LOGS[path] = TrialLog(path)
    return _LOGS[path]
