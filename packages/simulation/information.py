"""Observation-only rejection sampler: never accepts authoritative private state.

Every accepted particle is an independently generated legal trajectory matching
ALL observations and the viewer's recorded choices. Proposal priors/policies are
explicit, not a claim of a uniform posterior. Exhaustion returns no particles;
there is deliberately no fallback to copying real hidden cards. Long histories
may have very low acceptance and must be measured before online search use.
"""

import copy
import random
import itertools
from collections import Counter
from dataclasses import dataclass
from packages.battle.runtime import Adapter, PlayerId, view
from packages.simulation.fork import RuleFork

PUBLIC_KEYS = {
    "stateVersion",
    "observation",
    "done",
    "decision",
    "phase",
    "startingPlayer",
    "setupEvents",
    "publicReveals",
    "winner",
}
FORBIDDEN_KEYS = {
    "seed",
    "config",
    "checkpoint",
    "stateDigest",
    "rng",
    "private_state",
    "deck1",
    "deck2",
}


def observation(value):
    """Explicit projection; transport/UI metadata does not constrain the world."""
    return {k: copy.deepcopy(value[k]) for k in PUBLIC_KEYS if k in value}


def assert_public(value):
    if isinstance(value, dict):
        if FORBIDDEN_KEYS.intersection(value):
            raise ValueError("PRIVATE_DATA_IN_INFORMATION_SET")
        for item in value.values():
            assert_public(item)
    elif isinstance(value, list):
        for item in value:
            assert_public(item)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError("INFORMATION_SET_MUST_BE_JSON")


@dataclass(frozen=True)
class InformationSet:
    viewer: str
    # Snapshots at every accepted action, beginning at version zero.
    observations: list
    # Viewer choices indexed by stateVersion; no opponent private commands.
    choices: dict
    own_deck: list
    # Publicly chosen prior family; not fetched from an opponent's match config.
    opponent_priors: list

    def validate(self):
        if (
            self.viewer not in ("PLAYER1", "PLAYER2")
            or not self.observations
            or not self.opponent_priors
        ):
            raise ValueError("INVALID_INFORMATION_SET")
        assert_public(self.observations)
        assert_public(self.choices)
        for i, snapshot in enumerate(self.observations):
            if set(snapshot) - PUBLIC_KEYS or snapshot.get("stateVersion") != i:
                raise ValueError("INCOMPLETE_OBSERVATION_HISTORY")
            if snapshot["observation"]["opponent"]["hand"] is not None:
                raise ValueError("OPPONENT_HAND_DISCLOSURE")
            acting = snapshot.get("decision") is not None
            if i < len(self.observations) - 1 and acting != (str(i) in self.choices):
                raise ValueError("VIEWER_CHOICE_HISTORY_MISMATCH")
        allowed_versions = {str(i) for i in range(len(self.observations) - 1)}
        if set(self.choices) - allowed_versions:
            raise ValueError("EXTRA_PRIVATE_COMMANDS")
        # Input card pools are prior hypotheses, all using reviewed legal cards.
        from packages.collection.domain import CARDS, validation

        by_effect = {
            c["engineId"]: c["printingId"]
            for c in CARDS.values()
            if c["effectStatus"] == "verified"
        }
        from collections import Counter
        from ptcg.utils.load_deck import load_deck

        for lines in [self.own_deck, *self.opponent_priors]:
            if not isinstance(lines, list) or not all(
                isinstance(s, str) for s in lines
            ):
                raise ValueError("INVALID_DECK_PRIOR")
            try:
                counts = Counter(by_effect[c.id] for c in load_deck(lines).cards)
            except (KeyError, ValueError) as exc:
                raise ValueError("UNSUPPORTED_DECK_PRIOR") from exc
            if not validation(
                [{"printingId": p, "quantity": n} for p, n in counts.items()]
            )["playable"]:
                raise ValueError("ILLEGAL_DECK_PRIOR")


