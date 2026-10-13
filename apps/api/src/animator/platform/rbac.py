"""Roles and permissions (RBAC). Code asks for a permission, never for a role, so what a role may
do is decided here, in one table.

- learner: watches, creates lessons within a daily quota, asks doubts, shares privately (a link).
- educator: everything a learner can, with larger quotas, and publishes lessons to Explore.
- admin: everything, including people, every lesson, support, the audit log, credits and cost,
  ratings and what Explore lists, and the saved drawings cost-saver lessons use.

A new role (a curator, a finance viewer) is one more line in ROLE_PERMS: the routes already ask for
the permission they need.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    LEARNER = "learner"
    EDUCATOR = "educator"
    ADMIN = "admin"


class Perm(StrEnum):
    LESSON_CREATE = "lesson:create"
    LESSON_PUBLISH = "lesson:publish"  # make a lesson public, in Explore
    LESSON_ANY = "lesson:any"  # see, change and delete anyone's lesson
    LESSON_FEATURE = "lesson:feature"
    DOUBT_ASK = "doubt:ask"
    SUPPORT_MANAGE = "support:manage"
    USER_MANAGE = "user:manage"
    AUDIT_READ = "audit:read"
    ANALYTICS_READ = "analytics:read"
    DEBUG = "debug"  # the pipeline's raw API, call logs, the DSL playground
    USAGE_READ = "usage:read"  # everyone's credits and cost (ADR-053)
    CREDITS_MANAGE = "credits:manage"  # a person's monthly credits, credits given back
    RATINGS_READ = "ratings:read"  # every lesson's ratings and comments
    EXPLORE_CURATE = "explore:curate"  # add any lesson to Explore, or keep one out
    REVIEW_MANAGE = "review:manage"  # flag a lesson for the team to improve
    OBJECTS_READ = "objects:read"  # the saved drawings, the warehouse and its harvest (ADR-055)
    OBJECTS_CURATE = "objects:curate"  # approve, reject, edit or import a saved drawing
    MODELS_TRIAL = "models:trial"  # make a lesson with a trial model, to compare models (ADR-056)


ROLE_PERMS: dict[Role, frozenset[Perm]] = {
    Role.LEARNER: frozenset({Perm.LESSON_CREATE, Perm.DOUBT_ASK}),
    Role.EDUCATOR: frozenset({Perm.LESSON_CREATE, Perm.DOUBT_ASK, Perm.LESSON_PUBLISH}),
    Role.ADMIN: frozenset(Perm),
}


def perms_of(role: str) -> frozenset[Perm]:
    try:
        return ROLE_PERMS[Role(role)]
    except ValueError:
        return frozenset()


def has(role: str, perm: Perm) -> bool:
    return perm in perms_of(role)
