"""Generate an isolated source overlay. Never modifies the P0 checkout."""
from pathlib import Path
import shutil, subprocess, hashlib, json
ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT/'vendor/engine-upstream'
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=UPSTREAM,text=True).strip() == '92c3cc4fe85a26f102d7bb6b3e8be7678512d2e5'
assert not subprocess.check_output(['git','diff','HEAD','--','src'],cwd=UPSTREAM,text=True).strip()
DEST = ROOT/'runtime/engine'
shutil.copytree(UPSTREAM/'src/ptcg',DEST/'ptcg',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
shutil.copy2(UPSTREAM/'LICENSE',DEST/'UPSTREAM-LICENSE')
p=DEST/'ptcg/core/enums.py'
s=p.read_text(encoding='utf8').replace('    FAIRY = 13', '    FAIRY = 13\n    PSYCHIC_DARK = 14')
p.write_text(s, encoding='utf8', newline='\n')
p=DEST/'ptcg/utils/utils.py'
s=p.read_text(encoding='utf-8')
s=s.replace("def flip_coin(state: State | None = None) -> Coin:", "def flip_coin(state: State | None = None, player=None, *, during_turn=True) -> Coin:")
s=s.replace('    if state is not None:\n        side = "HEADS"', '    if state is not None:\n        owner = player or current_player(state)\n        if during_turn and not getattr(state, "resolving_checkup", False) and getattr(owner, "all_coin_tails_turn", None) == state.turn_number:\n            result = Coin.TAIL\n        side = "HEADS"')
s+='''\n# P0.1 scoped replacements. See packages/rules/core_fixes.py.\nfrom packages.rules.core_fixes import check_energy, discard_card, discard_pokemon, end_turn_tools\n_upstream_next_turn = next_turn\ndef next_turn(state):\n    end_turn_tools(state)\n    return _upstream_next_turn(state)\nRULES_OVERLAY = True\n'''
p.write_text(s, encoding='utf-8', newline='\n')
p.write_text(p.read_text(encoding='utf-8')+'''\n_upstream_move_pokemon = move_pokemon\ndef move_pokemon(player, pokemon):\n    result = _upstream_move_pokemon(player, pokemon)\n    for card in player.bench:\n        if hasattr(card, 'special_condition'): del card.special_condition\n    return result\n_upstream_switch_pokemon = switch_pokemon\ndef switch_pokemon(a, b, player):\n    result = _upstream_switch_pokemon(a, b, player)\n    for card in player.bench:\n        if hasattr(card, 'special_condition'): del card.special_condition\n    return result\n''', encoding='utf-8', newline='\n')
p=DEST/'ptcg/core/reducer.py'
s=p.read_text(encoding='utf-8')
old='    state: State,\n) -> StepGenerator:\n    """Handle Pokemon knockout:'
assert s.count(old)==1
s=s.replace(old,'    state: State,\n    *, attack_damage: bool = False,\n) -> StepGenerator:\n    """Handle Pokemon knockout:')
old='    # Allow on_knocked_out tool effects to run before the pokemon is discarded'
s=s.replace(old,'    if attack_damage:\n        from packages.rules.core_fixes import exp_share\n        yield from exp_share(target, attacker, opponent, state)\n\n'+old)
# Only attack-damage path, not damage-counter/recoil knockouts.
start=s.index('def reduce_attack_damage(');end=s.index('\ndef ',start+5)
part=s[start:end].replace('_handle_knockout(target, player, opponent, state)', '_handle_knockout(target, player, opponent, state, attack_damage=True)')
s=s[:start]+part+s[end:]
# Evolution keeps existing damage counters; special conditions do not carry over.
old = '    evolved_card.energy = evolving_card.energy.copy()'
assert s.count(old) == 1
s = s.replace(old, '    from packages.rules.maximum_hp import maximum\n    evolved_card.hp -= max(0, maximum(evolving_card, state) - evolving_card.hp)\n' + old)
p.write_text(s, encoding='utf-8', newline='\n')
# Registry discovery requires classes defined in the scanned module.
p=DEST/'ptcg/core/card_registry.py'
s=p.read_text(encoding='utf-8')
old='str(rel.with_suffix("")).replace("/", ".")'
assert s.count(old) == 1
p.write_text(s.replace(old, '".".join(rel.with_suffix("").parts)'), encoding='utf-8', newline='\n')
p=DEST/'ptcg/cards/P01'
p.mkdir(exist_ok=True)
(p/'__init__.py').write_text('')
names=('Tatsugiri','ExpShare','NeoUpperEnergy','LuminousEnergy','GimmighoulSV7a','DunsparceSV2P')
(p/'additions.py').write_text('from packages.rules.additions import '+', '.join(names)+'\n'+''.join(f'class Local{n}({n}):\n    pass\n' for n in names), encoding='utf-8', newline='\n')
p=DEST/'ptcg/cards/P4'
p.mkdir(exist_ok=True)
(p/'__init__.py').write_text('', encoding='utf-8')
(p/'plain.py').write_text('from packages.rules.plain import install\ninstall(globals())\n', encoding='utf-8', newline='\n')
# Attack effects resolve after damage but before knockout/prize processing.
p=DEST/'ptcg/core/reducer.py'
s=p.read_text(encoding='utf-8')
old='    apply_weakness_resistance: bool = True,\n'
assert s.count(old)==1
s=s.replace(old, old+'    after_damage=None,\n')
old='    if target.hp > damage:\n        target.hp -= damage\n    else:\n        yield from _handle_knockout(target, player, opponent, state, attack_damage=True)'
assert s.count(old)==1
s=s.replace(old, '    knocked_out = target.hp <= damage\n    target.hp -= damage\n    if after_damage is not None:\n        yield from after_damage(action, state)\n    if knocked_out:\n        yield from _handle_knockout(target, player, opponent, state, attack_damage=True)')
p.write_text(s, encoding='utf-8', newline='\n')
p=DEST/'ptcg/core/player.py'
s=p.read_text(encoding='utf-8')
old='if check_energy(card.retreat, card.energy) and len(self.bench) != 0:'
assert s.count(old)==1
s=s.replace(old, 'if getattr(card, "retreat_blocked_turn", None) != state.turn_number and check_energy(card.retreat, card.energy) and len(self.bench) != 0:')
p.write_text(s, encoding='utf-8', newline='\n')
p=DEST/'ptcg/utils/utils.py'
s=p.read_text(encoding='utf-8')
old="        if hasattr(card, 'special_condition'): del card.special_condition"
assert s.count(old)==2
s=s.replace(old,old+"\n        if hasattr(card, 'retreat_blocked_turn'): del card.retreat_blocked_turn\n        if hasattr(card, 'attack_locks'): del card.attack_locks\n        if hasattr(card, 'attack_protection'): del card.attack_protection\n        if hasattr(card, 'timed_modifiers'): del card.timed_modifiers\n        if hasattr(card, 'attack_coin_check'): del card.attack_coin_check\n        if hasattr(card, 'evolution_blocked_turn'): del card.evolution_blocked_turn\n        if hasattr(card, 'sleep_coins'): del card.sleep_coins\n        if hasattr(card, 'confusion_damage'): del card.confusion_damage")
p.write_text(s, encoding='utf-8', newline='\n')
# Apply attack-only protection after weakness/resistance, including bench damage.
p=DEST/'ptcg/core/reducer.py'
s=p.read_text(encoding='utf-8')
old='        else action.attack.damage\n'
assert s.count(old)==1
s=s.replace(old,'        else shield_damage(target, action.attack.damage, state)\n')
s+='''\nfrom packages.rules.core_fixes import shield_damage\n_unshielded_damage = _calculate_damage\ndef _calculate_damage(source, target, base_damage, state):\n    return shield_damage(target, _unshielded_damage(source, target, base_damage, state), state)\n'''
p.write_text(s, encoding='utf-8', newline='\n')
p=DEST/'ptcg/utils/utils.py'
s=p.read_text(encoding='utf-8')
old="        if hasattr(card, 'retreat_blocked_turn'): del card.retreat_blocked_turn"
assert s.count(old)==2
s=s.replace(old,old+"\n        if hasattr(card, 'damage_shield'): del card.damage_shield")
p.write_text(s, encoding='utf-8', newline='\n')
old="        if hasattr(card, 'damage_shield'): del card.damage_shield"
assert s.count(old)==2
s=s.replace(old,old+"\n        if hasattr(card, 'attack_blocked_turn'): del card.attack_blocked_turn")
p.write_text(s, encoding='utf-8', newline='\n')
p=DEST/'ptcg/core/reducer.py'
s=p.read_text(encoding='utf-8')
old='    if knocked_out:\n        yield from _handle_knockout(target, player, opponent, state, attack_damage=True)'
assert s.count(old)==1
s=s.replace(old, '''    from packages.rules.maximum_hp import reconcile, affected
    reconcile(state)
    if source.hp <= 0 or hasattr(action, 'group_damage_targets') or affected(state):
        from packages.rules.knockouts import resolve_group
        yield from resolve_group(state, damage_targets=getattr(action, 'group_damage_targets', [target] if knocked_out else []))
    elif knocked_out:
        yield from _handle_knockout(target, player, opponent, state, attack_damage=True)''')
p.write_text(s, encoding='utf-8', newline='\n')
p=DEST/'ptcg/utils/utils.py'
s=p.read_text(encoding='utf-8')
old='    if state.termination_reason == "deck_out" and state.termination_loser is not None:'
assert s.count(old)==1
s=s.replace(old, '    if state.termination_reason == "simultaneous_knockout":\n        return True, state.group_winner\n\n'+old)
p.write_text(s, encoding='utf-8', newline='\n')
# Checkup may require prize/replacement choices, so defer it to the engine.
s += '''\n_complete_next_turn = next_turn
def next_turn(state):
    from packages.rules.attack_attachments import restore
    restore(state)
    if getattr(state, 'copy_attack_pending', False):
        return
    if getattr(state, 'festival_attack_pending', False):
        return
    if getattr(state, 'field_event_queue', []) or getattr(state, 'hand_event_queue', []):
        state.pending_event_turn = True
        return
    from packages.rules.maximum_hp import reconcile
    if reconcile(state):
        state.pending_hp_turn = True
        return
    from packages.rules.tool_effects import end_turn
    from packages.rules.end_phase import needed as end_phase_needed
    if end_phase_needed(state):
        state.pending_end_phase = True
        return
    end_turn(state)
    from packages.rules.checkup import needed
    if needed(state):
        state.pending_checkup = True
        return
    return _complete_next_turn(state)
'''
old="        if hasattr(card, 'attack_blocked_turn'): del card.attack_blocked_turn"
assert s.count(old)==2
s=s.replace(old,old+"\n        if hasattr(card, 'poisoned'): del card.poisoned\n        if hasattr(card, 'burned'): del card.burned")
p.write_text(s, encoding='utf-8', newline='\n')
p=DEST/'ptcg/core/reducer.py'
s=p.read_text(encoding='utf-8')
s=s.replace('    after_damage=None,\n', '    after_damage=None,\n    ignore_effects=False,\n    ignore_resistance=False,\n    ignore_weakness=False,\n')
old='''    damage = (
        _calculate_damage(source, target, action.attack.damage, state)
        if apply_weakness_resistance
        else shield_damage(target, action.attack.damage, state)
    )'''
assert s.count(old)==1
s=s.replace(old, '''    if ignore_weakness:
        raw = attack_damage(source,target,action.attack.damage,state)
        if source.cardType in target.resistance:
            raw -= (getattr(target,'spec',None) or {}).get('resistanceAmount',30)
        damage = shield_damage(target,max(0,raw),state,source)
    elif ignore_resistance:
        raw = action.attack.damage * (2 if source.cardType in target.weakness else 1)
        damage = shield_damage(target, raw, state)
    elif ignore_effects:
        damage = _unshielded_damage(source, target, action.attack.damage, state) if apply_weakness_resistance else action.attack.damage
    else:
        damage = (
            _calculate_damage(source, target, action.attack.damage, state)
            if apply_weakness_resistance
            else shield_damage(target, action.attack.damage, state)
        )''')
p.write_text(s, encoding='utf-8', newline='\n')
old='    knocked_out = target.hp <= damage\n    target.hp -= damage\n'
assert s.count(old)==1
s=s.replace(old,old+'    action.damage_dealt = damage\n')
s=s.replace('    action.damage_dealt = damage\n', '    action.damage_dealt = damage\n    if knocked_out and damage > 0 and getattr(action, "bonus_prizes", 0):\n        target.knockout_bonus_prizes = action.bonus_prizes\n')
s=s.replace('    discard_pokemon(opponent, target)\n', '    prize_reward = target.prize + getattr(target, "knockout_bonus_prizes", 0)\n    discard_pokemon(opponent, target)\n')
s=s.replace('prize_count = min(len(attacker.prize), target.prize)', 'prize_count = min(len(attacker.prize), prize_reward)')
s=s.replace('shield_damage(target, _unshielded_damage(source, target, base_damage, state), state)', 'shield_damage(target, _unshielded_damage(source, target, attack_damage(source, target, base_damage, state), state), state, source)')
s=s.replace('raw = action.attack.damage * (2 if source.cardType in target.weakness else 1)', 'raw = attack_damage(source, target, action.attack.damage, state) * (2 if source.cardType in target.weakness else 1)')
s=s.replace('damage = shield_damage(target, raw, state)', 'damage = shield_damage(target, raw, state, source)')
s=s.replace('_unshielded_damage(source, target, action.attack.damage, state) if apply_weakness_resistance else action.attack.damage', '_unshielded_damage(source, target, attack_damage(source, target, action.attack.damage, state), state) if apply_weakness_resistance else attack_damage(source, target, action.attack.damage, state)')
s=s.replace('else shield_damage(target, action.attack.damage, state)', 'else shield_damage(target, attack_damage(source, target, action.attack.damage, state), state, source)')
s+='\nfrom packages.rules.modifiers import attack_damage\n'
s = s.replace(
    'return shield_damage(target, _unshielded_damage(source, target, attack_damage(source, target, base_damage, state), state), state, source)',
    'from packages.rules.modifiers import ignore_target_effects\n    raw = _unshielded_damage(source, target, attack_damage(source, target, base_damage, state), state)\n    return raw if ignore_target_effects(source, target, state) else shield_damage(target, raw, state, source)'
)
prize_start = s.index('    attacker.reward.apply_prize_card_reward(prize_count)')
prize_end = s.index('    # Record prize card event', prize_start)
s = s[:prize_start] + '    from packages.rules.prizes import take\n    yield from take(attacker, prize_count, state)\n\n' + s[prize_end:]
s += '''
_single_handle_knockout = _handle_knockout
def _handle_knockout(target, attacker, opponent, state, *, attack_damage=False):
    from packages.rules.maximum_hp import affected
    if affected(state):
        from packages.rules.knockouts import resolve_group
        target.hp = min(0, target.hp)
        yield from resolve_group(state, damage_targets=[target] if attack_damage else [])
    else:
        yield from _single_handle_knockout(target, attacker, opponent, state, attack_damage=attack_damage)
'''
s = s.replace('    trigger_attack_abilities(action, state)', '    from packages.rules.maximum_hp import reconcile\n    reconcile(state)\n    trigger_attack_abilities(action, state)')
s = s.replace('    target.hp -= damage\n', '    from packages.rules.damage_events import deal\n    deal(source, target, damage, state)\n', 1)
s = s.replace('    action.damage_dealt = damage\n', '    action.damage_dealt = int(damage)\n')
s = s.replace('source.cardType in target.weakness', 'weak_to(source, target, state)')
s += '\nfrom packages.rules.tool_effects import weak_to\n'
s = s.replace('    if source.hp <= 0 or hasattr(action,', '    from packages.rules.attack_attachments import restore\n    restore(state)\n    from packages.rules.damage_events import finish\n    reacted = finish(state)\n    knocked_out = target.hp <= 0\n    if reacted or source.hp <= 0 or hasattr(action,')
p.write_text(s, encoding='utf-8', newline='\n')
p=DEST/'ptcg/utils/utils.py'
s=p.read_text(encoding='utf-8')
s=s.replace("if hasattr(card, 'poisoned'): del card.poisoned", "if hasattr(card, 'poisoned'): del card.poisoned\n        if hasattr(card, 'poison_damage'): del card.poison_damage")
s=s.replace("if hasattr(card, 'poison_damage'): del card.poison_damage", "if hasattr(card, 'poison_damage'): del card.poison_damage\n        if hasattr(card, 'attack_damage_reduction'): del card.attack_damage_reduction\n        if hasattr(card, 'damage_retaliation'): del card.damage_retaliation")
for attr in ("energy_hand_blocked_turn", "attachment_ends_turn", "delayed_prize_bonus", "future_team_shield"):
    s=s.replace("if hasattr(card, 'damage_shield'): del card.damage_shield", f"if hasattr(card, 'damage_shield'): del card.damage_shield\n        if hasattr(card, '{attr}'): del card.{attr}")
s=s.replace("if hasattr(card, 'damage_shield'): del card.damage_shield", "if hasattr(card, 'damage_shield'): del card.damage_shield\n        if hasattr(card, 'weakness_override'): del card.weakness_override")
s=s.replace("if hasattr(card, 'damage_retaliation'): del card.damage_retaliation", "if hasattr(card, 'damage_retaliation'): del card.damage_retaliation\n        if hasattr(card, 'turn_damage_bonus'): del card.turn_damage_bonus\n        if hasattr(card, 'delayed_attacks'): del card.delayed_attacks")
s += '''
def can_attach_tool(pokemon):
    from ptcg.core.card import ToolCard
    return sum(isinstance(c,ToolCard) for c in pokemon.attachment) < getattr(pokemon,'tool_capacity',1)

def is_active_ability_suppressed(player, state):
    from packages.rules.suppression import enabled
    return bool(player.active) and not enabled(player.active[0], state)

_move_cards_without_energy_triggers = move_cards
def move_cards(cards, source_pos, target_pos, state):
    selected = list(cards) if isinstance(cards, (list, tuple)) else [cards]
    from packages.rules.zone_guards import allows
    selected = [c for c in selected if allows(c, source_pos, target_pos, state)]
    cards = selected
    if target_pos[1] in (CardPosition.ACTIVE_ATTACHMENT, CardPosition.BENCH_ATTACHMENT):
        from packages.rules.turn_interrupts import attachment_allowed
        from ptcg.core.card import EnergyCard
        owner = state.player1 if target_pos[0] == state.player1.id else state.player2
        pool = owner.active if target_pos[1] == CardPosition.ACTIVE_ATTACHMENT else owner.bench
        target = next((c for c in pool if c.index == target_pos[2]), None)
        if target is not None and not attachment_allowed(target, source_pos[1], state):
            selected = [c for c in selected if not isinstance(c, EnergyCard)]
            cards = selected
    if target_pos[1] in (CardPosition.ACTIVE, CardPosition.BENCH):
        from packages.rules.pokemon_replacement import permitted
        from ptcg.core.card import PokemonCard
        selected = [c for c in selected if not isinstance(c, PokemonCard) or permitted(c)]
        cards = selected
    if source_pos[1] == CardPosition.PRIZE or target_pos[1] == CardPosition.PRIZE:
        for card in selected:
            if hasattr(card, "prize_face_up"):
                del card.prize_face_up
    result = _move_cards_without_energy_triggers(cards, source_pos, target_pos, state)
    if target_pos[1] in (CardPosition.ACTIVE_ATTACHMENT, CardPosition.BENCH_ATTACHMENT):
        from packages.rules.special_energy import attached
        attached(selected, source_pos, target_pos, state)
    return result
'''
p.write_text(s, encoding='utf-8', newline='\n')
p=DEST/'ptcg/core/ability_handler.py'
s=p.read_text(encoding='utf-8')
old='    handler = getattr(card, "use_ability", None)'
assert s.count(old) == 1
s=s.replace(old, '    from ptcg.core.card import PokemonCard\n    from packages.rules.suppression import enabled\n    if isinstance(card, PokemonCard) and not enabled(card, state):\n        return\n'+old)
s=s.replace(old, '    from ptcg.core.card import ToolCard\n    from packages.rules.tool_effects import enabled as tools_enabled\n    if isinstance(card, ToolCard) and not tools_enabled(state):\n        return\n'+old)
p.write_text(s, encoding='utf-8', newline='\n')
p=DEST/'ptcg/cards/ASR/switch_cart.py'
s=p.read_text(encoding='utf-8')
old='active_pokemon.hp = min(active_pokemon.hp + 30, max_hp)'
assert s.count(old)==1
s=s.replace(old,'from packages.rules.healing import value as healed\n                active_pokemon.hp = healed(active_pokemon, 30, state, record=True)')
p.write_text(s,encoding='utf-8',newline='\n')
# Capture public physical-card transitions for cross-turn card text.
p=DEST/'ptcg/core/state.py'
s=p.read_text(encoding='utf-8')
s += """
_original_state_init = State.__init__
def _init_with_rule_history(self, *args, **kwargs):
    _original_state_init(self, *args, **kwargs)
    self.player1.rules_state = self
    self.player2.rules_state = self
State.__init__ = _init_with_rule_history
"""
p.write_text(s,encoding='utf-8',newline='\n')
p=DEST/'ptcg/utils/utils.py'
s=p.read_text(encoding='utf-8')
s += """
_move_with_history = move_pokemon
def move_pokemon(player, pokemon):
    old = list(player.active)
    result = _move_with_history(player, pokemon)
    from packages.rules.history import moved
    moved(player, old)
    return result
_switch_with_history = switch_pokemon
def switch_pokemon(a, b, player):
    old = list(player.active)
    state = getattr(player, 'rules_state', None)
    if state is not None:
        from packages.rules.zone_guards import trainer_immune
        if trainer_immune(a, state) or trainer_immune(b, state):
            return
    result = _switch_with_history(a, b, player)
    from packages.rules.history import moved
    moved(player, old)
    return result
"""
p.write_text(s,encoding='utf-8',newline='\n')
p=DEST/'ptcg/core/reducer.py'
s=p.read_text(encoding='utf-8')
s += """
_evolve_with_history = reduce_evolve_pokemon_action
def reduce_evolve_pokemon_action(action, state):
    from packages.rules.pokemon_replacement import permitted
    if not permitted(action.source):
        return
    before = action.target.name
    from packages.rules.evolution_effects import retained_poison, restore_poison
    poison = retained_poison(action.target, state)
    _evolve_with_history(action, state)
    restore_poison(action.source, poison)
    action.source.evolved_turn = state.turn_number
    action.source.evolved_from_name = before
_single_knockout_with_history = _single_handle_knockout
def _single_handle_knockout(target, attacker, opponent, state, *, attack_damage=False):
    from packages.rules.history import knocked_out
    knocked_out(target, opponent, state, attack_damage)
    yield from _single_knockout_with_history(target, attacker, opponent, state, attack_damage=attack_damage)
"""
p.write_text(s,encoding='utf-8',newline='\n')
# Both types participate in weakness/resistance; apply resistance after weakness.
p=DEST/'ptcg/core/reducer.py'
s=p.read_text(encoding='utf-8')
s=s.replace('source.cardType in target.resistance', 'resisted(source, target, state)')
s=s.replace('(2 if weak_to(source, target, state) else 1)', '(weakness_multiplier(target, state) if weak_to(source, target, state) else 1)')
s += """
from packages.rules.pokemon_types import weakness_resistance, weakness_multiplier, resisted
def _unshielded_damage(source, target, base_damage, state):
    return weakness_resistance(source, target, base_damage, state)
"""
p.write_text(s,encoding='utf-8',newline='\n')
manifest={p.relative_to(DEST).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.rglob('*.py'), key=lambda p: p.relative_to(DEST).as_posix())}
(ROOT/'artifacts/engine/overlay-hashes.json').write_text(json.dumps(manifest,indent=2), encoding='utf-8', newline='\n')
print(DEST)
