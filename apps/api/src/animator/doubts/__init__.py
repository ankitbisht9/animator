"""Doubts (ADR-050): a learner raises a hand mid-lesson and the tutor answers. An isolated feature: it
reads lessons and uses the engine, the LLM client and the speech cache, and nothing depends on it."""

from animator.doubts.router import router
from animator.doubts.service import DoubtError, DoubtService

__all__ = ["DoubtError", "DoubtService", "router"]
