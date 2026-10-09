"""Continuous Bench/Tool capacities, with owner-controlled overflow discards."""

from ptcg.core.card import ToolCard
from ptcg.core.enums import PokemonRule
from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions
from packages.rules.abilities import enabled


def refresh(state):
    from packages.rules.stadiums import rules
    stadium = next((c for c in state.stadium if (getattr(c,"spec",None) or {}).get("mechanic",{}).get("teraBench")),None)
    if stadium:
        state.bench_expansion_owner = stadium.playedFrom
    for p in (state.player1,state.player2):
        o = state.player2 if p is state.player1 else state.player1
        caps = [r["teraBench"] for r in rules(state) if r.get("teraBench") and any(c.pokemonRule == PokemonRule.TERA for c in p.active+p.bench)]
        caps += [r["benchLimit"] for c in o.active+o.bench if enabled(c,state) for r in (getattr(c,"spec",None) or {}).get("abilities",[]) if r["kind"] == "field_capacity" and r.get("benchLimit") and (not r.get("activeOnly") or c in o.active)]
        p.benchSize = min(caps) if caps else 5
        for c in p.active+p.bench:
            c.tool_capacity = max([1]+[r["toolLimit"] for r in (getattr(c,"spec",None) or {}).get("abilities",[]) if r["kind"] == "field_capacity" and r.get("toolLimit") and enabled(c,state)])


def settle(state):
    from packages.rules.core_fixes import discard_pokemon
    from packages.rules.zone_effects import discard_attached
    from packages.rules.maximum_hp import reconcile
    players = [state.player1,state.player2]
    players.sort(key=lambda p:p.id != getattr(state,"bench_expansion_owner",state.turn))
    while True:
        refresh(state)
        changed = False
        for p in players:
            excess = len(p.bench)-p.benchSize
            if excess>0:
                cards = yield from reduce_choose_card_actions(choose_card_actions(p.id,p.id,excess,excess,list(p.bench),indexed=True,tips="Discard Benched Pokémon to the current Bench limit."),state)
                for c in cards:
                    discard_pokemon(p,c)
                changed = True
            for target in p.active+p.bench:
                tools = [c for c in target.attachment if isinstance(c,ToolCard)]
                excess = len(tools)-target.tool_capacity
                if excess>0:
                    cards = yield from reduce_choose_card_actions(choose_card_actions(p.id,p.id,excess,excess,tools,indexed=True,source=target,tips="Discard excess Pokémon Tools."),state)
                    discard_attached(target,cards,p)
                    changed = True
        if not changed:
            break
        reconcile(state)
