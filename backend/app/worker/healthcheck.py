"""Exit 0 when the worker heartbeat is fresh. Used by Docker."""

from __future__ import annotations

import sys
from datetime import timedelta

from app.database import SessionLocal
from app.models import WorkerHeartbeat
from app.timeutils import utcnow


def main() -> int:
    db = SessionLocal()
    try:
        row = db.get(WorkerHeartbeat, 1)
        if row is None or row.last_seen_at is None:
            print("worker heartbeat missing")
            return 1
        if row.status != "running":
            print(f"worker status {row.status}")
            return 1
        if row.last_seen_at < utcnow() - timedelta(seconds=30):
            print("worker heartbeat is stale")
            return 1
        print("worker ok")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
