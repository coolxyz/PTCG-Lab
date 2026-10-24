"""Private P2-D worker: frozen legacy versus installed core, without app services.

Copy this script outside checkout for wheel acceptance. Inputs/transcripts contain
private engine records and must stay in an ignored, operator-owned directory.
"""

import argparse
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import random

SEATS = ("PLAYER1", "PLAYER2")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def choose(v, rng):
    d = v["decision"]
    if d["kind"] == "selection":
        n = rng.randint(d["min"], min(d["max"], len(d["candidates"])))
        choice = {"selectedRefs": [c["ref"] for c in rng.sample(d["candidates"], n)]}
    else:
        ranks = dict(
            PlayPokemonAction=90,
            EvolvePokemonAction=85,
            AttachEnergyAction=80,
            UseAbilityAction=75,
            UseSupporterAction=70,
            UseItemAction=65,
            UseToolAction=60,
            PutStadiumAction=55,
            UseStadiumAction=50,
            AttackAction=40,
            RetreatAction=10,
            PassTurn=0,
        )
        choice = {
            "optionId": max(
                d["options"],
                key=lambda o: (ranks.get(o["actionType"], 5), rng.random()),
            )["id"]
        }
    return dict(
        commandId=f'cmd{v["stateVersion"]}',
        expectedStateVersion=v["stateVersion"],
        decisionId=d["id"],
        choice=choice,
    )


def load(args):
    global Adapter, PlayerId, view, effects, explain, factory
    factory = None
    if args.mode == "legacy":
        from packages.battle.runtime import Adapter, PlayerId, view
        from packages.simulation.events import public_effects as effects
        from packages.simulation.results import explain
    else:
        from ptcg_engine_provider.bundle import DirectoryEngineProvider
        from ptcg_core.factory import CoreFactory

        factory = CoreFactory(DirectoryEngineProvider(args.bundle, args.pin).load())
        from ptcg_core.rules.adapter import Adapter
        from ptcg.core.enums import PlayerId
        from ptcg_core.projection import view
        from ptcg_core.events import public_effects as effects
        from ptcg_core.results import explain
    from loguru import logger

    logger.remove()


def module(name):
    return importlib.import_module(
        ("ptcg_core.rules." if factory else "packages.rules.") + name
    )


def new(case):
    config = case["config"]
    if factory:
        provenance = config["provenance"]
        match = factory.create_match(
            provenance["deck1"],
            provenance["deck2"],
            seed=config["seed"],
            as_of=provenance["formatAsOf"],
        )
        assert match._adapter.config["provenance"] == config["provenance"]
        return match._adapter
    return Adapter(**config)


def normalize(v):
    v = deepcopy(v)
    # Only these two documented additions differ; wire checks prove the binding.
    for c in (v.get("decision") or {}).get("candidates", []):
        c.pop("revealedPrizeIndex", None)
        c.pop("revealedPrizeSide", None)
    return v


def classify(g):
    if g.done:
        return {"terminal"}
    d = view(g, g.actor)["decision"]
    tags = {"setup:" + g.env.phase} if g.env.start_stage else {"playing"}
    if d["kind"] == "selection":
        tags.add("hidden-selection" if d["hidden"] else "visible-selection")
        if d.get("deckInspection"):
            tags.add("deck-search")
        if d["hidden"] and any("card" in c for c in d["candidates"]):
            tags.add("face-up-prize")
        if d["hidden"] and all(
            str(getattr(c, "cardPosition", "")) == "CardPosition.PRIZE"
            for c in g.actions.candidates
        ):
            tags.add("prize")
    if not g.env.start_stage and g.actor != g.env.gamestate.turn:
        tags.add("non-turn-actor")
    if not g.env.start_stage and not any(
        p.active for p in g.env.get_players() if p.id == g.actor
    ):
        tags.add("replacement")
    return tags


def frame(g, previous=None, wire=True):
    vs = [view(g, PlayerId[s]) for s in SEATS]
    deltas = [
        effects(previous[i]["observation"] if previous else None, v["observation"])
        for i, v in enumerate(vs)
    ]
    results = [explain(v) for v in vs]
    if factory and wire:
        from ptcg_protocol.projection import snapshot, legacy_view, replay_page

        for i, (seat, v) in enumerate(zip(SEATS, vs)):
            snap = snapshot(
                v,
                match_id="acceptance",
                rule_release_id=factory.rule_release_id,
                viewer_seat=seat,
                match_seq=g.version,
                controller_epoch=1,
                stream_cursor=f"s{g.version}",
                visual_effects=deltas[i],
                result=results[i],
            )
            assert legacy_view(snap) == v, "LOSSY_PROJECTION"
            page = replay_page([snap])
            forbidden = {
                "seed",
                "config",
                "commands",
                "stateDigest",
                "recoveryRecord",
                "rawInfo",
                "rng",
                "player1",
                "player2",
                "left",
            }

            def inspect(x):
                if isinstance(x, dict):
                    assert not set(x) & forbidden, "PRIVATE_FIELD_LEAK"
                    for v in x.values():
                        inspect(v)
                elif isinstance(x, list):
                    for v in x:
                        inspect(v)

            inspect(snap)
            inspect(page)
            assert snap["observation"]["opponent"]["hand"] is None
            assert not set(page["frames"][0]) & {
                "decision",
                "controllerEpoch",
                "streamCursor",
            }
            if g.done:
                assert snap["decision"] is None
    return (
        dict(
            views=digest([normalize(v) for v in vs]),
            state=g.private_digest(),
            effects=digest(deltas),
            results=digest(results),
            coverage=sorted(classify(g)),
        ),
        vs,
    )


