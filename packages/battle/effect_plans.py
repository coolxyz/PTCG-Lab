"""Build plans from the actor's visible cards and bind each step to a live prompt."""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from packages.battle.allocations import (
    candidate_objects,
    checked_command,
    continuations,
)

JOLTIK = "P4P-6F43C4BBAF38"
SECRET_BOX = "P4T-48AEC844B6D7"
ARVEN = "P4T-195A48AF3235"
CRISPIN = "P4T-DFECFA3FB1D3"
MUNKIDORI = "TWM-095"
EVOLUTION = "PAR-178"
ENERGY_SWITCH = "P4T-F765EF2DAA52"
PIKACHU = "P4P-CHS5EA6DD16D6397A4A"
ENERGY_SEARCH_PRO = "P4T-736430E482A0"

BENCH = ["CardPosition.BENCH"]
HAND = ["CardPosition.HAND"]
PAYMENT = re.compile(r"Choose Energy cards \((\d+)/(\d+) units")


@dataclass(frozen=True)
class Step:
    kind: str
    selected: tuple = ()
    eligible_ids: frozenset = frozenset()


@dataclass(frozen=True)
class Offer:
    effect: str
    fields: dict
    build: Callable[[dict[str, list[str]]], list[Step] | None]

    def visible(self):
        return {"effect": self.effect, "fields": self.fields}

    def steps(self, answer):
        if not valid(answer, self.visible()):
            return None
        return self.build(answer)


def valid(answer, spec):
    if not isinstance(answer, dict) or answer.keys() != spec["fields"].keys():
        return False
    for name, field in spec["fields"].items():
        refs = answer[name]
        if (
            not isinstance(refs, list)
            or not field["min"] <= len(refs) <= field["max"]
            or not all(isinstance(ref, str) for ref in refs)
            or (not field.get("repeat") and len(set(refs)) != len(refs))
            or not set(refs) <= {option["ref"] for option in field["options"]}
        ):
            return False
    return True


def _field(label, options, minimum=0, maximum=1, *, repeat=False):
    return {
        "label": label,
        "options": list(options),
        "min": minimum,
        "max": maximum,
        **({"repeat": True} if repeat else {}),
    }


def _board(game, view, side):
    viewer = game.actor
    player = next(
        p for p in game.env.get_players() if (p.id == viewer) == (side == "self")
    )
    return {
        dto["ref"]: (dto, obj)
        for zone in ("active", "bench")
        for dto, obj in zip(
            view["observation"][side][zone], getattr(player, zone), strict=True
        )
    }


def _board_options(board):
    return [{"ref": ref, "name": dto["name"]} for ref, (dto, _) in board.items()]


def _target(board, ref):
    return Step("board", (board[ref][1],))


def _inspected(decision):
    return decision.get("deckInspection", {}).get("owner") == "self"


def _deck(decision):
    # Resolve rule predicates only for IDs already in the authorized, unordered DTO.
    from ptcg.core.card_registry import registry

    cards = {}
    for entry in decision["deckInspection"]["cards"]:
        dto = entry["card"]
        cls = registry.get(dto["id"])
        if cls is not None:
            cards[dto["id"]] = (dto, entry["count"], cls())
    return cards


def _printed_options(cards):
    return [
        {
            "ref": ref,
            "name": dto["name"],
            "count": count,
            "cardType": dto.get("cardType"),
        }
        for ref, (dto, count, _) in cards.items()
    ]


def _deck_step(cards, selected):
    return Step("deck", tuple(selected), frozenset(cards))


def _deck_energies(deck):
    from packages.rules.zone_effects import matches

    return {
        ref: entry for ref, entry in deck.items() if matches(entry[2], "basic_energy")
    }


