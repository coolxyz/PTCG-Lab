"""Private, opt-in P2-C runtime check; run with installed wheels outside checkout.

Inputs and report remain operator-owned private files. This is an extraction
smoke comparison, not P2-D's full rules/hidden-state/concurrency acceptance.
"""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import random
import subprocess
import sys


def choose(view, rng):
    decision = view["decision"]
    if decision["kind"] == "selection":
        count = rng.randint(
            decision["min"], min(decision["max"], len(decision["candidates"]))
        )
        choice = {
            "selectedRefs": [
                c["ref"] for c in rng.sample(decision["candidates"], count)
            ]
        }
    else:
        ranks = {
            "PlayPokemonAction": 90,
            "EvolvePokemonAction": 85,
            "AttachEnergyAction": 80,
            "UseAbilityAction": 75,
            "UseSupporterAction": 70,
            "UseItemAction": 65,
            "UseToolAction": 60,
            "PutStadiumAction": 55,
            "UseStadiumAction": 50,
            "AttackAction": 40,
            "RetreatAction": 10,
            "PassTurn": 0,
        }
        selected = max(
            decision["options"],
            key=lambda o: (ranks.get(o["actionType"], 5), rng.random()),
        )
        choice = {"optionId": selected["id"]}
    return {
        "commandId": f'cmd{view["stateVersion"]}',
        "expectedStateVersion": view["stateVersion"],
        "decisionId": decision["id"],
        "choice": choice,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from ptcg_engine_provider.bundle import DirectoryEngineProvider
    from ptcg_core.factory import CoreFactory

    bundle = DirectoryEngineProvider(args.bundle, args.manifest_sha256).load()
    factory = CoreFactory(bundle)
    assert CoreFactory(bundle).runtime is factory.runtime
    try:
        CoreFactory(replace(bundle, bundle_id=bundle.bundle_id + "-different"))
    except RuntimeError as exc:
        assert str(exc) == "RULE_BUNDLE_CONFLICT"
    else:
        raise AssertionError("mixed rule bundles allowed")
    from ptcg_core.base import digest

    case = json.loads(args.case.read_text())
    game = factory.create_match(
        case["deck1"], case["deck2"], seed=case["seed"], as_of=case["asOf"]
    )
    recovered = None
    rows, commands = [], []
    rng = random.Random(92831)
    seat_names = ("PLAYER1", "PLAYER2")
    restored_at = min(25, case["steps"] // 2)
    for step in range(case["steps"] + 1):
        views = [game.project_view(s) for s in seat_names]
        rows.append(
            {
                "views": digest(views),
                "state": game.export_recovery_record()["stateDigest"],
            }
        )
        if recovered is not None:
            assert [recovered.project_view(s) for s in seat_names] == views
            assert recovered.export_recovery_record() == game.export_recovery_record()
        if step == restored_at:
            recovered = factory.restore_match(game.export_recovery_record())
            assert [recovered.project_view(s) for s in seat_names] == views
        if views[0]["done"] or step == case["steps"]:
            break
        actor = next(i for i, view in enumerate(views) if view["decision"])
        command = choose(views[actor], rng)
        receipt = game.apply_command(seat_names[actor], command)
        assert game.apply_command(seat_names[actor], command) == receipt
        commands.append({"viewer": seat_names[actor], "command": command})
        if recovered is not None:
            assert recovered.apply_command(seat_names[actor], command) == receipt
    # The legacy worker receives exactly the same frozen engine lines/provenance.
    # No source path enters the installed core worker.
    record = game.export_recovery_record()
    payload = {"config": record["config"], "commands": commands}
    code = """
import json,sys
from packages.battle.runtime import Adapter, PlayerId, view, ENGINE_VERSION
from packages.engine_adapter.base import digest
payload=json.load(sys.stdin)
game=Adapter(**payload['config'])
rows=[]
for row in [None]+payload['commands']:
    if row is not None: game.submit(PlayerId[row['viewer']],row['command'])
    rows.append({'views':digest([view(game,s) for s in (PlayerId.PLAYER1,PlayerId.PLAYER2)]),'state':game.private_digest()})
print(json.dumps({'legacyImplementation':ENGINE_VERSION,'rows':rows}))
"""
    child = subprocess.run(
        [sys.executable, "-c", code],
        cwd=args.legacy_root,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    legacy = json.loads(child.stdout)
    if rows != legacy["rows"]:
        mismatch = next(
            i for i, pair in enumerate(zip(rows, legacy["rows"])) if pair[0] != pair[1]
        )
        raise AssertionError(f"legacy/core mismatch at step {mismatch}")
    # Old-path imports delegate to exact same class objects; nothing is copied.
    from ptcg_core.compat import install_legacy_imports

    install_legacy_imports(factory)
    from packages.battle.runtime import Adapter as Alias, view as alias_view, PlayerId
    from ptcg_core.rules.adapter import Adapter

    assert Alias is Adapter
    compat_game = Alias(**record["config"])
    assert (
        digest(
            [alias_view(compat_game, s) for s in (PlayerId.PLAYER1, PlayerId.PLAYER2)]
        )
        == rows[0]["views"]
    )
    try:
        factory.restore_match({**record, "schema": "p01-replay-v1"})
    except ValueError:
        pass
    else:
        raise AssertionError("legacy recovery identity accepted")
    forbidden = {"sqlite3", "fastapi", "torch", "numpy", "PySide6"}
    assert not any(name.split(".")[0] in forbidden for name in sys.modules)
    report = {
        "schema": 1,
        "steps": len(commands),
        "framesCompared": len(rows),
        "finished": game.project_view("PLAYER1")["done"],
        "bothPlayerProjectionsAndPrivateDigests": "identical",
        "restoreAt": restored_at,
        "restoreAndContinue": "passed",
        "idempotentRetries": "passed",
        "legacyImportDelegation": "passed",
        "legacyRecoveryRejected": "passed",
        "ruleReleaseId": factory.rule_release_id,
        "legacyImplementation": legacy["legacyImplementation"],
        "bundleId": bundle.bundle_id,
        "productionRoutingChanged": False,
        "p2dComplete": False,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
