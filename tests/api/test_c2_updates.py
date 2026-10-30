"""Synthetic end-to-end HTTP source -> pinned catalog publish without Git."""

import json

from packages.sync.http_source import HTTPSource, object_sha


def test_current_product_update_plan_publish_with_http_objects(tmp_path, monkeypatch):
    from apps.api.main import create_app

    app = create_app(tmp_path / "p.sqlite")
    service = app.state.sync
    # Use a complete source-backed synthetic input and the actual adaptation,
    # release archive and worker health path. No downloaded executable is used.
    from tests.sync.test_sync import raw as sample

    data = sample.__wrapped__()
    content = json.dumps(data).encode()
    blob = object_sha("blob", content)
    tree = object_sha("tree", b"100644 ptcg_chs_infos.json\0" + bytes.fromhex(blob))
    commit = "a" * 40

    def transport(path, limit, *, raw=False):
        if path.endswith("commits/main"):
            v = {"sha": commit}
        elif "/git/commits/" in path:
            v = {"sha": commit, "tree": {"sha": tree}, "parents": []}
        elif "/git/trees/" in path:
            v = {
                "sha": tree,
                "truncated": False,
                "tree": [
                    {
                        "path": "ptcg_chs_infos.json",
                        "type": "blob",
                        "mode": "100644",
                        "sha": blob,
                    }
                ],
            }
        else:
            return content
        return json.dumps(v).encode()

    service.custom_source = HTTPSource(
        tmp_path.resolve() / "objects",
        "https://github.com/example/Cards",
        transport=transport,
    )
    service.source = service.custom_source
    monkeypatch.setenv("PATH", "")
    try:
        job = service.start("plan", background=False)
        assert job["state"] == "ready", job.get("error")
        assert job["commit"] == commit
        # Publication keeps current rules and executes the real current-release
        # health worker using the absolute bundled/developer interpreter.
        result = service.start("sync", commit, background=False)
        assert result["state"] in {"partial", "completed"}, result.get("error")
        assert service.releases.current() == result["releaseId"]
    finally:
        service.close()