def _jolting_charge(game, view, deck):
    from packages.rules.attachment_effects import matches

    board = _board(game, view, "self")
    pools = {
        field: {
            ref: entry
            for ref, entry in deck.items()
            if matches(entry[2], {"basic": True, "type": energy})
        }
        for field, energy in (
            ("grassTargets", "GRASS"),
            ("lightningTargets", "LIGHTNING"),
        )
    }
    if not any(pools.values()) or not board:
        return None
    if len(board) == 1 and sum(bool(pool) for pool in pools.values()) == 1:
        return None
    fields = {
        field: _field(
            label,
            _board_options(board),
            maximum=min(2, sum(entry[1] for entry in pools[field].values())),
            repeat=True,
        )
        for field, label in (
            ("grassTargets", "基本草能量的附加目标，每个引用附加一张"),
            ("lightningTargets", "基本雷能量的附加目标，每个引用附加一张"),
        )
    }

    def build(answer):
        steps = []
        for field, pool in pools.items():
            targets = answer[field]
            # The physical Basic Energy printings of one type are interchangeable.
            steps.append(Step("deck-count", (len(targets),), frozenset(pool)))
            if len(board) > 1:
                steps.extend(_target(board, ref) for ref in targets)
        return steps

    return Offer("jolting-charge", fields, build)


def _trainer_search(deck, effect, categories):
    from packages.rules.trainers import matches

    pools = {
        category: {
            ref: entry for ref, entry in deck.items() if matches(entry[2], category)
        }
        for category in categories
    }
    if sum(bool(pool) for pool in pools.values()) < 2:
        return None
    labels = {
        "item": "物品",
        "tool": "宝可梦道具",
        "supporter": "支援者",
        "stadium": "竞技场",
    }
    fields = {
        category: _field(
            labels[category], _printed_options(pool), maximum=int(bool(pool))
        )
        for category, pool in pools.items()
    }
    return Offer(
        effect,
        fields,
        lambda answer: [
            _deck_step(pool, answer[category]) for category, pool in pools.items()
        ],
    )


def _distinct_energy(game, view, deck, *, attach):
    pool = _deck_energies(deck)
    types = {ref: entry[2].cardType for ref, entry in pool.items()}
    if len(set(types.values())) < 2:
        return None
    board = _board(game, view, "self") if attach else {}
    limit = 2 if attach else 8
    fields = {
        "energies": _field(
            "每种属性至多一张；第一张加入手牌，第二张附加到指定目标"
            if attach
            else "每种属性至多一张，按选择顺序加入手牌",
            _printed_options(pool),
            maximum=min(limit, len(set(types.values()))),
        )
    }
    if attach:
        fields["recipient"] = _field(
            "选择两张能量时，指定第二张的附加目标", _board_options(board)
        )

    def build(answer):
        refs = answer["energies"]
        if len({types[ref] for ref in refs}) != len(refs):
            return None
        recipient = answer.get("recipient", [])
        if attach and bool(recipient) != (len(refs) == 2):
            return None
        available = pool
        steps = []
        for ref in refs:
            steps.append(_deck_step(available, [ref]))
            available = {
                key: entry
                for key, entry in available.items()
                if types[key] != types[ref]
            }
        if len(refs) < limit:
            steps.append(_deck_step(available, []))
        if attach and refs:
            steps.append(Step("staged-energy", (refs[0],), frozenset(refs)))
            if recipient:
                steps.append(_target(board, recipient[0]))
        return steps

    return Offer("crispin" if attach else "energy-search-pro", fields, build)


