"""Optional diagnostic-only PostgreSQL wait sampling inside an owned test API.

No query text, backend identity, DSN, game data or authentication material is saved.
This extra observer is excluded from the formal capacity matrix.
"""

from collections import Counter
import json
import os
from pathlib import Path
import sys
import time

import psycopg


def event_key(kind, event):
    allowed = {
        "WalSync",
        "WalWrite",
        "WALWrite",
        "WALInsert",
        "WALBufMapping",
        "DataFileRead",
        "DataFileWrite",
        "DataFileSync",
        "transactionid",
        "advisory",
        "tuple",
        "ClientRead",
        "ClientWrite",
    }
    if event is None:
        return "running"
    kind = kind if kind in {"IO", "LWLock", "Lock", "Client", "IPC"} else "other"
    return kind + "/" + (event if event in allowed else "other")


def main(seconds):
    if not 1 <= seconds <= 600:
        raise ValueError("BOUNDED_DIAGNOSTIC_DURATION_REQUIRED")
    counts = Counter()
    started, queries = time.monotonic(), 0
    with psycopg.connect(
        os.environ["PTCG_ONLINE_V2_DATABASE_URL"],
        autocommit=True,
        application_name="p7-wait-observer",
    ) as db:
        while time.monotonic() - started < seconds:
            rows = db.execute("""SELECT wait_event_type,wait_event,count(*) FROM pg_stat_activity
                WHERE application_name='ptcg-online-v2' AND state='active'
                GROUP BY wait_event_type,wait_event""").fetchall()
            for kind, event, count in rows:
                counts[event_key(kind, event)] += count
            queries += 1
            time.sleep(0.05)
    result = dict(
        schema=1,
        diagnosticOnly=True,
        intervalSeconds=0.05,
        queries=queries,
        elapsedSeconds=time.monotonic() - started,
        activeBackendWaitSamples=dict(counts),
    )
    Path("/measure/db-waits.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(dict(status="passed", queries=queries)))


if __name__ == "__main__":
    try:
        main(int(sys.argv[1]))
    except Exception as error:
        print(json.dumps(dict(status="failed", error=type(error).__name__)))
        raise SystemExit(1) from None