def accepted(g, row):
    seat, cmd = row["viewer"], row["command"]
    if not factory:
        return g.submit(PlayerId[seat], cmd)
    from ptcg_core.factory import Match
    from ptcg_protocol.projection import snapshot, core_command

    snap = snapshot(
        view(g, PlayerId[seat]),
        match_id="acceptance",
        rule_release_id=factory.rule_release_id,
        viewer_seat=seat,
        match_seq=g.version,
        controller_epoch=1,
        stream_cursor=f"s{g.version}",
    )
    assert (
        core_command(
            dict(
                protocolVersion="2.0",
                expectedMatchSeq=g.version,
                controllerEpoch=1,
                **cmd,
            ),
            trusted_view=snap,
            authenticated_seat=seat,
        )
        == cmd
    )
    m = Match(g, factory.rule_release_id)
    before = g.private_digest()
    for who, bad in [
        (next(s for s in SEATS if s != seat), cmd),
        (seat, {**cmd, "commandId": "reject-stale", "expectedStateVersion": -1}),
        (
            seat,
            {**cmd, "commandId": "reject-choice", "choice": {"optionId": "invalid"}},
        ),
    ]:
        try:
            m.apply_command(who, bad)
        except ValueError:
            pass
        else:
            raise AssertionError("INVALID_COMMAND_ACCEPTED")
        assert g.private_digest() == before
    ack = m.apply_command(seat, cmd)
    after = g.private_digest()
    assert m.apply_command(seat, cmd) == ack
    try:
        m.apply_command(seat, {**cmd, "choice": {"optionId": "conflict"}})
    except ValueError:
        pass
    else:
        raise AssertionError("IDEMPOTENCY_CONFLICT_ACCEPTED")
    assert g.private_digest() == after
    return ack


def hidden_probe(g):
    for seat in SEATS:
        before = view(g, PlayerId[seat])
        d = before["decision"]
        if d and d["kind"] == "selection" and not d["hidden"]:
            continue
        changes = []
        players = list(g.env.get_players())
        decks = [p.left[:] for p in players]
        for p in players:
            p.left.reverse()
            for i, c in enumerate(
                ([] if p.id == PlayerId[seat] else p.hand)
                + [c for c in p.prize if not getattr(c, "prize_face_up", False)]
            ):
                changes.append((c, c.name))
                c.name = f"PRIVATE_SENTINEL_{seat}_{i}"
        try:
            assert view(g, PlayerId[seat]) == before, "HIDDEN_STATE_INFLUENCED_VIEW"
        finally:
            for c, name in changes:
                c.name = name
            for p, deck in zip(players, decks):
                p.left[:] = deck
    original = g.private_digest()
    v = view(g, PlayerId.PLAYER1)
    v["observation"]["self"]["hand"].clear()
    v["setupEvents"].clear()
    assert g.private_digest() == original


