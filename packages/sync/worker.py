"""JSON-lines transport for an isolated, frozen application release.

Run as a file, not as an imported module. Request bodies are never executed.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
from pathlib import Path
import sys
import traceback
import sqlite3


def main():
    root, database, release_id, archive_index = sys.argv[1:5]
    root = Path(root).resolve()
    os.chdir(root)
    sys.path[:] = [str(root)] + [p for p in sys.path if "packages/sync" not in p.replace("\\", "/")]
    os.environ.update(PTCG_SYNC_WORKER="1", PTCG_DB=database, PTCG_RELEASE_ID=release_id, PTCG_ARCHIVE_INDEX=archive_index)
    with contextlib.redirect_stdout(sys.stderr):
        from fastapi.testclient import TestClient
        from apps.api.main import create_app
        # Historical releases predate the archive loader. Extend only unknown
        # identities for user references, never grant them historical play support.
        from packages.collection import domain
        archive_stamp = None
        def refresh_identities():
            nonlocal archive_stamp
            if not Path(archive_index).exists():
                return
            stamp = Path(archive_index).stat().st_mtime_ns
            if stamp == archive_stamp:
                return
            archived = json.loads(Path(archive_index).read_text(encoding="utf-8"))
            for pid, card in archived.items():
                if pid not in domain.CARDS:
                    domain.CARDS[pid] = {**card, "effectStatus": "unverified", "sourceVerified": False, "reviewNote": "历史收藏身份；不在此发布的对战范围。"}
            archive_stamp = stamp
        refresh_identities()
        client = TestClient(create_app(database))
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                refresh_identities()
                # Transport has no session of its own. Never reuse a cookie set by
                # a previous caller sharing this release worker.
                client.cookies.clear()
                response = client.request(request["method"], request["path"], headers=request.get("headers", {}), content=base64.b64decode(request.get("body", "")))
            result = {"status": response.status_code, "headers": {k: v for k, v in response.headers.items() if k.lower() not in ("content-encoding", "content-length", "transfer-encoding", "connection")}, "body": base64.b64encode(response.content).decode()}
        except sqlite3.IntegrityError as exc:
            message = b'{"code":"VARIANT_ALLOCATION_CONFLICT","message":"Release variant allocations before reducing collection quantity"}'
            result = {"status": 409, "headers": {"content-type": "application/json"}, "body": base64.b64encode(message).decode()}
        except Exception:
            traceback.print_exc(file=sys.stderr)
            result = {"status": 500, "headers": {"content-type": "application/json"}, "body": base64.b64encode(b'{"code":"WORKER_ERROR","message":"Version worker failed"}').decode()}
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
