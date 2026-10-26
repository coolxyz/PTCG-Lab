"""Public action announcements, never raw commands, prompts or engine logs.

Call only AFTER a command has been accepted by the rule worker. Card identities
from a private hand may be announced only for explicit, public card-play actions.
All choices/search candidates and all setup placements remain private here.
"""

from copy import deepcopy

PLAYS = {
    "PlayPokemonAction": "放置宝可梦",
    "EvolvePokemonAction": "进化",
    "AttachEnergyAction": "附加能量",
    "UseSupporterAction": "使用支援者",
    "UseItemAction": "使用物品",
    "UseToolAction": "附加道具",
    "PutStadiumAction": "放置竞技场",
}
PUBLIC = {
    **PLAYS,
    "AttackAction": "使用招式",
    "UseAbilityAction": "使用特性",
    "RetreatAction": "撤退",
    "UseStadiumAction": "使用竞技场效果",
    "DiscardStadiumAction": "移除竞技场",
    "PassTurn": "结束回合",
}


def coin_results(info):
    # These exact engine messages contain no private data. Do not forward any
    # other diagnostic text, or parse card names from unstructured engine logs.
    known = {"Coin flip: HEADS.": "heads", "Coin flip: TAILS.": "tails"}
    return [
        known[m]
        for m in info.get("auto_executed", [])
        if isinstance(m, str) and m in known
    ][:200]


def selection_source_ref(adapter, before):
    """Resolve the exact prompt object against public zones before execution.

    A matching name/printing ID alone is insufficient: a hidden duplicate could
    have the same identity. Never resolve a prompt from hand or deck contents.
    """
    decision = before.get("decision")
    if (
        before.get("phase") != "playing"
        or not decision
        or decision["kind"] != "selection"
    ):
        return None
    source = getattr(adapter.info.get("prompt"), "source", None)
    if source is None:
        return None
    for player in adapter.env.get_players():
        side = "self" if player.id.name == decision["actor"] else "opponent"
        for zone in ("active", "bench", "discard"):
            for obj, dto in zip(
                getattr(player, zone), before["observation"][side][zone]
            ):
                if obj is source:
                    return dto.get("ref")
    for obj, dto in zip(
        adapter.env.gamestate.stadium, before["observation"]["stadium"]
    ):
        if obj is source:
            return dto.get("ref")
    return None


def printed_identity(card):
    return {
        "cardId": card.get("id") or card["name"],
        "name": card["name"],
        "presentation": {
            k: deepcopy(card[k])
            for k in ("name", "id", "attacks", "abilities")
            if k in card
        },
    }


def action_events(before, actor, choice, coins=(), *, selection_source=None):
    decision = before.get("decision")
    if not decision or decision.get("actor") != actor:
        return []
    event = {"actor": actor, "kind": "action", "text": "完成一次效果选择"}
    if before.get("phase") != "playing":
        event.update(kind="setup", text="完成开局准备步骤")
    elif decision["kind"] == "selection" and selection_source:
        observation = before["observation"]
        stadium = observation["stadium"]
        public = stadium + [
            c
            for side in ("self", "opponent")
            for zone in ("active", "bench", "discard")
            for c in observation[side][zone]
        ]
        source = next((c for c in public if c.get("ref") == selection_source), None)
        if source:
            is_stadium = any(c.get("ref") == selection_source for c in stadium)
            event["text"] = (
                ("竞技场效果" if is_stadium else "卡牌效果")
                + " · "
                + source["name"]
                + " · 完成选牌"
            )
            event["cards"] = [printed_identity(source)]
    elif decision["kind"] == "options":
        option = next(
            (o for o in decision["options"] if o["id"] == choice.get("optionId")), None
        )
        if option is None:
            return []
        kind = option["actionType"]
        if kind == "NumberOption":
            event["text"] = "完成效果选项选择"
        elif kind not in PUBLIC:
            event["text"] = "完成对局操作"
        if kind in PUBLIC:
            event["text"] = PUBLIC[kind]
            observation = before["observation"]
            public = [
                c
                for side in ("self", "opponent")
                for zone in ("active", "bench")
                for c in observation[side][zone]
            ] + observation["stadium"]
            sources = public + (
                observation["self"].get("hand") or [] if kind in PLAYS else []
            )
            cards = []
            for role in ("source", "active_pokemon", "target"):
                # Refs disambiguate duplicates and ensure targets are public.
                ref = option.get(role + "Ref")
                card = next(
                    (
                        c
                        for c in (public if role == "target" else sources)
                        if ref and c.get("ref") == ref
                    ),
                    None,
                )
                if not card or any(
                    c["cardId"] == card.get("id", card["name"]) for c in cards
                ):
                    continue
                identity = printed_identity(card)
                cards.append(identity)
                event["text"] += (" → " if role == "target" else " · ") + card["name"]
            if kind == "AttackAction" and isinstance(option.get("attack"), dict):
                event["text"] += " · " + option["attack"].get("name", "招式")
            if kind == "UseAbilityAction" and isinstance(option.get("ability"), str):
                event["text"] += " · " + option["ability"]
            if cards:
                event["cards"] = cards
    if coins:
        event["coins"] = list(coins)
    event["text"] = event["text"][:1000]
    return [deepcopy(event)]
