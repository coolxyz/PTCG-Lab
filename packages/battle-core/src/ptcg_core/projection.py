"""Detached actor-scoped projection preserving legacy visible fields."""

def view(game, viewer):
    result = game.view(viewer)
    result["winner"] = getattr(game.info.get("winner"), "name", None)
    # Decision-scoped public instance references: never export hidden objects.
    locations = {}
    players = list(game.env.get_players())
    for side in ("self", "opponent"):
        player = next(p for p in players if (p.id == viewer) == (side == "self"))
        for zone in ("active", "bench", "discard", "hand"):
            if side == "opponent" and zone == "hand":
                continue
            for index, (dto, obj) in enumerate(
                zip(result["observation"][side][zone], getattr(player, zone))
            ):
                ref = f"v{game.version}:{side}:{zone}:{index}"
                dto["ref"] = ref
                locations[id(obj)] = ref
        result["observation"][side]["benchCapacity"] = max(5, len(player.bench))
    for index, (dto, obj) in enumerate(
        zip(result["observation"].get("stadium", []), game.env.gamestate.stadium)
    ):
        ref = f"v{game.version}:stadium:{index}"
        dto["ref"] = ref
        locations[id(obj)] = ref
    decision = result["decision"]
    if decision:
        if decision["kind"] == "selection":
            decision["indexed"] = game.actions.indexed
            from ptcg_core.prompts import describe

            decision["zones"] = (
                []
                if decision["hidden"]
                else sorted(
                    {
                        str(getattr(c, "cardPosition", ""))
                        for c in game.actions.candidates
                    }
                )
            )
            prompt = game.info.get("prompt")
            source = getattr(prompt, "source", None)
            # Structured, actor-only metadata; never forward free-form engine logs.
            decision["source"] = getattr(source, "name", None)
            decision["sourceId"] = getattr(source, "id", None)
            # Public field positions disambiguate identical Pokémon. Deck/hand refs
            # stay decision-scoped, and hidden choices carry no identity/position.
            for item, original in zip(decision["candidates"], game._candidate_order()):
                card = game.actions.candidates[original]
                zone = str(getattr(card, "cardPosition", ""))
                if not decision["hidden"] and id(card) in locations:
                    item["boardRef"] = locations[id(card)]
                if not decision["hidden"] and zone in (
                    "CardPosition.ACTIVE",
                    "CardPosition.BENCH",
                ):
                    item["position"] = zone
                    item["index"] = getattr(card, "index", None)
                    for player in game.env.get_players():
                        if any(card is c for c in player.active + player.bench):
                            item["side"] = "self" if player.id == viewer else "opponent"
            decision["instruction"] = describe(game, decision)
        else:
            for option, action in zip(decision["options"], game.actions):
                for role in ("source", "target", "active_pokemon"):
                    obj = getattr(action, role, None)
                    if hasattr(obj, "name"):
                        option[role] = obj.name
                        option[role + "Id"] = getattr(obj, "id", None)
                        option[role + "Ref"] = locations.get(id(obj))
    return result
