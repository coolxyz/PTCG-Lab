from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from ptcg_online_server.config import Settings
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.multirelease import ReleaseWorkers


@pytest.fixture
def workers(monkeypatch):
    def fake(settings, **kwargs):
        worker = Mock()
        worker.settings = settings
        worker.available = True
        worker.slots = [SimpleNamespace(lock=Lock(), close=Mock())]
        worker.probe.return_value = True
        worker.options = kwargs
        return worker

    monkeypatch.setattr("ptcg_online_server.multirelease.RuleWorker", fake)
    settings = Settings("postgresql://example/db", "test", "https://example.test",
                        approved_rule_release_id="new")
    return ReleaseWorkers(settings, "old", "/approved/previous")


def test_only_explicit_versions_are_supported(workers):
    assert workers.supports_release("old") and workers.supports_release("new")
    with pytest.raises(ServiceError):
        workers.project({"implementation": "unapproved"})
    workers.default.project.assert_not_called()
    workers.previous.project.assert_not_called()


def test_old_record_is_sent_to_exact_previous_core(workers):
    record = {"implementation": "old"}
    workers.command_for_match(record, "PLAYER2", {"choice": {}}, "match")
    workers.previous.command_for_match.assert_called_once_with(record, "PLAYER2", {"choice": {}}, "match")
    workers.default.command_for_match.assert_not_called()
    workers.default.slots[0].close.assert_called_once()
    assert workers.previous.settings.worker_persistent is False
    assert workers.previous.options["pythonpath"] == "/approved/previous"


def test_new_games_and_records_use_default_core(workers):
    workers.initialize({"rule_release_id": "new"})
    workers.validate([{"printingId": "synthetic", "quantity": 60}])
    workers.project_for_match({"implementation": "new"}, "match")
    workers.default.initialize.assert_called_once()
    workers.default.validate.assert_called_once()
    workers.default.project_for_match.assert_called_once()
    workers.previous.initialize.assert_not_called()
    workers.previous.validate.assert_not_called()


def test_both_pins_must_pass_probe(workers):
    workers.previous.probe.return_value = False
    assert workers.probe() is False
    workers.default.probe.assert_called_once()
    workers.previous.probe.assert_called_once()


def test_rule_work_is_bounded_across_versions(workers):
    workers.lock.acquire()
    try:
        with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
            workers.project({"implementation": "old"})
        assert workers.finish_candidate("match", {"recovery": {"implementation": "new"}}, True) is False
    finally:
        workers.lock.release()


def test_cache_ack_routes_to_its_execution_version(workers):
    candidate = {"recovery": {"implementation": "new"}}
    workers.finish_candidate("match", candidate, True)
    workers.default.finish_candidate.assert_called_once_with("match", candidate, True)
    workers.previous.finish_candidate.assert_not_called()


def test_both_workers_are_closed(workers):
    workers.close()
    workers.default.close.assert_called_once()
    workers.previous.close.assert_called_once()
