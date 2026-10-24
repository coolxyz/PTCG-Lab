"""Opt-in tests using an already approved, private P2 bundle; no asset import."""

from dataclasses import replace
import json
import os
from pathlib import Path
import time
import pytest
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.worker import RuleWorker
from tests.online_v2.test_process import server, call
from tests.online_v2.test_lobby import body


@pytest.fixture
def real(settings):
    source = os.environ.get("PTCG_V2_REAL_INPUTS_FILE")
    if not source:
        pytest.skip("Explicit private approved rule inputs not configured")
    inputs = json.loads(Path(source).read_text())
    configured = replace(
        settings,
        rule_directory=inputs["directory"],
        rule_manifest_sha256=inputs["pin"],
        approved_rule_release_id=inputs["release"],
    )
    return configured, inputs


def test_real_rule_worker_isolated_validation_and_deterministic_initialization(real):
    import sys

    settings, inputs = real
    worker = RuleWorker(settings)
    assert worker.probe()
    for deck in (inputs["deck1"], inputs["deck2"]):
        assert worker.validate(deck)["entries"]
    with pytest.raises(ServiceError, match="INVALID_PAYLOAD"):
        worker.validate([{"printingId": "unknown-test-printing", "quantity": 60}])
    assert worker.available
    as_of = worker.validate(inputs["deck1"])["asOf"]
    job = {**inputs, "rule_release_id": inputs["release"], "as_of": as_of, "seed": 1341}
    first = worker.initialize(job)
    second = worker.initialize(job)
    assert first == second
    assert first["schema"] == "core-recovery-v2" and first["commands"] == []
    assert not any(m == "ptcg" or m.startswith("ptcg.") for m in sys.modules)


def test_two_real_http_clients_initialize_same_job_after_api_restart(real, tmp_path):
    from ptcg_online_server.repository import Repository

    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    with server(settings, tmp_path) as origin:
        tokens = [
            call(origin, "/sessions", body={"protocolVersion": "2.0"})["accessToken"]
            for _ in range(2)
        ]
        decks = [
            call(
                origin,
                "/decks",
                body=body(ruleReleaseId=inputs["release"], entries=inputs[name]),
                access=token,
            )["deckRevision"]
            for name, token in zip(("deck1", "deck2"), tokens)
        ]
        created = call(
            origin,
            "/rooms",
            body=body(ruleReleaseId=inputs["release"], deckRevision=decks[0]),
            access=tokens[0],
        )
        room = call(
            origin,
            "/rooms/join",
            body=body(inviteCode=created["inviteCode"], deckRevision=decks[1]),
            access=tokens[1],
        )
        path = "/rooms/" + room["roomId"]
        call(
            origin,
            path + "/ready",
            body=body(expectedRoomVersion=2, ready=True),
            access=tokens[0],
        )
        request = body(expectedRoomVersion=3, ready=True)
        room = call(origin, path + "/ready", body=request, access=tokens[1])
    with repo.db() as db:
        before = db.execute(
            "SELECT match_id,seed,revision1,revision2 FROM ptcg_v2.initializations"
        ).fetchone()
        # A killed initializer may hold a lease. Expire only this test-owned job
        # rather than waiting 60 seconds; production waits for lease expiry.
        db.execute(
            "UPDATE ptcg_v2.initializations SET lease_until=now()-interval '1 second' WHERE status='running'"
        )
    with server(settings, tmp_path) as origin:
        assert call(origin, path + "/ready", body=request, access=tokens[1]) == room
        for _ in range(80):
            result = call(origin, path, access=tokens[0])
            if result["status"] == "initialized":
                break
            time.sleep(0.25)
        assert (
            result["status"] == "initialized"
            and result["matchId"] == before["match_id"]
        )
        with repo.db() as db:
            after = db.execute(
                "SELECT match_id,seed,revision1,revision2 FROM ptcg_v2.initializations"
            ).fetchone()
            saved = db.execute(
                "SELECT recovery FROM ptcg_v2.initializations"
            ).fetchone()["recovery"]
        assert before == after and saved["schema"] == "core-recovery-v2"
        assert "seed" not in json.dumps(result) and "stateDigest" not in json.dumps(
            result
        )
