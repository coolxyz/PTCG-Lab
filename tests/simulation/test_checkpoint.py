"""Portable private continuation tests, including actual process boundaries."""

import json
import os
import subprocess
import sys
import pytest
from packages.battle.agent import predict
from packages.battle.runtime import Adapter, view
from packages.simulation.fork import RuleFork, Continuation
from packages.simulation.checkpoint import dumps, loads


def test_graph_aliases_cycles_enums_and_rng():
    import random
    from packages.battle.runtime import PlayerId

    shared = [PlayerId.PLAYER1, random.Random(73)]
    graph = {"a": shared, "b": shared}
    graph["cycle"] = graph
    restored = loads(dumps(graph))
    assert restored["a"] is restored["b"]
    assert restored["cycle"] is restored
    assert restored["a"][0] is PlayerId.PLAYER1
    assert restored["a"][1].random() == shared[1].random()


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": "no", "nodes": [], "root": None},
        {"schema": "ptcg-private-graph-v1", "nodes": [], "root": {"ref": -1}},
        {
            "schema": "ptcg-private-graph-v1",
            "nodes": [{"kind": "object", "type": "os:system", "fields": None}],
            "root": {"ref": 0},
        },
        {
            "schema": "ptcg-private-graph-v1",
            "nodes": [{"kind": "tuple", "items": [{"ref": 0}]}],
            "root": {"ref": 0},
        },
    ],
)
def test_rejects_unknown_types_and_malformed_graph(payload):
    with pytest.raises(ValueError):
        loads(json.dumps(payload))


def test_checkpoint_in_new_process_preserves_next_choice(tmp_path):
    branch = RuleFork(Adapter(73))
    for _ in range(35):
        branch.submit(
            branch.game.actor, predict(view(branch.game, branch.game.actor))[0]
        )
    token = branch.checkpoint().to_json()
    path = tmp_path / "private.json"
    path.write_text(token, encoding="utf-8")
    command = predict(view(branch.game, branch.game.actor))[0]
    branch.submit(branch.game.actor, command)
    script = """
import json,sys
from pathlib import Path
from packages.simulation.fork import RuleFork, Continuation
b=RuleFork.restore(Continuation.from_json(Path(sys.argv[1]).read_text(encoding='utf-8')))
b.submit(b.game.actor,json.loads(sys.stdin.read()))
print(json.dumps({'digest':b.game.private_digest(),'view':b.game.view(b.game.actor)}))
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, str(path)],
        input=json.dumps(command),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=30,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    restored = json.loads(result.stdout)
    assert restored["digest"] == branch.game.private_digest()
    assert restored["view"] == branch.game.view(branch.game.actor)


def test_serialized_version_mismatch():
    token = RuleFork(Adapter(7)).checkpoint()
    token.engine_version = "unknown"
    with pytest.raises(ValueError, match="ENGINE_VERSION_MISMATCH"):
        Continuation.from_json(token.to_json())
