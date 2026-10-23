"""Structured presentation deltas computed ONLY from persisted public views."""

from collections import Counter, defaultdict


def movements(old, new, side):
    """Conservative identity-count deltas; never identify an opponent hand card.

    Public duplicate copies are grouped, not assigned fabricated stable instance
    IDs. Missing intermediate frames are handled by the client's sequence gate.
    """
    zones = ["active", "bench", "discard", "lost_zone"]
    if side == "self":
        zones.append("hand")
    removed, added = defaultdict(list), defaultdict(list)
    for zone in zones:
        a = Counter(c["name"] for c in old.get(zone, []) or [])
        b = Counter(c["name"] for c in new.get(zone, []) or [])
        for name, n in (a - b).items():
            removed[name].extend([zone] * n)
        for name, n in (b - a).items():
            added[name].extend([zone] * n)
    result = []

    def move(start, end, name=None, label="移动"):
        result.append(
            {
                "kind": "move",
                "side": side,
                "zone": end,
                "fromZone": start,
                "cardName": name,
                "label": label,
                "before": 0,
                "after": 1,
            }
        )

    for name in list(removed):
        while removed[name] and added[name]:
            start, end = removed[name].pop(0), added[name].pop(0)
            move(
                start,
                end,
                name,
                "昏厥 / 弃置"
                if start in ("active", "bench") and end == "discard"
                else "移动",
            )
    for zone in ("active", "bench"):
        for index, c in enumerate(new.get(zone, [])):
            previous = old.get(zone, [])
            if index >= len(previous):
                continue
            prior = previous[index]
            if c["name"] != prior["name"] and prior["name"] in c.get("evolved", []):
                existing = next(
                    (
                        e
                        for e in result
                        if e["cardName"] == c["name"] and e["zone"] == zone
                    ),
                    None,
                )
                if existing:
                    existing["label"] = "进化"
                else:
                    move("hand", zone, c["name"], "进化")
                if zone in added[c["name"]]:
                    added[c["name"]].remove(zone)
            if c["name"] != prior["name"]:
                continue
            attachments = Counter(c.get("attachment", [])[1:]) - Counter(
                prior.get("attachment", [])[1:]
            )
            for name, n in attachments.items():
                for _ in range(n):
                    move("hand", zone, name, "附着")
    drawn = max(0, old["deck_count"] - new["deck_count"])
    prizes = max(0, old["prize_count"] - new["prize_count"])
    if side == "opponent":
        # Only count-derived card backs; the supplied opponent hand is ignored.
        gain = max(0, new["hand_count"] - old["hand_count"])
        for _ in range(min(prizes, gain)):
            move("prize", "hand", label="领赏")
        for _ in range(min(drawn, max(0, gain - prizes))):
            move("deck", "hand", label="抽牌 / 检索")
        for name, destinations in added.items():
            for zone in destinations:
                if zone in ("active", "bench"):
                    move("hand", zone, name, "上场")
    else:
        names = [name for name, dest in added.items() for z in dest if z == "hand"]
        for name in names[:prizes]:
            move("prize", "hand", name, "领赏")
        for name in names[prizes : prizes + drawn]:
            move("deck", "hand", name, "抽牌 / 检索")
    return result


def public_effects(before, after):
    if before is None:
        return []
    effects = []
    for side in ("self", "opponent"):
        old, new = before[side], after[side]
        effects.extend(movements(old, new, side))
        for field in ("hand_count", "deck_count", "prize_count"):
            if old[field] != new[field]:
                effects.append(
                    {
                        "kind": "count",
                        "side": side,
                        "zone": field.removesuffix("_count"),
                        "before": old[field],
                        "after": new[field],
                    }
                )
        for zone in ("active", "bench", "discard", "lost_zone"):
            if len(old[zone]) != len(new[zone]):
                effects.append(
                    {
                        "kind": "count",
                        "side": side,
                        "zone": zone,
                        "before": len(old[zone]),
                        "after": len(new[zone]),
                    }
                )
        for zone in ("active", "bench"):
            for index, card in enumerate(new[zone]):
                if index >= len(old[zone]):
                    continue
                previous = old[zone][index]
                if (
                    previous.get("id", previous["name"]) == card.get("id", card["name"])
                    and previous.get("hp") != card.get("hp")
                    and isinstance(card.get("hp"), (int, float))
                    and isinstance(previous.get("hp"), (int, float))
                ):
                    effects.append(
                        {
                            "kind": "hp",
                            "side": side,
                            "zone": zone,
                            "index": index,
                            "before": max(0, previous["hp"]),
                            "after": max(0, card["hp"]),
                        }
                    )
    if before.get("turn_number") != after.get("turn_number") or before.get(
        "turn"
    ) != after.get("turn"):
        effects.append(
            {
                "kind": "turn",
                "side": "self",
                "zone": "active",
                "before": before.get("turn_number", 0),
                "after": after.get("turn_number", 0),
                "label": "换回合",
            }
        )
    return effects
