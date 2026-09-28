"""Logging setup with secret redaction (spec Sections 17 and 18).

Anything shaped like an API key is masked before it reaches a handler, so a key can never end
up in logs even if an exception message quotes it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time

_SECRET = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8})[A-Za-z0-9_\-]{8,}")


def redact(text: str) -> str:
    return _SECRET.sub(r"\1…[redacted]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = redact(message)
        if clean != message:
            record.msg = clean
            record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": round(time.time(), 3),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        if record.exc_info:
            data["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False)


def setup_logging(level: str = "INFO", fmt: str = "console") -> None:
    root = logging.getLogger("animator")
    if getattr(root, "_animator_configured", False):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(
        JsonFormatter()
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
    root._animator_configured = True  # type: ignore[attr-defined]