def proposal_choice(dto, rng):
    decision = dto["decision"]
    if decision["kind"] == "options":
        return {"optionId": rng.choice(decision["options"])["id"]}
    count = rng.randint(decision["min"], decision["max"])
    return {
        "selectedRefs": [c["ref"] for c in rng.sample(decision["candidates"], count)]
    }


def transition_choices(dto, rng, limit=64):
    """A bounded proposal policy using only the candidate actor's legal DTO."""
    from packages.battle.agent import predict

    preferred = predict(dto)[0]["choice"]
    yield preferred
    decision = dto["decision"]
    if decision["kind"] == "options":
        rest = [{"optionId": o["id"]} for o in decision["options"]]
    else:
        refs = [c["ref"] for c in decision["candidates"]]
        rest = []
        for n in range(decision["min"], decision["max"] + 1):
            # Selection order can affect the public discard pile even for an
            # unordered source. Preserve every bounded ordering, not only sets.
            iterator = (
                itertools.permutations(refs, n)
                if n <= 3
                else itertools.combinations(refs, n)
            )
            for subset in itertools.islice(iterator, limit):
                rest.append({"selectedRefs": list(subset)})
                if len(rest) >= limit:
                    break
            if len(rest) >= limit:
                break
    rng.shuffle(rest)
    for choice in rest[:limit]:
        if choice != preferred:
            yield choice


def condition_opening(info, opening, opponent, rng):
    """Observation-derived importance proposal, followed by exact replay rejection.

    Publicly exposed cards are biased into the opponent's first hand. This is
    intentionally a proposal, NOT a deduction that every exposed card started
    there; a mixture retains the unconditioned proposal. No true deck order is read.
    """
    from ptcg.utils.load_deck import load_deck

    result = copy.deepcopy(opening)
    # Condition append-only visible draws until a search/hand replacement.
    # This is an importance proposal; exact replay still decides acceptance.
    position = 13
    for before, after in zip(info.observations[1:], info.observations[2:]):
        decision = before.get("decision")
        if before["phase"] == "playing" and decision:
            if decision["kind"] == "selection":
                break
            choice = info.choices.get(str(before["stateVersion"]), {})
            option = next(
                (o for o in decision["options"] if o["id"] == choice.get("optionId")),
                {},
            )
            if option.get("actionType") not in (
                "PassTurn",
                "AttachEnergyAction",
                "PlayPokemonAction",
                "EvolvePokemonAction",
            ):
                break
        a, b = before["observation"]["self"], after["observation"]["self"]
        old = [c["id"] for c in a["hand"]]
        new = [c["id"] for c in b["hand"]]
        if (
            before["phase"] != "playing"
            and after["phase"] == "playing"
            and after["startingPlayer"] == info.viewer
        ):
            # Placing the last opening Pokemon and the first turn draw can be
            # one transition: equal hand counts still contain a new drawn card.
            added = Counter(new) - Counter(old)
            for card_id in new:
                if added[card_id]:
                    result.setdefault("positions", {}).setdefault(info.viewer, {})[
                        str(position)
                    ] = card_id
                    position += 1
                    added[card_id] -= 1
            continue
        if len(new) > len(old):
            if new[: len(old)] != old or a["deck_count"] - b["deck_count"] < len(
                new
            ) - len(old):
                break
            for card_id in new[len(old) :]:
                if position >= 60:
                    break
                result.setdefault("positions", {}).setdefault(info.viewer, {})[
                    str(position)
                ] = card_id
                position += 1
    playing = next((s for s in info.observations if s["phase"] == "playing"), None)
    other = "PLAYER2" if info.viewer == "PLAYER1" else "PLAYER1"
    if not playing or other in result["hands"]:
        return result
    field = playing["observation"]["opponent"]
    basics = [c["id"] for c in field["active"] + field["bench"]]
    if not basics:
        return result
    result["hands"][other] = basics[:7]
    if rng.random() < 0.15:
        return result
    pool = load_deck(opponent).cards
    names = {c.name: c.id for c in pool}
    from ptcg.core.card import PokemonCard

    pokemon_ids = {c.id for c in pool if isinstance(c, PokemonCard)}
    required = Counter(basics)
    first_turn = playing["observation"]["turn_number"]
    for frame in info.observations:
        if frame["phase"] != "playing":
            continue
        if frame["observation"]["turn_number"] > first_turn:
            break
        public = frame["observation"]["opponent"]
        visible = Counter()
        for zone in ("active", "bench", "discard", "lost_zone"):
            for c in public[zone]:
                visible[c["id"]] += 1
                for name in c.get("attachment", [])[1:] + c.get("evolved", []):
                    if name in names:
                        visible[names[name]] += 1
        searched = Counter(
            f"{c['set_name']}-{c['number'].zfill(3)}"
            for reveal in frame.get("publicReveals", [])
            if reveal.get("actor") == other
            for c in reveal.get("cards", [])
        )
        # A revealed copy may remain in hand while an indistinguishable earlier
        # copy is discarded. Propose both physical assignments.
        if rng.random() < 0.5:
            searched = Counter({k: n for k, n in searched.items() if k in pokemon_ids})
        required |= visible - searched
    extras = list((required - Counter(basics)).elements())
    rng.shuffle(extras)
    hand = (basics + extras)[:7]
    result["hands"][other] = hand
    if len(basics + extras) > 7 and playing["startingPlayer"] == other:
        result.setdefault("positions", {}).setdefault(other, {})["13"] = (
            basics + extras
        )[7]
    # A known top-six reveal supplies a legal importance proposal for Tatsugiri.
    # Only bias a position that is still unknown; full history decides validity.
    if any(c["id"] == "P01-001" for c in field["active"]):
        reveals = [
            r
            for r in info.observations[-1].get("publicReveals", [])
            if r.get("actor") == other and r.get("cards")
        ]
        if reveals:
            c = reveals[0]["cards"][0]
            result.setdefault("positions", {}).setdefault(other, {})[
                str(14 if playing["startingPlayer"] == other else 13)
            ] = f"{c['set_name']}-{c['number'].zfill(3)}"
    return result


