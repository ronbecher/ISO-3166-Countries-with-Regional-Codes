"""run.py — entrypoint called by the cron job (brief §12 step 6).

Wires the pipeline: yad2_client (fetch) -> sync (delta) -> notifier (Telegram).
Loads .env locally for convenience; in CI the secrets come from the workflow env.
"""

from __future__ import annotations

import logging
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv optional at runtime in CI
    pass

from db import DB
from notifier import Notifier
from sync import SyncEngine
from yad2_client import Yad2Client


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run")

    # 1) fetch
    with Yad2Client() as client:
        per_target, target_ok = client.fetch_all()

    total = sum(len(v) for v in per_target.values())
    log.info("Fetched %d listings across %d targets", total, len(per_target))

    if total == 0 and not any(target_ok.values()):
        # Every target failed — abort before touching the DB so we don't flag
        # spurious removals (defense-in-depth on top of sync's per-target guard).
        log.error("All targets failed to fetch — aborting run without DB writes")
        return 1

    # 2) delta engine
    db = DB()
    engine = SyncEngine(db)
    events = engine.run(per_target, target_ok)
    log.info("Delta engine produced %d notifiable events", len(events))

    # 3) notify
    Notifier().notify(events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
