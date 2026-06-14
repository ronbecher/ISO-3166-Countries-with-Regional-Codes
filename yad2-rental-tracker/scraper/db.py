"""db.py — thin Supabase wrapper used by the delta engine.

The GitHub Action writes with the service-role key (SUPABASE_SERVICE_KEY).
The dashboard reads with the anon key (never used here).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from supabase import Client, create_client

log = logging.getLogger("db")

TABLE = "listings"


class DB:
    def __init__(self, url: str | None = None, key: str | None = None):
        url = url or os.environ.get("SUPABASE_URL")
        key = key or os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        self.client: Client = create_client(url, key)

    # -- reads -------------------------------------------------------------- #
    def get_all(self) -> dict[str, dict[str, Any]]:
        """Return every row keyed by id. The dataset is tiny (single-user)."""
        rows: dict[str, dict[str, Any]] = {}
        page = 0
        size = 1000
        while True:
            res = (
                self.client.table(TABLE)
                .select("*")
                .range(page * size, page * size + size - 1)
                .execute()
            )
            data = res.data or []
            for r in data:
                rows[str(r["id"])] = r
            if len(data) < size:
                break
            page += 1
        return rows

    # -- writes ------------------------------------------------------------- #
    def insert(self, record: dict[str, Any]) -> None:
        self.client.table(TABLE).insert(record).execute()

    def update(self, listing_id: str, changes: dict[str, Any]) -> None:
        self.client.table(TABLE).update(changes).eq("id", listing_id).execute()

    def mark_removed(self, listing_ids: list[str]) -> None:
        if not listing_ids:
            return
        self.client.table(TABLE).update({"status": "removed"}).in_("id", listing_ids).execute()