def condition_deals(info, opening, opponent, rng):
    """Condition each *observed* mulligan, not merely the first failed hand.

    Failed hands are public; the viewer's successful hand is privately known to
    that viewer. The opponent's successful hand remains a sampled hypothesis.
    All proposals still undergo exact replay against every historical snapshot.
    """
    failed = {"PLAYER1": [], "PLAYER2": []}
    for event in info.observations[-1].get("setupEvents", []):
        if event.get("kind") == "mulligan":
            for player, hand in event["hands"].items():
                failed[player].append(
                    [f"{c['set_name']}-{c['number'].zfill(3)}" for c in hand]
                )
    if not any(failed.values()):
        return condition_opening(info, opening, opponent, rng)
    own_hand = opening["hands"][info.viewer]
    own_index = 0
    for snapshot in info.observations[1:]:
        if snapshot["phase"] not in ("mulligan", "active"):
            continue
        hand = snapshot["observation"]["self"]["hand"]
        if len(hand) != 7:
            continue
        observed = [c["id"] for c in hand]
        failures = [
            e["hands"][info.viewer]
            for e in snapshot.get("setupEvents", [])
            if e.get("kind") == "mulligan" and info.viewer in e["hands"]
        ]
        last = (
            [f"{c['set_name']}-{c['number'].zfill(3)}" for c in failures[-1]]
            if failures
            else None
        )
        index = len(failures) - int(observed == last)
        if index >= own_index:
            own_index, own_hand = index, observed
    conditioned = condition_opening(
        info,
        {"schema": "observed-opening-v1", "hands": {info.viewer: own_hand}},
        opponent,
        rng,
    )
    for player, hands in failed.items():
        if not hands:
            continue
        sequence = list(hands)
        final_hand = conditioned["hands"].get(player)
        if final_hand is not None and (
            player != info.viewer or own_index >= len(hands)
        ):
            sequence.append(final_hand)
        conditioned["hands"][player] = sequence[0]
        if len(sequence) > 1:
            conditioned.setdefault("redraws", {})[player] = sequence[1:]
    return conditioned


