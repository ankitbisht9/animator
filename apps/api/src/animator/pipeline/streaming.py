"""Use a JSON reply while it is still streaming in: the lesson outline's plan and each scene brief
are handed over as soon as their closing bracket arrives, so scene generation starts seconds before
the outline is finished (ADR-033)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("animator.streaming")


class JsonScanner:
    """Incremental scanner for a JSON object reply. Reports each complete top-level value
    (`on_value(key, value)`) and each complete element of a top-level array
    (`on_item(key, index, value)`), in order, while the text is still arriving."""

    def __init__(
        self,
        on_value: Callable[[str, Any], None],
        on_item: Callable[[str, int, Any], None],
    ) -> None:
        self.on_value = on_value
        self.on_item = on_item
        self.reset()

    def reset(self) -> None:
        self.buf: list[str] = []
        self.pos = 0
        self.stack: list[str] = []
        self.in_str = False
        self.esc = False
        self.str_start = 0
        self.last_str = ""
        self.key = ""
        self.value_start = 0
        self.item_start = 0
        self.items = 0

    def feed(self, text: str) -> None:
        for ch in text:
            self._char(ch)

    def _char(self, ch: str) -> None:
        i = self.pos
        self.buf.append(ch)
        self.pos += 1
        if self.in_str:
            if self.esc:
                self.esc = False
            elif ch == "\\":
                self.esc = True
            elif ch == '"':
                self.in_str = False
                if len(self.stack) == 1:
                    self.last_str = "".join(self.buf[self.str_start + 1 : i])
            return
        if ch == '"':
            self.in_str = True
            self.str_start = i
        elif ch == ":" and len(self.stack) == 1:
            self.key = self.last_str
            self.items = 0
        elif ch in "{[":
            self.stack.append(ch)
            if len(self.stack) == 2:
                self.value_start = i
            elif len(self.stack) == 3 and self.stack[1] == "[":
                self.item_start = i
        elif ch in "}]":
            depth = len(self.stack)
            if not depth:
                return
            self.stack.pop()
            if depth == 2:
                self._emit(lambda v: self.on_value(self.key, v), self.value_start, i)
            elif depth == 3 and self.stack[1] == "[":
                index = self.items
                self.items += 1
                self._emit(lambda v: self.on_item(self.key, index, v), self.item_start, i)

    def _emit(self, deliver: Callable[[Any], None], start: int, end: int) -> None:
        try:
            value = json.loads("".join(self.buf[start : end + 1]))
        except json.JSONDecodeError:
            return  # not valid on its own: the complete reply is still checked at the end
        try:
            deliver(value)
        except Exception:  # a consumer problem must not break the stream
            log.exception("streamed value handler failed")
