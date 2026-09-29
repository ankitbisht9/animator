## Scene continuity

Scenes continue from each other: objects stay on stage until removed. Reuse the id of anything already on stage (`objects_kept`), list objects that leave in `objects_removed`, and use `transition` "continue" to keep the stage or "fade"/"cut" to start from a clean one. The scenes already planned, and the objects on stage after them, are listed in the user message: do not reintroduce objects that are already there.
