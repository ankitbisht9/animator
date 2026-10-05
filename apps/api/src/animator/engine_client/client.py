"""Typed facade over the engine worker pool, using the generated protocol models.

Python never re-implements DSL rules, layout, solvers or evaluation (P8): it sends
scene JSON to the worker and gets validated results back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from animator_contracts.protocol import (
    CatalogResult,
    CompileResult,
    LiftResult,
    SampleResult,
    SceneState,
    SchemaGetResult,
    StateAtResult,
    ValidateResult,
    VersionResult,
)

from .pool import EngineWorkerPool

SceneJson = Mapping[str, Any]


def _prev(prev: SceneState | None) -> dict[str, Any] | None:
    return prev.model_dump(mode="json", exclude_none=True) if prev is not None else None


class EngineClient:
    def __init__(self, pool: EngineWorkerPool) -> None:
        self.pool = pool

    async def version(self) -> VersionResult:
        return VersionResult.model_validate(await self.pool.call("version"))

    async def schema(self, kind: Literal["canonical", "llm"] = "llm") -> dict[str, Any]:
        res = SchemaGetResult.model_validate(await self.pool.call("schema.get", {"kind": kind}))
        return res.schema_

    async def catalog(self) -> CatalogResult:
        return CatalogResult.model_validate(await self.pool.call("catalog.get"))

    async def lift(self, scene: SceneJson) -> LiftResult:
        return LiftResult.model_validate(await self.pool.call("scene.lift", {"scene": dict(scene)}))

    async def validate(self, scene: SceneJson, prev_state: SceneState | None = None) -> ValidateResult:
        res = await self.pool.call("scene.validate", {"scene": dict(scene), "prevState": _prev(prev_state)})
        return ValidateResult.model_validate(res)

    async def compile(
        self, scene: SceneJson, prev_state: SceneState | None = None, lenient: bool = False
    ) -> CompileResult:
        """Compile a scene. `lenient` also returns the compiled form of a scene that failed only its
        layout checks (the layout planner measuring a draft); `ok` then stays false."""
        params: dict[str, Any] = {"scene": dict(scene), "prevState": _prev(prev_state)}
        if lenient:
            params["lenient"] = True
        res = await self.pool.call("scene.compile", params)
        return CompileResult.model_validate(res)

    async def state_at(
        self, scene: SceneJson, t: float, prev_state: SceneState | None = None
    ) -> StateAtResult:
        res = await self.pool.call(
            "scene.stateAt", {"scene": dict(scene), "prevState": _prev(prev_state), "t": t}
        )
        return StateAtResult.model_validate(res)

    async def sample(
        self,
        scene: SceneJson,
        times: Sequence[float],
        prev_state: SceneState | None = None,
        lenient: bool = False,
    ) -> SampleResult:
        """Frames at the given times. `lenient` also samples a scene that compiled but failed its
        layout checks (the layout planner measuring a draft); `ok` then stays false."""
        params: dict[str, Any] = {"scene": dict(scene), "prevState": _prev(prev_state), "times": list(times)}
        if lenient:
            params["lenient"] = True
        res = await self.pool.call("scene.sample", params)
        return SampleResult.model_validate(res)
