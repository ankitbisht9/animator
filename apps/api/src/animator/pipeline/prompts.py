"""Prompt packs: versioned instruction files plus the engine's own reference material.

Static content (rules, DSL reference, component catalog, exemplars) forms the `instructions`,
identical for every scene of every generation so the provider can cache it; per-call content
(the scene brief, the previous scene's state) goes in the user message (spec Section 4).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from animator import REPO_ROOT
from animator_contracts.protocol import CatalogResult

PROMPTS_DIR = Path(__file__).parent / "prompts"
# The scene and critic prompt versions of the fast pipeline (ADR-033 .. ADR-042).
FAST_ANIMATION = 5
FAST_CRITIC = 1
EXEMPLARS = [
    (
        "archimedes",
        "02-above-water.json",
        "An object above the water: characters, a speech bubble, a live readout, a checkpoint",
    ),
    (
        "archimedes",
        "05-level-rises.json",
        "The water level rises: a solver, bound readouts and dimension label, a camera move",
    ),
    (
        "archimedes",
        "10-equation.json",
        "The equation: stage_with_equation, per-term highlights and labels, an arrow to the diagram",
    ),
]


@dataclass(frozen=True)
class Prompt:
    version: str
    text: str


def load_prompt(name: str, version: int | None = None) -> Prompt:
    """A prompt pack by version; the newest one when no version is given."""
    if version is None:
        found = [int(p.stem.rsplit(".v", 1)[1]) for p in PROMPTS_DIR.glob(f"{name}.v*.md")]
        if not found:
            raise FileNotFoundError(f"no prompt pack '{name}' in {PROMPTS_DIR}")
        version = max(found)
    path = PROMPTS_DIR / f"{name}.v{version}.md"
    return Prompt(f"{name}.v{version}", path.read_text(encoding="utf-8"))


def compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def slug(text: str, limit: int = 28) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    s = s[:limit].rstrip("_")
    return s or "scene"


class PromptPack:
    """Built once per process from the worker's catalog and the generated docs."""

    def __init__(
        self,
        catalog: CatalogResult,
        dsl_doc: str,
        components_doc: str,
        exemplars: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self.catalog = catalog
        self.dsl_doc = dsl_doc
        self.components_doc = components_doc
        self.exemplars = exemplars
        self.planner = load_prompt("planner")
        self.storyboard = load_prompt("storyboard")
        self.outline = load_prompt("outline")
        # The teaching pipeline (ADR-044) uses the newest scene and critic prompts; the fast pipeline
        # keeps the ones it was tuned with, so ANIMATOR_PIPELINE=fast behaves exactly as before.
        self.animation = load_prompt("animation")
        self.critic = load_prompt("critic")
        self.animation_fast = load_prompt("animation", FAST_ANIMATION)
        self.critic_fast = load_prompt("critic", FAST_CRITIC)
        self.teacher = load_prompt("teacher")
        self.review = load_prompt("review")
        self.director = load_prompt("director")
        # The planned layout flow (ADR-047): its own scene and critic prompts, by name, so the classic
        # flow's prompts (`animation`, `critic`) are never affected.
        self.animation_planned = load_prompt("animation-planned")
        self.critic_planned = load_prompt("critic-planned")
        # How scenes relate (parallel: each on a clean stage; sequential: each continues the last).
        self.continuity = {mode: load_prompt(f"continuity-{mode}") for mode in ("parallel", "sequential")}
        self._animation_text: dict[str, str] = {}

    @classmethod
    def from_repo(cls, catalog: CatalogResult, root: Path = REPO_ROOT) -> PromptPack:
        exemplars = []
        for lesson, file, note in EXEMPLARS:
            path = root / "fixtures" / "scenes" / lesson / file
            if path.exists():
                exemplars.append((note, json.loads(path.read_text(encoding="utf-8"))))
        return cls(
            catalog,
            (root / "docs" / "DSL.md").read_text(encoding="utf-8"),
            (root / "docs" / "COMPONENTS.md").read_text(encoding="utf-8"),
            exemplars,
        )

    # ------------------------------------------------------------------ reference blocks

    def capabilities(self) -> str:
        by_cat: dict[str, list[str]] = {}
        for c in self.catalog.components:
            first = c.description.split(". ")[0].rstrip(".")
            by_cat.setdefault(c.category, []).append(f"`{c.type}` ({first})")
        lines = [f"- {cat}: " + "; ".join(items) for cat, items in sorted(by_cat.items())]
        lines.append(
            "- physics solvers (real numbers and motion): " + ", ".join(s.type for s in self.catalog.solvers)
        )
        return "\n".join(lines)

    def layouts(self) -> str:
        return "\n".join(
            f"- `{lay.id}`: " + ", ".join(f"`{s}`" for s in lay.slots) for lay in self.catalog.layouts
        )

    def solvers(self) -> str:
        return "\n".join(
            f"- `{s.type}`: "
            + ", ".join(o.name for o in s.outputs)
            + (" (plus per-section outputs)" if s.dynamicOutputs else "")
            for s in self.catalog.solvers
        )

    # ------------------------------------------------------------------ instructions

    def planner_instructions(self) -> str:
        return self.planner.text.replace("{capabilities}", self.capabilities())

    def storyboard_instructions(self, mode: str = "parallel") -> str:
        return self.storyboard.text.replace("{capabilities}", self.capabilities()).replace(
            "{continuity}", self.continuity[mode].text.strip()
        )

    def outline_instructions(self, mode: str = "parallel") -> str:
        return self.outline.text.replace("{capabilities}", self.capabilities()).replace(
            "{continuity}", self.continuity[mode].text.strip()
        )

    def animation_prompt(self, fast: bool = False) -> Prompt:
        return self.animation_fast if fast else self.animation

    def animation_instructions(self, fast: bool = False) -> str:
        prompt = self.animation_prompt(fast)
        if prompt.version not in self._animation_text:
            head = prompt.text.replace("{layouts}", self.layouts()).replace("{solvers}", self.solvers())
            examples = "\n\n".join(
                f"### Example: {note}\n\n```json\n{compact(scene)}\n```" for note, scene in self.exemplars
            )
            self._animation_text[prompt.version] = "\n\n".join(
                [
                    head,
                    "# Scene DSL reference\n\n" + self.dsl_doc,
                    "# Component catalog\n\n" + self.components_doc,
                    "# Hand-authored example scenes\n\nThese are written in compact form: "
                    "omitted fields are null (default). "
                    "Your output must still list every property the schema requires.\n\n" + examples,
                ]
            )
        return self._animation_text[prompt.version]

    def critic_prompt(self, fast: bool = False) -> Prompt:
        return self.critic_fast if fast else self.critic

    def critic_instructions(self, fast: bool = False) -> str:
        return self.critic_prompt(fast).text

    def planned_animation_instructions(self) -> str:
        """The planned flow's scene instructions, with the same reference material as the classic
        ones (DSL reference, component catalog, example scenes)."""
        return self._with_reference(self.animation_planned)

    def _with_reference(self, prompt: Prompt) -> str:
        if prompt.version not in self._animation_text:
            head = prompt.text.replace("{layouts}", self.layouts()).replace("{solvers}", self.solvers())
            examples = "\n\n".join(
                f"### Example: {note}\n\n```json\n{compact(scene)}\n```" for note, scene in self.exemplars
            )
            self._animation_text[prompt.version] = "\n\n".join(
                [
                    head,
                    "# Scene DSL reference\n\n" + self.dsl_doc,
                    "# Component catalog\n\n" + self.components_doc,
                    "# Hand-authored example scenes\n\nThese are written in compact form: "
                    "omitted fields are null (default). They show the scene format only: a planned scene "
                    "also has its `stage` plan. Your output must still list every property the schema "
                    "requires.\n\n" + examples,
                ]
            )
        return self._animation_text[prompt.version]

    def teacher_instructions(self) -> str:
        return self.teacher.text.replace("{capabilities}", self.capabilities())

    def review_instructions(self) -> str:
        return self.review.text

    def director_instructions(self) -> str:
        return self.director.text.replace("{capabilities}", self.capabilities())
