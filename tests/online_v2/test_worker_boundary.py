"""The private rule process receives no API credentials or research paths."""

from dataclasses import replace
import json
import os
from types import SimpleNamespace
import pytest
from ptcg_online_server.config import Settings
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.worker import RuleWorker


@pytest.mark.parametrize("persistent", [False, True])
def test_worker_drops_secret_environment_and_limits_execution(monkeypatch, persistent):
    settings = Settings(
        "postgresql://secret.invalid/db",
        "test",
        "https://test.invalid",
        worker_persistent=persistent,
        rule_directory="/approved-test",
        rule_manifest_sha256="a" * 64,
        approved_rule_release_id="rules-test",
    )
    monkeypatch.setenv("PTCG_ONLINE_V2_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("UNRELATED_PRIVATE_ENV", "private-sentinel")

    def inspect(env, request, timeout):
        assert timeout == 20
        assert set(env) <= {
            "PATH",
            "SYSTEMROOT",
            "LANG",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONPATH",
        }
        assert request["parent"] == os.getpid()
        assert set(request) == (
            {"parent", "operation", "directory", "pin", "release"}
            | ({"limits"} if persistent else set())
        )
        assert "secret.invalid" not in json.dumps(request)
        assert "private-sentinel" not in json.dumps(env)
        return {"release": "rules-test", "asOf": "2026-10-01"}

    def run(command, **kwargs):
        assert command[-1] == "ptcg_online_server.rule_worker"
        result = inspect(kwargs["env"], json.loads(kwargs["input"]), kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(result).encode())

    monkeypatch.setattr("ptcg_online_server.worker.subprocess.run", run)
    worker = RuleWorker(settings)
    if persistent:
        slot = worker.slots[0]
        assert slot.args[-1] == "--persistent"
        monkeypatch.setattr(
            slot,
            "exchange",
            lambda request, timeout: inspect(slot.options["env"], request, timeout),
        )
    assert worker.probe()
    lock = worker.slots[0].lock if persistent else worker.lock
    lock.acquire()
    try:
        with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
            worker.call("probe")
    finally:
        lock.release()
        worker.close()
    assert not RuleWorker(replace(settings, rule_directory=None)).probe()
