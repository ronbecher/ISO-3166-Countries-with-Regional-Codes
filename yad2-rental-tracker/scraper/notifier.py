"""notifier.py — Telegram notifications (brief §8).

Sends one batched message per run covering new listings, price drops, and
relisted listings. Each hit includes title, location, price (+ Δ for drops),
rooms, size, parking, a direct Yad2 link and a thumbnail.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

import httpx

log = logging.getLogger("notifier")

TELEGRAM_API = "https://api.telegram.org"


def _fmt_price(p: int | None) -> str:
    return f"{p:,}₪" if isinstance(p, int) else "—"


class Notifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            log.warning("Telegram not configured (missing token/chat id) — notifications disabled")

    def notify(self, events: list) -> None:
        """events: list[sync.Event]. Batched into a single message where reasonable."""
        if not events:
            return
        if not self.enabled:
            log.info("Would notify %d events (Telegram disabled)", len(events))
            return

        blocks = [self._format_event(ev) for ev in events]
        message = "\n\n".join(blocks)

        # Telegram hard-caps messages at 4096 chars; split conservatively.
        for chunk in self._split(message, 3800):
            self._send_message(chunk)

        # Send the first thumbnail of the batch as a photo for a richer ping.
        first_img = next((ev.listing.image_url for ev in events if ev.listing.image_url), None)
        if first_img:
            self._send_photo(first_img, caption=f"{len(events)} new hit(s) — see details above")

    # -- formatting --------------------------------------------------------- #
    @staticmethod
    def _format_event(ev) -> str:
        l = ev.listing
        header = {
            "new": "🆕 *New listing*",
            "price_drop": "📉 *Price drop*",
            "relisted": "🔁 *Relisted*",
        }.get(ev.kind, ev.kind)

        loc = " · ".join(x for x in (l.city, l.neighborhood) if x)
        lines = [header]
        if l.title:
            lines.append(f"*{_escape(l.title)}*")
        if loc:
            lines.append(_escape(loc))

        if ev.kind == "price_drop" and ev.previous_price:
            lines.append(
                f"💰 {_fmt_price(l.price)}  (was {_fmt_price(ev.previous_price)}, "
                f"▼ {_fmt_price(ev.price_diff)})"
            )
        else:
            lines.append(f"💰 {_fmt_price(l.price)}")

        meta = []
        if l.rooms is not None:
            meta.append(f"{_num(l.rooms)} rooms")
        if l.size_sqm is not None:
            meta.append(f"{_num(l.size_sqm)} sqm")
        if l.property_type:
            meta.append(_escape(str(l.property_type)))
        meta.append("🅿️ parking" if l.has_parking else "no parking")
        lines.append(" · ".join(meta))

        if l.url:
            lines.append(f"[View on Yad2]({l.url})")
        return "\n".join(lines)

    # -- transport ---------------------------------------------------------- #
    def _send_message(self, text: str) -> None:
        url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
        try:
            r = httpx.post(url, json=payload, timeout=20)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("Telegram sendMessage failed: %s", exc)

    def _send_photo(self, photo_url: str, caption: str = "") -> None:
        url = f"{TELEGRAM_API}/bot{self.token}/sendPhoto"
        try:
            r = httpx.post(
                url,
                json={"chat_id": self.chat_id, "photo": photo_url, "caption": caption},
                timeout=20,
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Telegram sendPhoto failed (non-fatal): %s", exc)

    @staticmethod
    def _split(text: str, limit: int) -> Iterable[str]:
        if len(text) <= limit:
            yield text
            return
        buf = ""
        for block in text.split("\n\n"):
            if len(buf) + len(block) + 2 > limit and buf:
                yield buf
                buf = ""
            buf += (("\n\n" if buf else "") + block)
        if buf:
            yield buf


def _num(v) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else str(f)


def _escape(text: str) -> str:
    # Minimal Markdown(v1) escaping for user-derived strings.
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text
