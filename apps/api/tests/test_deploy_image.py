"""The Render image (deploy/render/Dockerfile, ADR-054) holds every file the server reads at run time.

The server reads some files from the repository's folders rather than from its package (the scene
writer's references and example scenes, the model and price lists, the web build, the engine worker).
A file the image leaves out only shows on the live service, when a lesson stops before it starts (the
first deploy lacked docs/DSL.md). These tests read the Dockerfile's COPY lines and check each such file
is copied to the same place under /app, and that the prompts build from exactly what is copied.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from animator import REPO_ROOT
from animator.engine_client import EngineClient, EngineWorkerPool
from animator.pipeline.prompts import EXEMPLARS, PromptPack

DOCKERFILE = REPO_ROOT / "deploy" / "render" / "Dockerfile"

# Read at run time from the repository's folders (relative to the repository = /app in the image).
RUNTIME_FILES = [
    "docs/DSL.md",
    "docs/COMPONENTS.md",
    *(f"fixtures/scenes/{lesson}/{file}" for lesson, file, _ in EXEMPLARS),
    "config/models.json",
    "config/pricing.json",
    "apps/api/src/animator/pipeline/prompts",
    "apps/api/src/animator/objects/library",
]
# Built in the image's first stage and copied from it.
BUILT_FILES = ["apps/web/dist", "packages/engine-worker/dist/worker.cjs"]


def _runtime_copies() -> tuple[list[tuple[str, str]], list[str]]:
    """(source in the repository, place in the image) for the runtime stage's COPY lines, and the
    places filled from the build stages (COPY --from)."""
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("FROM") and " AS runtime" in line)
    pairs: list[tuple[str, str]] = []
    built: list[str] = []
    for line in lines[start:]:
        if not line.startswith("COPY "):
            continue
        words = line.split()[1:]
        from_stage = any(w.startswith("--from=") for w in words)
        args = [w for w in words if not w.startswith("--")]
        *sources, dest = args
        if from_stage:
            built.append(dest.rstrip("/"))
            continue
        for src in sources:
            place = dest + Path(src).name if dest.endswith("/") or len(sources) > 1 else dest
            pairs.append((src.rstrip("/"), place.rstrip("/")))
    return pairs, built


def _place_in_image(rel: str, pairs: list[tuple[str, str]]) -> str | None:
    for src, dest in pairs:
        if rel == src or rel.startswith(src + "/"):
            return dest + rel[len(src) :]
    return None


def test_the_render_image_has_every_file_the_server_reads() -> None:
    pairs, built = _runtime_copies()
    for rel in RUNTIME_FILES:
        assert (REPO_ROOT / rel).exists(), f"{rel} is not in the repository"
        assert _place_in_image(rel, pairs) == rel, f"{rel} is not copied into the image (at /app/{rel})"
    for rel in BUILT_FILES:
        assert rel in built, f"{rel} is not copied from the build stage"


async def test_the_prompts_build_from_the_files_the_image_holds(
    tmp_path: Path, pool: EngineWorkerPool
) -> None:
    pairs, _ = _runtime_copies()
    for src, dest in pairs:
        if not src.startswith(("docs", "fixtures")):
            continue
        source, target = REPO_ROOT / src, tmp_path / dest
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    pack = PromptPack.from_repo(await EngineClient(pool).catalog(), root=tmp_path)
    assert len(pack.exemplars) == len(EXEMPLARS)