def sample(info, *, sampling_seed, trials=100, count=1, diagnostics=None):
    """Finite proposal budget. Results are private simulations, never API DTOs."""
    info.validate()
    if (
        not 1 <= trials <= 10000
        or not 1 <= count <= trials
        or len(info.observations) > 2501
    ):
        raise ValueError("INVALID_SAMPLING_BUDGET")
    rng = random.Random(sampling_seed)
    particles = []
    viewer = PlayerId[info.viewer]
    proposed = 0
    opening = None
    if len(info.observations) > 1:
        # Version one is the first deal immediately after the order choice.
        hand = info.observations[1]["observation"]["self"]["hand"]
        if hand is not None and len(hand) == 7:
            opening = {
                "schema": "observed-opening-v1",
                "hands": {
                    info.viewer: [
                        f"{c['set_name']}-{c['number'].zfill(3)}" for c in hand
                    ]
                },
            }
            for event in info.observations[1].get("setupEvents", []):
                if event.get("kind") == "mulligan":
                    for player, revealed in event["hands"].items():
                        opening["hands"][player] = [
                            f"{c['set_name']}-{c['number'].zfill(3)}" for c in revealed
                        ]
    for _ in range(trials):
        proposed += 1
        opponent = rng.choice(info.opponent_priors)
        decks = (
            [info.own_deck, opponent]
            if viewer == PlayerId.PLAYER1
            else [opponent, info.own_deck]
        )
        proposal_seed = rng.getrandbits(64)
        if opening:
            from packages.simulation.proposals import proposal_game

            conditioned = condition_deals(info, opening, opponent, rng)

            branch = RuleFork(
                proposal_game(
                    {
                        "seed": proposal_seed,
                        "deck1": decks[0],
                        "deck2": decks[1],
                        "openingProposal": conditioned,
                    }
                )
            )
        else:
            branch = RuleFork(Adapter(proposal_seed, *decks))
        matched = True
        for index, expected in enumerate(info.observations):
            actual = observation(view(branch.game, viewer))
            if actual != expected:
                if diagnostics is not None:
                    diagnostics[index] = diagnostics.get(index, 0) + 1
                matched = False
                break
            if index == len(info.observations) - 1:
                break
            if branch.game.done:
                matched = False
                break
            actor = branch.game.actor
            dto = view(branch.game, actor)
            try:
                if actor == viewer:
                    branch.submit(
                        actor,
                        {
                            "commandId": f"sample-{index}",
                            "expectedStateVersion": index,
                            "decisionId": dto["decision"]["id"],
                            "choice": info.choices[str(index)],
                        },
                    )
                else:
                    # Condition a historical opponent action on its public result.
                    # This is inference about an ALREADY observed action, not a
                    # future opponent policy that sees the viewer's private hand.
                    found = None
                    for choice in transition_choices(dto, rng):
                        trial = branch.fork()
                        trial.submit(
                            actor,
                            {
                                "commandId": f"sample-{index}",
                                "expectedStateVersion": index,
                                "decisionId": dto["decision"]["id"],
                                "choice": choice,
                            },
                        )
                        if (
                            observation(view(trial.game, viewer))
                            == info.observations[index + 1]
                        ):
                            found = trial
                            break
                    if found is None:
                        if diagnostics is not None:
                            diagnostics[index + 1] = diagnostics.get(index + 1, 0) + 1
                        matched = False
                        break
                    branch = found
            except ValueError as exc:
                if str(exc) != "OPENING_PRIOR_INCONSISTENT":
                    raise
                matched = False
                break
        if matched:
            particles.append(branch.checkpoint())
            if len(particles) == count:
                break
    return {
        "status": "ok" if len(particles) == count else "exhausted",
        "proposals": proposed,
        "particles": particles,
    }
