"""Exercise real frozen workers, publication, rollback and offline restoration."""
import copy
import json

from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.collection.domain import RAW
from packages.sync.common import ROOT, digest, read, write
from packages.sync.portable import export_bundle, restore_bundle
from packages.sync.runtime import RuntimePool
from packages.sync.releases import Releases


def test_old_and_new_matches_survive_release_restart_and_restore(tmp_path):
    database = tmp_path / "user.sqlite"
    app = create_app(database)
    sync = app.state.sync
    with TestClient(app) as client:
        client.post("/api/battle/session", json={}).raise_for_status()
        deck = app.state.store.create_deck("before", RAW["templates"][0]["entries"])
        revision = app.state.store.freeze(deck["id"], deck["version"])
        request = {"requestId": "before-sync-test", "revisionId": revision["id"], "opponent": "dragapult"}
        before = client.post("/api/battle/matches", json=request)
        assert before.status_code == 201, before.text
        before_id = before.json()["matchId"]
        baseline = sync.baseline()
        catalog = copy.deepcopy(RAW)
        extra = copy.deepcopy(catalog["cards"][0])
        extra.update(printingId="CN:CHS:999999", effectStatus="unverified", sourceVerified=False, cnName="迁移测试身份")
        catalog["cards"].append(extra)
        catalog["version"] = digest(catalog)
        release = sync.releases.archive(catalog=catalog, metadata={"kind": "catalog"})
        sync.extend_identity_archive(release)
        sync.releases.activate(release, None, sync.jobs.path)
        with TestClient(create_app(database), base_url="http://127.0.0.1:8777") as local:
            assert local.post("/api/battle/session", json={}, headers={"Origin": "http://127.0.0.1:8777"}).status_code == 200
            assert local.post("/api/battle/session", json={}, headers={"Origin": "https://example.com"}).status_code == 403
        assert client.get("/api/battle/matches/" + before_id).headers["x-ptcg-release"] == baseline
        with TestClient(create_app(database)) as stranger:
            assert stranger.get("/api/battle/matches/" + before_id).status_code == 401
        duplicate = client.post("/api/battle/matches", json=request)
        assert duplicate.status_code == 201 and duplicate.json()["matchId"] == before_id
        # Use a revision frozen by the new worker, so catalog provenance is real.
        worker = sync.pool.get(release)
        created = worker.request("POST", "/api/decks", {"Content-Type": "application/json"}, json.dumps({"name": "after", "entries": RAW["templates"][0]["entries"]}).encode())
        assert created["status"] in (200, 201), created
        body = json.loads(created["body"])
        frozen = worker.request("POST", "/api/decks/" + body["id"] + "/revisions", {"Content-Type": "application/json"}, json.dumps({"expectedVersion": body["version"]}).encode())
        assert frozen["status"] in (200, 201), frozen
        new_request = {**request, "requestId": "after-sync-test", "revisionId": json.loads(frozen["body"])["id"]}
        after = client.post("/api/battle/matches", json=new_request)
        assert after.status_code == 201, after.text
        after_id = after.json()["matchId"]
        sync.pool.close()
        assert client.get("/api/battle/matches/" + after_id).headers["x-ptcg-release"] == release
        sync.rollback(baseline, release)
        assert client.get("/api/battle/matches/" + after_id).headers["x-ptcg-release"] == release
        assert client.get("/api/battle/matches/" + before_id).headers["x-ptcg-release"] == baseline
        bundle = tmp_path / "backup.zip"
        export_bundle(sync, bundle)
    restored = restore_bundle(bundle, tmp_path / "restored")
    pool = RuntimePool(Releases(restored["home"]), restored["database"])
    try:
        assert pool.route("/api/battle/matches/" + after_id) == release
        assert pool.get(release).request("GET", "/api/meta")["status"] == 200
    finally:
        pool.close()

