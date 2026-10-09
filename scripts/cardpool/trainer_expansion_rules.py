"""Whole bilingual trainer effects using shared hand/deck operations."""

import json
from pathlib import Path

CLAUSES = json.loads(Path(__file__).with_name("trainer-additional-clauses.json").read_text(encoding="utf8"))


def compile_expansion(en, cn):
    for c in CLAUSES:
        if en == c["english"] and cn == c["chinese"]:
            return dict(c["rule"])
    pairs = [
        (
            "Each players shuffles their hand and puts it on the bottom of their deck. If either player put any cards on the bottom of their deck in this way, each player draws a card for each of their remaining Prize cards.",
            "双方玩家，各将自己所有的手牌反面朝上重洗，放回牌库下方。然后，各从牌库上方抽取与自已剩余{{TCG|奖赏卡}}张数相同数量的卡牌。",
            {"kind": "legacy_trainer", "handler": "Iono"},
        ),
        (
            "Put 1 of your Pokémon in play into your hand. ''(Discard all cards attached to that Pokémon.)''",
            "选择自己场上的1只宝可梦，放回手牌。（除宝可梦以外的卡牌，全部放于弃牌区。）",
            {"kind": "legacy_trainer", "handler": "Turo"},
        ),
        (
            "Look at the top 6 cards of your deck and put 2 of them into your hand. Discard the other cards.",
            "查看自己牌库上方6张卡牌。选择其中2张卡牌，加入手牌。将剩余的卡牌放于弃牌区。",
            {"kind": "look_hand", "look": 6, "count": 2, "rest": "discard"},
        ),
        (
            "Look at the top 4 cards of your deck and put 2 of them into your hand. Shuffle the other cards and put them on the bottom of your deck.",
            "查看自己牌库上方4张卡牌，选择其中2张卡牌，加入手牌。剩余的卡牌全部翻到反面重洗，放回牌库下方。",
            {"kind": "look_hand", "look": 4, "count": 2, "rest": "bottom"},
        ),
        (
            "Look at the top 8 cards of your deck and put up to 3 of them into your hand. Shuffle the other cards back into your deck.",
            "查看自己牌库上方8张卡牌，选择其中最多3张卡牌，加入手牌。将剩余的卡牌放回牌库并重洗牌库。",
            {
                "kind": "look_hand",
                "look": 8,
                "count": 3,
                "rest": "shuffle",
                "optional": True,
            },
        ),
        (
            "Look at the top 7 cards of your deck. You may reveal up to 2 in any combination of {{e|Grass}} Pokémon and Basic {{e|Grass}} Energy cards you find there and put them into your hand. Shuffle the other cards back into your deck.",
            "查看自己牌库上方7张卡牌，选择其中的{{e|草}}宝可梦和「基本{{e|草}}能量」合计最多2张，在给对手看过之后，加入手牌。将剩余的卡牌放回牌库并重洗牌库。",
            {
                "kind": "look_hand",
                "look": 7,
                "count": 2,
                "filter": "pokemon_energy:GRASS",
                "reveal": True,
                "rest": "shuffle",
                "optional": True,
            },
        ),
        (
            "Your opponent discards cards from their hand until they have 3 cards in their hand.",
            "对手将对手自己的手牌放于弃牌区，直到手牌变为3张为止。",
            {"kind": "discard_hand_to", "count": 3, "players": "opponent"},
        ),
        (
            "Each player discards cards from their hand until they have 5 cards in their hand. Your opponent discards first. ''(If a player has 5 or fewer cards in their hand, they do not discard.)''",
            "双方玩家，各将自己的手牌放于弃牌区，直到手牌变为5张为止。（由对手开始放于弃牌区。手牌在5张及以下的玩家无需将手牌放于弃牌区。）",
            {"kind": "discard_hand_to", "count": 5, "players": "both"},
        ),
        (
            "Your opponent reveals their hand, and you discard up to 2 Item cards you find there.",
            "查看对手的手牌，选择其中最多2张物品，放于弃牌区。",
            {
                "kind": "opponent_hand",
                "filter": "item",
                "count": 2,
                "destination": "discard",
                "optional": True,
            },
        ),
        (
            "Your opponent reveals their hand, and you put a Pokémon you find there on the bottom of their deck.",
            "查看对手的手牌，选择其中1张宝可梦，放回对手的牌库下方。",
            {
                "kind": "opponent_hand",
                "filter": "pokemon",
                "count": 1,
                "destination": "bottom",
            },
        ),
        (
            "Your opponent reveals their hand, and you draw 2 cards for each Supporter card you find there.",
            "查看对手的手牌，从自己牌库上方抽取对手的手牌中支援者张数×2张卡牌。",
            {"kind": "reveal_draw", "filter": "supporter", "factor": 2},
        ),
        (
            "Draw a card for each of your Ancient Pokémon in play.",
            "从牌库上方抽取与自己场上「古代」宝可梦数量相同数量的卡牌。",
            {"kind": "draw", "count": 0, "countTerm": "ancient"},
        ),
        (
            "Draw a card for each of your opponent's Benched Pokémon.",
            "从自己牌库上方抽取与对手备战宝可梦数量相同数量的卡牌。",
            {"kind": "draw", "count": 0, "countTerm": "opponent_bench"},
        ),
        (
            "Search your deck for up to 3 Pokémon ex, reveal them, and put them into your hand. Then, shuffle your deck.",
            "选择自己牌库中最多3张「宝可梦{{ex}}」，在给对手看过之后，加入手牌。并重洗牌库。",
            {"kind": "search_hand", "filter": "ex", "count": 3},
        ),
        (
            'Search your deck for up to 3 Pokémon Tool cards that have "Technical Machine" in their name, reveal them, and put them into your hand. Then, shuffle your deck.',
            "选择自己牌库中，名字中带有「{{TCG|招式学习器（宝可梦道具类型）|招式学习器}}」的「宝可梦道具」最多3张，在给对手看过之后，加入手牌。并重洗牌库。",
            {"kind": "search_hand", "filter": "technical_machine", "count": 3},
        ),
        (
            "Search your deck for up to 2 Basic Hop's Pokémon and put them onto your Bench. Then, shuffle your deck.",
            "选择自己牌库中最多2张'''基础'''宝可梦的「赫普的宝可梦」，放于备战区。并重洗牌库。",
            {"kind": "search_bench", "filter": "hop_basic", "count": 2},
        ),
        (
            "Search your deck for up to 3 in any combination of Ethan's Pokémon and Basic {{e|Fire}} Energy cards, reveal them, and put them into your hand. Then, shuffle your deck.",
            "选择自己牌库中的「阿响的宝可梦」和「基本{{e|火}}能量」合计最多3张，在给对手看过之后加入手牌。并重洗牌库。",
            {"kind": "search_hand", "filter": "ethan_or_fire", "count": 3},
        ),
        (
            "Put up to 5 in any combination of Pokémon and Basic Energy cards from your discard pile into your hand.",
            "选择自己弃牌区中的宝可梦和基本能量合计最多5张，在给对手看过之后，加入手牌。",
            {"kind": "recover_hand", "filter": "pokemon_or_energy", "count": 5},
        ),
        (
            "Put up to 4 in any combination of {{e|超}} Pokémon and Basic {{e|超}} Energy cards from your discard pile into your hand.",
            "选择自己弃牌区中的{{e|超}}宝可梦和「基本{{e|超}}能量」合计最多4张，在给对手看过之后，加入手牌。",
            {"kind": "recover_hand", "filter": "pokemon_energy:PSYCHIC", "count": 4},
        ),
        (
            "Search your deck for an Item card, a Pokémon Tool card, a Supporter card, and a Stadium card, reveal them, and put them into your hand. Then, shuffle your deck.",
            "选择自己牌库中「物品」「宝可梦道具」「支援者」「竞技场」各1张，在给对手看过之后，加入手牌。并重洗牌库。",
            {
                "kind": "search_pair",
                "filters": ["item", "tool", "supporter", "stadium"],
                "count": 1,
            },
        ),
        (
            "Heal 70 damage from your Active Pokémon.",
            "回复自己的战斗宝可梦「70」点HP。",
            {"kind": "heal", "count": 70, "target": "active"},
        ),
        (
            "Heal 60 damage from your Active {{e|Dragon}} Pokémon.",
            "回复自己战斗场上{{e|龙}}宝可梦「60」HP。",
            {"kind": "heal", "count": 60, "target": "active", "type": "DRAGON"},
        ),
        (
            "Heal 60 damage from each of your {{e|Lightning}} Pokémon.",
            "将自己所有{{e|雷}}宝可梦的HP，各回复「60」。",
            {"kind": "heal_own_all", "count": 60, "type": "LIGHTNING"},
        ),
        (
            "Choose up to 2 of your Pokémon and heal 50 damage from each of them.",
            "选择最多2只自己的宝可梦，各回复其「50」HP。",
            {"kind": "heal", "count": 50, "targets": 2},
        ),
        (
            "Heal all damage from 1 of your Pokémon that has 30 HP or less remaining.",
            "将自己的1只剩余HP在「30」及以下的宝可梦的HP，全部回复。",
            {"kind": "heal", "count": "all", "maxRemainingHP": 30},
        ),
        (
            "Your opponent's Active Pokémon is now Burned and Confused.",
            "令对手的战斗宝可梦陷入'''{{TCG|灼伤}}'''和'''{{TCG|混乱}}'''状态。",
            {"kind": "opponent_status", "statuses": ["BURNED", "CONFUSED"]},
        ),
        (
            "Each player shuffles their hand into their deck. Then, you draw 5 cards, and your opponent draws 2 cards.",
            "双方玩家，各将所有手牌放回牌库并重洗牌库。然后，自己从牌库上方抽取5张卡牌，对手从牌库上方抽取2张卡牌。",
            {"kind": "both_shuffle_draw", "count": 5, "opponentCount": 2},
        ),
    ]
    for english, chinese, rule in pairs:
        if en == english and cn == chinese:
            return rule
    return None
