from ptcg.core.action import PlayPokemonAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import PokemonPosition, PokemonRule
from ptcg.utils.utils import can_attach_tool
from packages.battle.runtime import view
from packages.rules.adapter import Adapter
from packages.rules.field_capacity import refresh, settle
from tests.cardpool.test_checkup import board
from tests.cardpool.test_stadiums import stadium
from test_effects import zone, drive
from test_rules import finish_setup


def test_area_zero_can_play_through_eight_and_projects_free_slots():
    game = Adapter(0)
    finish_setup(game)
    env = game.env
    state = env.gamestate
    player = next(p for p in env.get_players() if p.id == state.turn)
    player.active[0].pokemonRule = PokemonRule.TERA
    field = stadium("Area Zero Underdepths")
    field.playedFrom = player.id
    state.stadium = [field]
    basic = registry.get("P01-005")
    zone(player, "bench", [basic() for _ in range(5)])
    zone(player, "hand", [basic() for _ in range(4)])
    env.cur_available_actions = env.get_actions(state)

    assert view(game, player.id)["observation"]["self"]["benchCapacity"] == 8
    assert view(game, player.id)["observation"]["opponent"]["benchCapacity"] == 5
    for count in (6, 7, 8):
        play = next(
            action for action in env.cur_available_actions
            if isinstance(action, PlayPokemonAction)
            and action.position == PokemonPosition.BENCH
        )
        game.obs, _, game.done, game.info = env.step(play)
        own = view(game, player.id)["observation"]["self"]
        assert len(own["bench"]) == count
        assert own["benchCapacity"] == 8

    assert not any(
        isinstance(action, PlayPokemonAction)
        and action.position == PokemonPosition.BENCH
        for action in env.cur_available_actions
    )


def test_area_zero_expands_to_eight_and_removed_stadium_owner_discards_first():
    s,p,o=board()
    for player in (p,o):
        player.active[0].pokemonRule=PokemonRule.TERA
        zone(player,"bench",[registry.get("P01-005")() for _ in range(8)])
    field=stadium("Area Zero Underdepths")
    field.playedFrom=o.id
    s.stadium=[field]
    refresh(s)
    assert p.benchSize==o.benchSize==8
    s.stadium=[]
    prizes=(len(p.prize),len(o.prize))
    pauses=drive(settle(s))
    assert [x['raw_available_actions'][0].playerId for x in pauses]==[o.id,p.id]
    assert len(p.bench)==len(o.bench)==5
    assert (len(p.prize),len(o.prize))==prizes


def test_glimmora_smaller_cap_wins_and_recovers_on_suppression():
    s,p,o=board()
    p.active[0].pokemonRule=PokemonRule.TERA
    field=stadium("Area Zero Underdepths");field.playedFrom=p.id;s.stadium=[field]
    o.active[0].spec={"abilities":[{"kind":"field_capacity","benchLimit":3,"activeOnly":True}]}
    zone(p,"bench",[registry.get("P01-005")() for _ in range(8)])
    drive(settle(s))
    assert len(p.bench)==3 and p.benchSize==3
    o.active[0].ability_blocked_turn=s.turn_number
    refresh(s)
    assert p.benchSize==8


def test_tool_capacity_discard_is_owner_choice_and_does_not_discard_energy():
    s,p,o=board()
    holder=p.active[0]
    holder.spec={"abilities":[{"kind":"field_capacity","toolLimit":4}]}
    tools=[registry.get("TEF-159")() for _ in range(4)]
    energy=registry.get("SVE-008")()
    holder.attachment=tools+[energy]
    refresh(s)
    assert not can_attach_tool(holder)
    holder.attachment.pop(0)
    assert can_attach_tool(holder)
    holder.ability_blocked_turn=s.turn_number
    drive(settle(s))
    assert energy in holder.attachment and len(holder.attachment)==2
    assert len(p.discard)>=2 and not can_attach_tool(holder)