def directed(case, kind):
    """Test-only deterministic board recipe, not a production restore format."""
    g = new(case)
    rng = random.Random(41)
    while g.env.start_stage:
        g.submit(g.actor, choose(view(g, g.actor), rng))
    from ptcg.core.card_registry import registry
    from ptcg.core.enums import CardPosition, PokemonPosition, SpecialCondition
    from ptcg.core.reducer import reduce_choose_card_actions
    from ptcg.core.action import choose_card_actions

    s = g.env.gamestate
    s.turn = s.starting_player = PlayerId.PLAYER1
    p, o = s.player1, s.player2

    def put(player, zone, ids):
        cards = [registry.get(k)() for k in ids]
        setattr(player, zone, cards)
        for i, c in enumerate(cards):
            c.index = i + 1
            c.cardPosition = CardPosition[zone.upper()]
            if zone in ("active", "bench"):
                c.position = PokemonPosition[zone.upper()]
                c.firstTurnPlayed = False

    for player in (p, o):
        for zone in ("hand", "left", "prize", "active", "bench", "discard", "lostZone"):
            setattr(player, zone, [])
        player.firstTurn = player.energyPlayedTurn = player.supporterPlayedTurn = False
        for zone, ids in dict(
            active=["P01-005"],
            bench=["P01-006", "P01-005"],
            left=["SVE-008", "SVE-002", "P01-005", "PAF-087"],
            hand=["SVE-008", "SVE-002"],
            prize=["SVE-008", "SVE-002", "P01-005"],
        ).items():
            put(player, zone, ids)
    g.env.phase = "playing"
    g.env.start_stage = False
    g.commands = []
    g.receipts = {}
    g.version = 0
    g.done = g.failed = False
    if kind == "face-up-prize":
        p.prize[0].prize_face_up = True
    if kind == "replacement":
        o.active[0].hp = 0
    if kind == "effects":
        c = p.active[0]
        c.maximum_hp = c.hp + 30
        c.special_condition = SpecialCondition.CONFUSED
        c.poisoned = c.burned = True
        c.poison_damage = 30
        c.retreat_blocked_turn = c.attack_blocked_turn = s.turn_number
        c.attack_locks = ["Synthetic lock"]
        c.attack_protection = {"damage": True, "effects": True, "source": "synthetic"}
        c.damage_shield = {"amount": 20, "maximum": 30}
        c.attack_damage_reduction = {"turn": s.turn_number, "amount": 10}
        c.turn_damage_bonus = {"turn": s.turn_number, "amount": 20}
        c.damage_retaliation = {"turn": s.turn_number, "counters": 2}

    def sequence():
        if kind in ("prize", "face-up-prize"):
            yield from module("prizes").take(p, 1, s)
        elif kind == "replacement":
            yield from module("knockouts").resolve_group(s)
        elif kind in ("search", "private-top", "hidden-opponent-choice"):
            actor = o if kind == "hidden-opponent-choice" else p
            actions = (
                module("deck_search").choose_card_actions(
                    p.id, p.id, 1, 1, list(p.left), deck_owner=p
                )
                if kind == "search"
                else choose_card_actions(
                    actor.id,
                    p.id,
                    1,
                    1,
                    list(p.left[:2]),
                    hidden=kind == "hidden-opponent-choice",
                )
            )
            yield from reduce_choose_card_actions(actions, s)
        elif kind == "coin":
            from ptcg.utils.utils import flip_coin

            for _ in range(7):
                flip_coin(s)
        else:
            yield from reduce_choose_card_actions(
                choose_card_actions(p.id, p.id, 1, 1, list(p.bench), indexed=True), s
            )
        while True:
            yield (
                s.get_obs(p.id),
                0,
                False,
                {"raw_available_actions": p.get_actions(s), "fixtureFinished": True},
            )

    g.env.reducer = sequence()
    result = next(g.env.reducer)
    g.env._remember_available_actions(result)
    g.obs, _, g.done, g.info = result
    return g


def natural(cases, expected):
    results = []
    checkpoints = []
    coverage = set()
    global_rng = random.getstate()
    for i, case in enumerate(cases):
        g = new(case)
        previous = None
        rows = []
        commands = []
        seen = set()
        rng = random.Random(92831 + i)
        clone = None
        for step in range(1001):
            row, vs = frame(g, previous)
            rows.append(row)
            coverage.update(row["coverage"])
            if expected:
                assert row == expected[i]["rows"][step], f"EQUIVALENCE:{i}:{step}"
            if factory:
                if clone:
                    assert (
                        frame(clone, previous, False)[0] == row
                    ), f"RESTORE_CONTINUATION:{i}:{step}"
                tags = set(row["coverage"]) - seen
                if tags and step > 0 and not g.done:
                    record = g.export_private_replay()
                    clone = Adapter.replay(record)
                    assert clone.private_digest() == g.private_digest()
                    checkpoints.append(
                        dict(case=i, step=step, tags=sorted(tags), record=record)
                    )
                    seen.update(tags)
                if step in (0, 30) or "prize" in row["coverage"]:
                    hidden_probe(g)
            if g.done:
                break
            cmd = (
                expected[i]["commands"][step]
                if expected
                else dict(
                    viewer=g.actor.name,
                    command=choose(vs[SEATS.index(g.actor.name)], rng),
                )
            )
            accepted(g, cmd)
            if clone:
                clone.submit(PlayerId[cmd["viewer"]], cmd["command"])
            commands.append(cmd)
            previous = vs
        assert g.done, f"UNFINISHED_NATURAL:{i}"
        results.append(dict(rows=rows, commands=commands))
    assert random.getstate() == global_rng, "GLOBAL_RNG_CHANGED"
    return results, checkpoints, coverage


