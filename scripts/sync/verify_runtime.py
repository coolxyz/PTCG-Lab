"""Verify a frozen candidate with real engine assertions and version-bound A1 games."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys
import time
from functools import lru_cache


def initialize(root):
    sys.path.insert(0, str(Path(root).resolve()))
    from packages.battle import runtime  # noqa: F401


@lru_cache(maxsize=1)
def generated_decks():
    from packages.collection.domain import CARDS, validation
    from packages.rules.plain import SPECS
    from packages.cardpool.scope import includes
    from packages.simulation.registry import EFFECTS
    from packages.collection.domain import AS_OF
    legal = [c for c in CARDS.values() if c['effectStatus']=='verified' and c.get('sourceVerified') and includes(c) and c.get('releasedAt') and c['releasedAt']<=AS_OF and c.get('engineLine') and c['engineId'] in EFFECTS and c['printingId'] in EFFECTS[c['engineId']]['printings']]
    by_effect = {c['engineId']:c for c in legal}
    specs={s['effectKey']:s for s in SPECS}
    energy={c['basicEnergyType']:c for c in legal if c.get('basicEnergyType') in ('grass','fire','water','lightning','psychic','fighting','darkness','metal')}
    assert len(energy)==8, 'Eight source-owned basic energies are required'
    translated={'GRASS':'grass','FIRE':'fire','WATER':'water','LIGHTNING':'lightning','PSYCHIC':'psychic','FIGHTING':'fighting','DARK':'darkness','METAL':'metal'}
    proof_path=Path(__import__('packages.collection.domain',fromlist=['ROOT']).ROOT)/'data/sync/reviewed-equivalence.json'
    reviewed={p['effectKey'] for p in json.loads(proof_path.read_text(encoding='utf-8'))['proofs'].values()} if proof_path.exists() else set()
    requested=sorted(k for k in by_effect if k in reviewed or k.startswith('P4P-SYNC'))
    if not requested:
        requested=sorted(k for k,c in by_effect.items() if c.get('isBasicPokemon'))[:2]
    decks = []
    deferred = []
    by_name = {}
    for c in legal:
        by_name.setdefault(c['engineLine'].rsplit(' ',2)[0], []).append(c)
    class MissingPredecessor(Exception): pass
    for key in requested:
        card=by_effect[key]; counts={}; names=set(); ace=False; ancestors=set()
        def add(c, quantity=4):
            nonlocal ace
            if c['nameLimitKey'] in names or c.get('aceSpec') and ace:return
            amount=min(1 if c.get('aceSpec') else quantity,60-sum(counts.values()))
            if amount<=0:return
            counts[c['printingId']]=amount;names.add(c['nameLimitKey']);ace=ace or bool(c.get('aceSpec'))
        def lineage(c):
            k=c['engineId']
            if k in ancestors:return
            ancestors.add(k);add(c)
            from ptcg.core.card_registry import registry
            predecessors=specs[k].get('evolvesFrom',[]) if k in specs else getattr(registry.get(k)(),'evolveFrom',[])
            for previous in predecessors:
                matches=by_name.get(previous, [])
                if not matches: raise MissingPredecessor(previous)
                lineage(matches[0])
        try:
            lineage(card)
        except MissingPredecessor as exc:
            deferred.append({'effectKey':key,'printingId':card['printingId'],'missingPredecessor':str(exc)})
            continue
        for anchor in ('MEW-151','P01-005','P01-006'):
            if anchor in by_effect:add(by_effect[anchor])
        if not any(CARDS[pid].get('isBasicPokemon') for pid in counts):
            add(next(c for c in legal if c.get('isBasicPokemon')))
        types=sorted({t for a in specs.get(key,{}).get('attacks',card.get('attacks',[])) for t in a.get('cost',[]) if t in translated}) or ['METAL']
        for i in range(min(22,60-sum(counts.values()))):
            pid=energy[translated[types[i%len(types)]]]['printingId']
            counts[pid]=counts.get(pid,0)+1
        for cn in ('博士的研究','奇树','巢穴球','高级球','派帕','夜间担架','超级钓竿','老大的指令','紧急滑板'):
            matches=[c for c in legal if c['cnName']==cn]
            if matches:add(matches[0])
        pid=energy[translated[types[0]]]['printingId']
        counts[pid]=counts.get(pid,0)+60-sum(counts.values())
        entries = [{"printingId": k, "quantity": n} for k, n in counts.items()]
        check = validation(entries)
        assert check["playable"], (key, check)
        decks.append({"effect": key, "entries": entries})
    assert decks, 'No complete legal source-owned deck is available'
    generated_decks.deferred = deferred
    return decks


def batch(indices):
    from packages.battle.runtime import Adapter, view
    from packages.battle.decision import decide
    from packages.battle.service import MatchService
    from packages.rules.invariants import check_conservation
    decks = generated_decks()
    rows = []
    for index in indices:
        new = decks[index % len(decks)]
        old = decks[(index+17) % len(decks)]['entries']
        left, right = (new["entries"], old) if index % 2 else (old, new["entries"])
        row = {"seed": 810000 + index, "effect": new["effect"], "status": "truncated", "fallbacks": 0, "actions": {}}
        counts = Counter()
        try:
            game = Adapter(row["seed"], MatchService._lines(left), MatchService._lines(right))
            for _ in range(2000):
                if game.done:
                    row["status"] = "finished"
                    break
                dto = view(game, game.actor)
                command, fallback = decide(dto)
                row["fallbacks"] += int(fallback)
                if dto["decision"]["kind"] != "selection":
                    option = next(o for o in dto["decision"]["options"] if o["id"] == command["choice"]["optionId"])
                    if option.get("sourceId"):
                        counts[option["sourceId"] + ":" + option["actionType"]] += 1
                game.submit(game.actor, command)
                check_conservation(game.env.gamestate)
            row["steps"] = game.version
            # Deterministic recovery of the actual final state, including new cards.
            if index % 50 == 0:
                recovered = Adapter.replay(game.export_private_replay())
                assert recovered.private_digest() == game.private_digest()
                row["replayed"] = True
        except Exception as e:
            row.update(status="error", error=repr(e))
        row["actions"] = dict(counts)
        rows.append(row)
    return rows


def directed():
    from packages.rules.plain import SPECS
    from ptcg.core.card_registry import registry
    from ptcg.core.action import AttackAction
    from ptcg.core.enums import PokemonPosition, CardType
    from packages.battle.runtime import Adapter
    from packages.rules.maximum_hp import maximum
    from packages.rules.healing import value
    from packages.rules.plain import PlainPokemon
    from packages.battle.decision import decide
    from packages.battle.runtime import view
    from ptcg.core.enums import CardPosition
    from ptcg.utils.utils import current_player, opponent_player
    assertions = 0
    checked = []
    for spec in SPECS:
        if not spec["effectKey"].startswith("P4P-SYNC"):
            continue
        card = registry.get(spec["effectKey"])()
        assert card.hp == spec["hp"] and card.prize == 1
        for attack, a in zip(card.attacks, spec["attacks"]):
            assert attack.damage == a["damage"]
            assert [c.name for c in attack.cost] == a["cost"]
            assert attack.compiled_rule == a
        for attack_index, a in enumerate(spec["attacks"]):
            for multiplier, resistance in ((1, 0), (2, 0), (1, 30)):
                game = Adapter(0)
                while game.env.start_stage:
                    game.submit(game.actor, decide(view(game, game.actor))[0])
                state = game.env.gamestate
                player, opponent = current_player(state), opponent_player(state)
                player.firstTurn = opponent.firstTurn = False
                player.bench = []; opponent.bench = []; state.stadium = []
                source = registry.get(spec["effectKey"])()
                target_spec = {**spec, "effectKey": "P4P-SYNCTARGET", "name": "Directed target", "hp": 10000, "weakness": [], "resistance": []}
                target = type("DirectedTarget", (PlainPokemon,), {"spec": target_spec})()
                for owner, c in ((player, source), (opponent, target)):
                    owner.active = [c]; c.index = 1; c.position = PokemonPosition.ACTIVE; c.cardPosition = CardPosition.ACTIVE
                target.weakness = [source.cardType] if multiplier == 2 else []
                target.resistance = [source.cardType] if resistance else []
                source.energy = []
                if a["cost"]:
                    assert not any(isinstance(x, AttackAction) and x.attack_template is source.attacks[attack_index] for x in source.get_actions(state))
                source.energy = [CardType[t] for t in a["cost"]]
                action = next(x for x in source.get_actions(state) if isinstance(x, AttackAction) and x.attack_template is source.attacks[attack_index])
                mechanic = a.get("mechanic", {})
                source.hp = max(10, spec["hp"] - 30)
                old_hp = source.hp
                if mechanic.get("cure"):
                    source.poisoned = True
                generator = source.reduce_action(action, state)
                try:
                    item = next(generator)
                    while True:
                        item = generator.send(list(item[3]["raw_available_actions"])[-1])
                except StopIteration:
                    pass
                assert target.hp == 10000 - max(0, a["damage"] * multiplier - resistance), (spec["effectKey"], attack_index, target.hp)
                if mechanic:
                    assert source.hp == min(spec["hp"], old_hp + mechanic.get("amount", 0))
                    assert not mechanic.get("cure") or not getattr(source, "poisoned", False)
                assertions += 1
        checked.append(spec["effectKey"])
    # Deck admissibility executes the real policy and release registry.
    decks = generated_decks()
    from packages.collection.domain import CARDS
    from packages.cardpool.scope import includes
    admitted={c['engineId'] for c in CARDS.values() if c['sourceVerified'] and c['effectStatus']=='verified' and includes(c)}
    assert set(checked)&admitted <= {d['effect'] for d in decks}|{d['effectKey'] for d in generated_decks.deferred}
    return {"compiledEffects": len(checked), "legalDecks": len(decks), "effectKeys": checked, "executedAttackCases": assertions, "deferredEvolutionDecks": generated_decks.deferred, "inactiveHistoricalEffects": sorted(set(checked)-admitted)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    initialize(args.root)
    start = time.monotonic()
    assertions = directed()
    results = []
    with ProcessPoolExecutor(args.workers, initializer=initialize, initargs=(args.root,)) as pool:
        for rows in pool.map(batch, [list(range(i, min(i + 10, args.games))) for i in range(0, args.games, 10)]):
            results.extend(rows)
            print(len(results), dict(Counter(r["status"] for r in results)), flush=True)
    from packages.battle.runtime import ENGINE_VERSION
    from packages.simulation.registry import VERSION
    from packages.collection.domain import CATALOG_VERSION
    from packages.battle.agent import VERSION as AI_VERSION
    statuses = dict(Counter(r["status"] for r in results))
    report = {"engineVersion": ENGINE_VERSION, "effectVersion": VERSION, "catalogVersion": CATALOG_VERSION, "aiVersion": AI_VERSION, "directed": assertions, "games": len(results), "seconds": time.monotonic() - start, "statuses": statuses, "results": results, "passed": len(results) == args.games and statuses.get("finished", 0) / max(1, args.games) >= .995 and not statuses.get("error", 0)}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

