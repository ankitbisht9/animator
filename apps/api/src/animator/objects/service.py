"""The object library as the pipeline uses it (ADR-055): which saved drawings a scene may use, and
the record of the ones it placed."""

from __future__ import annotations

import asyncio
import logging

from animator.config import Settings
from animator.objects.matching import Offer, SceneText, offers_for
from animator.objects.store import ObjectStore
from animator.pipeline.models import Generation, SceneRecord

log = logging.getLogger("animator.objects")


def scene_text(gen: Generation, rec: SceneRecord) -> SceneText:
    """What a scene's brief says: its new objects one by one, the rest of it, and the lesson's topic
    and look (for context words only)."""
    b = rec.brief
    body = [b.title, b.purpose, b.visual_plan, b.engagement]
    for beat in b.beats:
        body += [beat.narration, beat.on_screen]
    lesson = [gen.request.topic]
    if gen.plan is not None:
        lesson.append(gen.plan.title)
    look = (gen.teaching or {}).get("look")
    if isinstance(look, str):
        lesson.append(look)
    return SceneText(objects=list(b.objects_new), body=[t for t in body if t], lesson=lesson)


class ObjectLibrary:
    def __init__(self, settings: Settings, store: ObjectStore | None = None, record: bool = True) -> None:
        self.settings = settings
        self.store = store or ObjectStore(settings.resolve(settings.animator_objects_dir))
        # Uses are statistics of real lessons: evaluation runs read the library but record nothing.
        self.record = record

    def offers(self, gen: Generation, rec: SceneRecord) -> list[Offer]:
        """The approved drawings a scene's brief names exactly, best first. Never fails a lesson: a
        broken library offers nothing and the scene is drawn as in a planned lesson."""
        try:
            return offers_for(
                scene_text(gen, rec), self.store.approved(), self.settings.animator_objects_max_offers
            )
        except Exception:
            log.exception("saved objects: no offers for scene %s", rec.id)
            return []

    def offers_text(self, lines: list[str], lesson: list[str], limit: int = 8) -> str:
        """The approved drawings a lesson's script names, one line per thing with its views (for its
        visual director). Empty when there are none (or the library cannot be read)."""
        try:
            offers = offers_for(
                SceneText(objects=[], body=lines, lesson=lesson), self.store.approved(), limit
            )
        except Exception:
            log.exception("saved objects: no list for the lesson's director")
            return ""
        by_thing: dict[str, list[str]] = {}
        for o in offers:
            view = o.obj.view or o.obj.title
            by_thing.setdefault(o.obj.concept, []).append(view)
        return "\n".join(f"- {thing}: {'; '.join(views)}" for thing, views in by_thing.items())

    async def record_uses(self, gen_id: str, scene_id: str, refs: list[str]) -> None:
        if not self.record:
            return
        try:
            await asyncio.to_thread(self.store.record_uses, gen_id, scene_id, refs)
        except OSError:
            log.warning("saved objects: could not record the uses of scene %s", scene_id)