def scenes(cases, expected):
    result = {}
    coverage = set()
    for kind in (
        "prize",
        "face-up-prize",
        "replacement",
        "search",
        "private-top",
        "hidden-opponent-choice",
        "effects",
        "coin",
    ):
        g = directed(cases[0], kind)
        previous = None
        rows = []
        commands = []
        rng = random.Random(51)
        noise = new(cases[1]) if factory else None
        for step in range(12):
            row, vs = frame(g, previous)
            rows.append(row)
            coverage.update(row["coverage"])
            if expected:
                assert (
                    row == expected[kind]["rows"][step]
                ), f"DIRECTED_EQUIVALENCE:{kind}:{step}"
            if factory:
                hidden_probe(g)
                before = g.private_digest()
                noise.submit(noise.actor, choose(view(noise, noise.actor), rng))
                assert g.private_digest() == before
            if g.info.get("fixtureFinished"):
                break
            cmd = (
                expected[kind]["commands"][step]
                if expected
                else dict(
                    viewer=g.actor.name,
                    command=choose(vs[SEATS.index(g.actor.name)], rng),
                )
            )
            accepted(g, cmd)
            commands.append(cmd)
            previous = vs
        assert g.info.get("fixtureFinished"), f"DIRECTED_UNFINISHED:{kind}"
        result[kind] = dict(rows=rows, commands=commands)
    assert {
        "prize",
        "face-up-prize",
        "replacement",
        "deck-search",
        "non-turn-actor",
    } <= coverage
    return result, coverage


def interleaved(cases, expected):
    games = [new(c) for c in cases]
    previous = [None] * len(games)
    restored = set()
    rng = random.Random(17)
    count = 0
    while any(not g.done for g in games):
        indices = [i for i, g in enumerate(games) if not g.done]
        rng.shuffle(indices)
        for i in indices:
            g = games[i]
            step = g.version
            row, vs = frame(g, previous[i], False)
            assert row == expected[i]["rows"][step], f"INTERLEAVE:{i}:{step}"
            if step >= 25 and i not in restored:
                games[i] = g = Adapter.replay(g.export_private_replay())
                restored.add(i)
            before = [x.private_digest() for x in games]
            accepted(g, expected[i]["commands"][step])
            count += 1
            previous[i] = vs
            assert all(
                x.private_digest() == before[j] for j, x in enumerate(games) if j != i
            ), "CROSS_MATCH_MUTATION"
            if g.done:
                assert frame(g, previous[i], False)[0] == expected[i]["rows"][g.version]
    return count


def shared_fingerprint():
    from ptcg.core.card_registry import registry

    plain = module("plain")
    return digest(
        dict(
            definitions=[plain.SPECS, plain.TRAINER_SPECS, plain.SPECIAL_ENERGY_SPECS],
            freshCards=[Adapter._card(registry.get(k)()) for k in registry.list_all()],
        )
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["legacy", "core", "resume"], required=True)
    p.add_argument("--bundle", type=Path)
    p.add_argument("--pin")
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--expected", type=Path)
    p.add_argument("--checkpoint", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    load(a)
    cases = json.loads(a.cases.read_text())
    baseline = json.loads(a.expected.read_text()) if a.expected else None
    if a.mode == "resume":
        cp = json.loads(a.checkpoint.read_text())
        g = Adapter.replay(cp["record"])
        i = cp["case"]
        for step in range(cp["step"], len(baseline["natural"][i]["rows"])):
            row, _ = frame(g, wire=False)
            old = baseline["natural"][i]["rows"][step]
            assert (row["views"], row["state"]) == (old["views"], old["state"])
            if not g.done:
                accepted(g, baseline["natural"][i]["commands"][step])
        assert g.done
        a.output.write_text(
            json.dumps(
                dict(
                    crossProcessResume="passed", case=i, start=cp["step"], end=g.version
                )
            )
        )
        return
    before = shared_fingerprint()
    results, cps, coverage = natural(cases, baseline["natural"] if baseline else None)
    direct, extra = scenes(cases, baseline["directed"] if baseline else None)
    coverage |= extra
    interleave = interleaved(cases, results) if factory else 0
    assert shared_fingerprint() == before, "SHARED_DEFINITIONS_MUTATED"
    output = dict(
        natural=results,
        directed=direct,
        checkpoints=cps,
        coverage=sorted(coverage),
        interleavedCommands=interleave,
        definitionsUnchanged=True,
        globalRngUnchanged=True,
        ruleReleaseId=factory.rule_release_id if factory else None,
    )
    a.output.write_text(json.dumps(output, ensure_ascii=False))
    print(
        json.dumps(
            dict(
                mode=a.mode,
                matches=len(results),
                commands=sum(len(r["commands"]) for r in results),
                directed=len(direct),
                checkpoints=len(cps),
                interleavedCommands=interleave,
                coverage=sorted(coverage),
            )
        )
    )


if __name__ == "__main__":
    main()
