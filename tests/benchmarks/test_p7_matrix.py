"""Matrix ordering, failure isolation and reproducible benchmark entropy."""

import json
import secrets
from types import SimpleNamespace

import pytest

from benchmarks.online import matrix
from benchmarks.online.fixture import install
from benchmarks.online.waits import event_key


def test_wait_sampling_distinguishes_postgresql_17_wal_io_from_lwlocks():
    assert event_key("IO", "WalSync") == "IO/WalSync"
    assert event_key("IO", "WalWrite") == "IO/WalWrite"
    assert event_key("LWLock", "WALWrite") == "LWLock/WALWrite"
    assert event_key(None, None) == "running"
    assert event_key("Extension", "private_plugin_name") == "other/other"


def test_fixture_repeats_rule_seeds_and_ids_without_modifying_auth_entropy():
    original = secrets.randbelow
    first, second = (SimpleNamespace(secrets=secrets) for _ in range(2))
    install(first, 42)
    install(second, 42)
    assert [first.uid() for _ in range(20)] == [second.uid() for _ in range(20)]
    assert [first.secrets.randbelow(9007199254740992) for _ in range(10)] == [
        second.secrets.randbelow(9007199254740992) for _ in range(10)
    ]
    assert secrets.randbelow is original
    assert first.secrets.token_urlsafe is secrets.token_urlsafe
    assert first.secrets.choice is secrets.choice
    with pytest.raises(ValueError):
        first.secrets.randbelow(10)


@pytest.mark.parametrize(
    "levels,workers,repeats",
    [([25], [1], 3), ([10, 50], [1, 2], 3), ([10], [2, 1], 3), ([10], [1], 2)],
)
def test_matrix_refuses_missing_lower_levels_or_repetitions(levels, workers, repeats):
    with pytest.raises(ValueError):
        matrix.plan(levels, workers, repeats)


def simulated_lab(monkeypatch, tmp_path):
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    monkeypatch.setattr(matrix, "runtime_digest", lambda root: "runtime")
    monkeypatch.setattr(matrix, "tools_digest", lambda root: "tools")
    monkeypatch.setattr(matrix.lab, "prepare", lambda *args: None)
    monkeypatch.setattr(matrix.lab, "metadata", lambda p: (p, {}))
    monkeypatch.setattr(matrix.lab, "start", lambda *args: None)
    monkeypatch.setattr(matrix.lab, "run_driver", lambda *args: None)
    stopped = []
    monkeypatch.setattr(
        matrix.lab, "stop", lambda path, value: stopped.append(path.name)
    )
    return stopped


def test_matrix_failure_stops_ascent_but_preserves_three_repeats_and_other_worker(
    monkeypatch, tmp_path
):
    stopped = simulated_lab(monkeypatch, tmp_path)
    monkeypatch.setattr(
        matrix,
        "summarize",
        lambda path, root: dict(
            status="threshold_failed" if "w1" in path.name else "passed",
            asgiP95UpperSeconds=0.75,
        ),
    )
    path = tmp_path / "var/p7/matrix"
    matrix.run(path, "unused", [10, 25], [1, 2], 3)
    result = json.loads((path / "matrix.json").read_text())
    assert result["status"] == "threshold_stopped"
    assert stopped == [f"m10-w1-r{r}" for r in (1, 2, 3)] + [
        f"m{n}-w2-r{r}" for n in (10, 25) for r in (1, 2, 3)
    ]
    assert result["stoppedWorkers"] == [1]


def test_matrix_command_failure_cleans_owned_lab_and_aborts_all_work(
    monkeypatch, tmp_path
):
    stopped = simulated_lab(monkeypatch, tmp_path)

    def fail(*args):
        raise ValueError("first attempt failed")

    monkeypatch.setattr(matrix.lab, "run_driver", fail)
    path = tmp_path / "var/p7/matrix"
    with pytest.raises(ValueError):
        matrix.run(path, "unused", [10, 25], [1, 2], 3)
    result = json.loads((path / "matrix.json").read_text())
    assert stopped == ["m10-w1-r1"]
    assert result["status"] == "aborted" and not result["samples"]


def test_matrix_failed_start_cleans_only_partial_owned_lab(monkeypatch, tmp_path):
    stopped = simulated_lab(monkeypatch, tmp_path)

    def fail(*args):
        raise ValueError("startup failed")

    cleaned = []
    monkeypatch.setattr(matrix.lab, "start", fail)
    monkeypatch.setattr(
        matrix.lab,
        "cleanup_failed_start",
        lambda path, value: cleaned.append(path.name),
    )
    with pytest.raises(ValueError):
        matrix.run(tmp_path / "var/p7/matrix", "unused", [10], [1], 3)
    assert cleaned == ["m10-w1-r1"] and stopped == []
