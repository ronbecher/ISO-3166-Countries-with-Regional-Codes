"""Offline smoke tests — no network. Exercise normalization + the delta engine
with an in-memory fake DB, so the core logic can be verified without Yad2,
Supabase, or Telegram.

Run: python scraper/test_smoke.py   (or: pytest scraper/test_smoke.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync import SyncEngine  # noqa: E402
from yad2_client import Yad2Client, Listing  # noqa: E402


# --------------------------------------------------------------------------- #
# Fake DB
# --------------------------------------------------------------------------- #
class FakeDB:
    def __init__(self, rows=None):
        self.rows = {r["id"]: dict(r) for r in (rows or [])}
        self.inserted = []
        self.updated = []
        self.removed = []

    def get_all(self):
        return {k: dict(v) for k, v in self.rows.items()}

    def insert(self, record):
        self.inserted.append(record)
        self.rows[record["id"]] = dict(record)

    def update(self, listing_id, changes):
        self.updated.append((listing_id, changes))
        self.rows.setdefault(listing_id, {"id": listing_id}).update(changes)

    def mark_removed(self, ids):
        self.removed.extend(ids)
        for i in ids:
            self.rows[i]["status"] = "removed"


def L(id, price, city="הרצליה", **kw):
    return Listing(id=id, price=price, city=city, _target_city=city, **kw)


def test_new_listing():
    db = FakeDB()
    events = SyncEngine(db).run({"הרצליה": [L("1", 18000)]}, {"הרצליה": True})
    assert [e.kind for e in events] == ["new"]
    assert db.inserted and db.inserted[0]["status"] == "active"
    print("✓ new listing inserted + notified")


def test_price_drop_preserves_previous():
    db = FakeDB([{"id": "1", "price": 20000, "status": "active", "city": "הרצליה"}])
    events = SyncEngine(db).run({"הרצליה": [L("1", 17000)]}, {"הרצליה": True})
    assert [e.kind for e in events] == ["price_drop"]
    _, changes = db.updated[0]
    assert changes["previous_price"] == 20000
    assert changes["price"] == 17000
    assert changes["price_diff"] == 3000
    print("✓ price drop preserves previous_price + computes diff")


def test_price_increase_is_silent():
    db = FakeDB([{"id": "1", "price": 15000, "status": "active", "city": "הרצליה"}])
    events = SyncEngine(db).run({"הרצליה": [L("1", 16000)]}, {"הרצליה": True})
    assert events == []
    _, changes = db.updated[0]
    assert changes == {"price": 16000, "last_seen": changes["last_seen"]}
    print("✓ price increase updates silently, no alert")


def test_relisted():
    db = FakeDB([{"id": "1", "price": 15000, "status": "removed", "city": "הרצליה"}])
    events = SyncEngine(db).run({"הרצליה": [L("1", 15000)]}, {"הרצליה": True})
    assert [e.kind for e in events] == ["relisted"]
    assert db.rows["1"]["status"] == "active"
    assert db.rows["1"]["relisted"] is True
    print("✓ relisted flips status back to active + flags relisted")


def test_removal_marks_absent():
    db = FakeDB([
        {"id": "1", "price": 15000, "status": "active", "city": "הרצליה"},
        {"id": "2", "price": 16000, "status": "active", "city": "הרצליה"},
    ])
    # only #1 returns this run
    SyncEngine(db).run({"הרצליה": [L("1", 15000)]}, {"הרצליה": True})
    assert db.removed == ["2"]
    print("✓ absent active listing marked removed")


def test_removal_guard_on_failed_fetch():
    db = FakeDB([{"id": "1", "price": 15000, "status": "active", "city": "הרצליה"}])
    # target failed (ok=False) -> must NOT remove anything
    SyncEngine(db).run({"הרצליה": []}, {"הרצליה": False})
    assert db.removed == []
    print("✓ failed/empty fetch skips removals (false-removed guard)")


def test_normalize_defensive():
    client = Yad2Client.__new__(Yad2Client)  # skip __init__/network
    raw = {
        "orderId": "abc123",
        "price": "18,500",
        "address": {
            "city": {"text": "הרצליה"},
            "neighborhood": {"text": "נוף ים"},
            "street": {"text": "הנדיב"},
            "house": {"number": 5, "floor": 2},
        },
        "additionalDetails": {"roomsCount": 4.5, "squareMeter": 140, "property": {"text": "פנטהאוז"}},
        "metaData": {"images": ["https://img.yad2.co.il/x.jpg"]},
        "token": "abc123",
    }
    norm = client._normalize(raw, "הרצליה")
    assert norm is not None
    assert norm.id == "abc123"
    assert norm.price == 18500
    assert norm.rooms == 4.5
    assert norm.size_sqm == 140
    assert norm.neighborhood == "נוף ים"
    assert norm.url.endswith("abc123")
    print("✓ defensive normalize handles nested Yad2 shape")


def test_extract_items_shapes():
    f = Yad2Client._extract_items
    assert f({"data": {"markers": [{"id": 1}]}}) == [{"id": 1}]
    assert f({"data": {"feed_items": [{"id": 2}]}}) == [{"id": 2}]
    assert f({"results": [{"id": 3}]}) == [{"id": 3}]
    print("✓ feed item extraction handles multiple payload shapes")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} smoke tests passed.")
