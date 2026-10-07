"""Outgoing mail: SMTP when `PLATFORM_SMTP_HOST` is set; otherwise each mail is written to
`<data_dir>/outbox/` (development), and the log says it is there (never the link or token itself)."""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

from animator.platform.settings import PlatformSettings

log = logging.getLogger("animator.platform.mail")


class Mailer:
    def __init__(self, settings: PlatformSettings) -> None:
        self.settings = settings
        self.outbox: Path = settings.directory / "outbox"

    @property
    def delivers(self) -> bool:
        return bool(self.settings.smtp_host.strip())

    async def send(self, to: str, subject: str, text: str) -> None:
        """Send, or write to the outbox. A failure is logged, never raised: the action that wanted the
        mail (sign-up, reset) has already happened and says so."""
        try:
            if self.delivers:
                await asyncio.to_thread(self._smtp, to, subject, text)
            else:
                await asyncio.to_thread(self._file, to, subject, text)
        except Exception as e:
            log.warning("mail to %s not sent: %s", to, type(e).__name__)

    def _smtp(self, to: str, subject: str, text: str) -> None:
        s = self.settings
        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                password = s.smtp_password.get_secret_value() if s.smtp_password else ""
                smtp.login(s.smtp_user, password)
            smtp.send_message(msg)

    def _file(self, to: str, subject: str, text: str) -> None:
        self.outbox.mkdir(parents=True, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{abs(hash((to, subject, time.time()))) % 10**6:06d}.json"
        (self.outbox / name).write_text(
            json.dumps({"to": to, "subject": subject, "text": text, "at": time.time()}, indent=1),
            encoding="utf-8",
        )
        log.info("mail to %s written to the outbox (%s): %s", to, name, subject)
