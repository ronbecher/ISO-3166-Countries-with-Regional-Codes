"""sync.py — delta engine (brief §7).

For each run: take per-target fetched listings, compare against the DB, and
classify every listing as new / price_drop / relisted / still-present, then
flag removals per target. Returns the set of events worth notifying.

Importantly:
- A price DROP never overwrites the row blindly: we preserve previous_price.
- A price INCREASE updates price silently (no alert).
- Removals are computed PER TARGET and skipped when that target's fetch failed
  or returned zero results (false-removed guard).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from yad2_client import Listing

if TYPE_CHECKING:  # avoid importing the Supabase client (and its deps) at runtime
    from db import DB

log = logging.getLogger("sync")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Event:
    kind: str           # 'new' | 'price_drop' | 'relisted'
    listing: Listing
    previous_price: int | None = None
    price_diff: int | None = None


class SyncEngine:
    def __init__(self, db: "DB"):
        self.db = db

    def run(
        self,
        per_target: dict[str, list[Listing]],
        target_ok: dict[str, bool],
    ) -> list[Event]:
        existing = self.db.get_all()
        events: list[Event] = []

        # Flatten fetched listings, deduped across targets by id. The same row
        # can legitimately appear under two targets; first occurrence wins.
        fetched: dict[str, Listing] = {}
        for listings in per_target.values():
            for lst in listings:
                fetched.setdefault(lst.id, lst)

        seen_ids: set[str] = set()
        for listing in fetched.values():
            seen_ids.add(listing.id)
            ev = self._process_one(listing, existing.get(listing.id))
            if ev:
                events.append(ev)

        self._process_removals(per_target, target_ok, existing, seen_ids)
        return events

    # -- per-listing classification ---------------------------------------- #
    def _process_one(self, listing: Listing, row: dict[str, Any] | None) -> Event | None:
        rec = listing.to_record()

        if row is None:
            # NEW
            rec.update(
                status="active",
                relisted=False,
                first_seen=_now_iso(),
                last_seen=_now_iso(),
            )
            self.db.insert(rec)
            log.info("NEW %s @ %s", listing.id, listing.price)
            return Event("new", listing)

        now = _now_iso()
        old_price = row.get("price")
        new_price = listing.price

        # RELISTED: was removed, now present again.
        if row.get("status") == "removed":
            changes: dict[str, Any] = {
                "status": "active",
                "relisted": True,
                "last_seen": now,
                **{k: v for k, v in rec.items() if k != "id"},
            }
            # Preserve price-drop bookkeeping handled below.
            self.db.update(listing.id, changes)
            log.info("RELISTED %s", listing.id)
            return Event("relisted", listing)

        # PRICE DROP: do not overwrite — preserve previous_price.
        if old_price is not None and new_price is not None and new_price < old_price:
            diff = int(old_price) - int(new_price)
            self.db.update(
                listing.id,
                {
                    "previous_price": int(old_price),
                    "price": int(new_price),
                    "price_diff": diff,
                    "price_changed_at": now,
                    "last_seen": now,
                    "status": "active",
                },
            )
            log.info("PRICE DROP %s: %s -> %s (-%s)", listing.id, old_price, new_price, diff)
            return Event("price_drop", listing, previous_price=int(old_price), price_diff=diff)

        # PRICE INCREASE: update silently, no alert.
        if old_price is not None and new_price is not None and new_price > old_price:
            self.db.update(listing.id, {"price": int(new_price), "last_seen": now})
            return None

        # STILL PRESENT (no price change): refresh last_seen.
        self.db.update(listing.id, {"last_seen": now})
        return None

    # -- removals (per target, guarded) ------------------------------------ #
    def _process_removals(
        self,
        per_target: dict[str, list[Listing]],
        target_ok: dict[str, bool],
        existing: dict[str, dict[str, Any]],
        seen_ids: set[str],
    ) -> None:
        to_remove: list[str] = []
        for city, listings in per_target.items():
            # Guard: skip removals when this target failed or returned nothing.
            if not target_ok.get(city, False) or len(listings) == 0:
                log.warning("Skipping removal step for target '%s' (ok=%s, count=%d)",
                            city, target_ok.get(city), len(listings))
                continue

            for lid, row in existing.items():
                if row.get("status") != "active":
                    continue
                # Only consider rows that belong to this target's city scope and
                # were NOT seen anywhere this run.
                if str(row.get("city")) == str(city) and lid not in seen_ids:
                    to_remove.append(lid)

        if to_remove:
            log.info("Marking %d listings removed: %s", len(to_remove), to_remove)
            self.db.mark_removed(to_remove)