def _adrena_brain(game, view):
    from packages.rules.maximum_hp import maximum

    donors = _board(game, view, "self")
    allowed = {card["boardRef"] for card in view["decision"]["candidates"]}
    donors = {ref: entry for ref, entry in donors.items() if ref in allowed}
    counts = {
        ref: min(3, (maximum(obj) - obj.hp) // 10) for ref, (_, obj) in donors.items()
    }
    targets = _board(game, view, "opponent")
    if (
        not donors
        or not targets
        or (len(donors) == len(targets) == max(counts.values()) == 1)
    ):
        return None
    fields = {
        "donor": _field(
            "移出伤害指示物的我方宝可梦",
            [
                {**option, "maxCounters": counts[option["ref"]]}
                for option in _board_options(donors)
            ],
            minimum=1,
        ),
        "count": _field(
            "伤害指示物数量，不能超过所选移出目标的 maxCounters",
            [{"ref": str(n)} for n in range(1, max(counts.values()) + 1)],
            minimum=1,
        ),
        "target": _field(
            "接收伤害指示物的对方宝可梦", _board_options(targets), minimum=1
        ),
    }

    def build(answer):
        donor, count, target = (answer[name][0] for name in fields)
        if int(count) > counts[donor]:
            return None
        return [
            _target(donors, donor),
            Step("number", (int(count),)),
            _target(targets, target),
        ]

    return Offer("adrena-brain", fields, build)


def _evolution(game, view, deck, previous):
    from packages.rules.pokemon_replacement import permitted

    # The engine normalizes the previous multi-selection to bench order.
    # No Pokémon move between that selection and the first deck prompt.
    chosen = {ref.split(":", 1)[1] for ref in previous.get("boardTargets", [])}
    targets = [
        (ref, dto)
        for ref, (dto, _) in _board(game, view, "self").items()
        if ref.split(":", 1)[1] in chosen
    ]
    if len(chosen) != 2 or len(targets) != 2:
        return None
    pools = {
        f"evolution{i}": {
            ref: entry
            for ref, entry in deck.items()
            if getattr(entry[2], "evolveFrom", [])[:1] == [dto["name"]]
            and permitted(entry[2])
        }
        for i, (_, dto) in enumerate(targets, 1)
    }
    if not all(pools.values()):
        return None
    fields = {
        field: _field(
            f"为 {dto['name']}（{ref}）选择直接进化卡，可跳过", _printed_options(pool)
        )
        for (field, pool), (ref, dto) in zip(pools.items(), targets, strict=True)
    }

    def build(answer):
        used = Counter()
        steps = []
        for field, pool in pools.items():
            available = {
                ref: entry for ref, entry in pool.items() if entry[1] > used[ref]
            }
            if any(ref not in available for ref in answer[field]):
                return None
            steps.append(_deck_step(available, answer[field]))
            used.update(answer[field])
        return steps

    return Offer("technical-machine-evolution", fields, build)


def _energy_switch(game, view):
    from packages.rules.trainer_field_effects import energy_matches

    board = _board(game, view, "self")
    donors = {item["boardRef"] for item in view["decision"]["candidates"]}
    cards = {}
    options = []
    for ref, (_, donor) in board.items():
        if ref not in donors:
            continue
        for i, energy in enumerate(donor.attachment):
            if not energy_matches(energy, {"basic": True}):
                continue
            token = f"{ref}:energy:{i}"
            cards[token] = (ref, energy)
            options.append({"ref": token, "name": energy.name, "donor": ref})
    if not options or len(board) < 2 or (len(options) == 1 and len(board) == 2):
        return None
    fields = {
        "energy": _field(
            "选择一张已附加的基本能量，donor 为移出目标", options, minimum=1
        ),
        "recipient": _field(
            "接收能量的我方宝可梦，必须与 donor 不同", _board_options(board), minimum=1
        ),
    }

    def build(answer):
        donor, card = cards[answer["energy"][0]]
        recipient = answer["recipient"][0]
        if donor == recipient:
            return None
        return [
            _target(board, donor),
            Step("attached-energy", (card,)),
            _target(board, recipient),
        ]

    return Offer("energy-switch", fields, build)


def _explosive_vortex(game, view):
    prompt = game.info["prompt"]
    match = PAYMENT.match(prompt.tips)
    if not match or match[1] != "0":
        return None
    required = int(match[2])
    objects = candidate_objects(game, view)
    if len(objects) < 2 or required < 2:
        return None
    units = {ref: len(card.provides) for ref, card in objects.items()}
    fields = {
        "payment": _field(
            f"按弃置顺序选择实体能量卡，units 合计至少 {required}；"
            f"最多弃置 {required} 张。达到能量数后可停止。",
            [
                {"ref": ref, "name": card.name, "units": units[ref]}
                for ref, card in objects.items()
            ],
            minimum=1,
            maximum=min(required, len(objects)),
        )
    }

    def build(answer):
        refs = answer["payment"]
        if sum(units[ref] for ref in refs) < required:
            return None
        steps = [Step("payment", (objects[ref],)) for ref in refs]
        if len(refs) < min(required, len(objects)):
            steps.append(Step("payment"))
        return steps

    return Offer("explosive-vortex", fields, build)


def window(game, view, history):
    """Recognize only the opening prompt of a supported planning window."""
    decision = view["decision"]
    if (
        view["phase"] != "playing"
        or not decision
        or decision["kind"] != "selection"
        or decision["hidden"]
        or not history
    ):
        return None
    previous = history[-1].get("ownAction", {})
    source = decision.get("sourceId")
    attack = (previous.get("attack") or {}).get("name")
    candidates = decision["candidates"]
    if _inspected(decision):
        if (
            source == JOLTIK
            and previous.get("sourceId") == source
            and attack == "Jolting Charge"
        ):
            return _jolting_charge(game, view, _deck(decision))
        if (
            source == SECRET_BOX
            and previous.get("sourceId") == source
            and previous.get("zones") == HAND
        ):
            return _trainer_search(
                _deck(decision), "secret-box", ("item", "tool", "supporter", "stadium")
            )
        if (
            source == ARVEN
            and previous.get("sourceId") == source
            and previous.get("actionType") == "UseSupporterAction"
        ):
            return _trainer_search(_deck(decision), "arven", ("item", "tool"))
        if (
            source in (CRISPIN, ENERGY_SEARCH_PRO)
            and previous.get("sourceId") == source
            and previous.get("actionType") in ("UseItemAction", "UseSupporterAction")
        ):
            return _distinct_energy(
                game, view, _deck(decision), attach=source == CRISPIN
            )
        if (
            source == EVOLUTION
            and previous.get("sourceId") == source
            and previous.get("zones") == BENCH
        ):
            return _evolution(game, view, _deck(decision), previous)
    elif (
        source == PIKACHU
        and previous.get("sourceId") == source
        and attack == "Explosive Vortex"
    ):
        return _explosive_vortex(game, view)
    elif candidates and all(item.get("side") == "self" for item in candidates):
        if (
            source == MUNKIDORI
            and previous.get("actionType") == "UseAbilityAction"
            and previous.get("sourceId") == source
        ):
            return _adrena_brain(game, view)
        if (
            source == ENERGY_SWITCH
            and previous.get("actionType") == "UseItemAction"
            and previous.get("sourceId") == source
        ):
            return _energy_switch(game, view)
    return None


def follow(game, first_view, steps, project):
    """Resolve stable plan choices to fresh refs only within the same effect."""
    source = game.info["prompt"].source

    def resolve(game, current, step):
        decision = current["decision"]
        if not decision:
            return None
        if step.kind == "number":
            if decision["kind"] != "options" or any(
                o["actionType"] != "NumberOption" for o in decision["options"]
            ):
                return None
            option = next(
                (o for o in decision["options"] if o["value"] == step.selected[0]), None
            )
            return (
                checked_command(current, {"optionId": option["id"]}) if option else None
            )
        prompt = game.info.get("prompt")
        if (
            decision["kind"] != "selection"
            or decision["hidden"]
            or getattr(prompt, "source", None) is not source
        ):
            return None
        candidates = decision["candidates"]
        if step.kind in ("deck", "deck-count", "staged-energy"):
            if (
                _inspected(decision) != (step.kind != "staged-energy")
                or frozenset(c["card"]["id"] for c in candidates) != step.eligible_ids
            ):
                return None
            refs = []
            if step.kind == "deck-count":
                refs = [c["ref"] for c in candidates[: step.selected[0]]]
                if len(refs) != step.selected[0]:
                    return None
            else:
                for ref in step.selected:
                    candidate = next(
                        (c["ref"] for c in candidates if c["card"]["id"] == ref), None
                    )
                    if candidate is None:
                        return None
                    refs.append(candidate)
        else:
            if _inspected(decision):
                return None
            if step.kind == "board" and not all(c.get("side") for c in candidates):
                return None
            if step.kind == "attached-energy" and not all(
                c["card"].get("energyType") == "BASIC" for c in candidates
            ):
                return None
            if step.kind == "payment" and not PAYMENT.match(prompt.tips):
                return None
            objects = candidate_objects(game, current)
            refs = [
                ref
                for target in step.selected
                for ref, obj in objects.items()
                if obj is target
            ]
            if len(refs) != len(step.selected):
                return None
        return checked_command(current, {"selectedRefs": refs})

    return continuations(game, first_view, steps, project, resolve)
