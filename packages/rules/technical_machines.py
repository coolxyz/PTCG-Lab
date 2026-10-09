"""Printed Tool attacks use the physical holder and its energy modifiers."""
from types import SimpleNamespace
from ptcg.core.attack import Attack
from ptcg.core.action import AttackAction
from ptcg.core.enums import CardType
from ptcg.utils.utils import current_player, opponent_player
from packages.rules.core_fixes import check_energy
from packages.rules.modifiers import effective_attack_cost


def initialize(tool):
    definition = tool.spec["mechanic"].get("grantedAttack")
    if definition:
        a = Attack({**definition, "cost": [CardType[t] for t in definition["cost"]]})
        a.compiled_rule = definition
        tool.attacks = [a]


def actions(tool, state):
    from packages.rules.tool_effects import enabled
    from packages.rules.attack_restrictions import allowed
    p, o = current_player(state), opponent_player(state)
    if not enabled(state) or not o.active or p.firstTurn and p.id == state.starting_player:
        return []
    holder = next((c for c in p.active if tool in c.attachment), None)
    if not holder:
        return []
    attack = tool.attacks[0]
    cost = effective_attack_cost(holder, attack, attack.cost, state)
    if not check_energy(cost, holder.energy) or not allowed(holder, attack, state):
        return []
    action = AttackAction(p.id, holder, attack, o.active[0])
    action.attack.cost, action.effect_source = cost, tool
    return [action]


def resolve(tool, action, state):
    from packages.rules.plain import PlainPokemon
    proxy = SimpleNamespace(spec={"attacks": [tool.spec["mechanic"]["grantedAttack"]]}, attacks=tool.attacks)
    proxy.resolve_mechanic = lambda r, a, s: PlainPokemon.resolve_mechanic(proxy, r, a, s)
    proxy.after_damage = lambda a, s: PlainPokemon.after_damage(proxy, a, s)
    yield from PlainPokemon.reduce_action(proxy, action, state)
