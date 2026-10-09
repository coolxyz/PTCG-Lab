"""Conditional opening proposals for observation-only simulation particles.

This changes only a simulation's proposal distribution, never the authoritative
engine. All final particles still have to match every observed history frame.
"""

import copy
from packages.rules.engine import RulesEngine
from packages.battle.runtime import Adapter


class OpeningProposalEngine(RulesEngine):
    def _deal(self, player):
        super()._deal(player)
        name = player.id.name
        deal_index = self.opening_applied.count(name)
        self.opening_applied.append(name)
        hands = (
            (
                [self.opening["hands"][name]]
                + self.opening.get("redraws", {}).get(name, [])
            )
            if name in self.opening["hands"]
            else []
        )
        if deal_index >= len(hands):
            return
        deck = player.deck
        # The initial random shuffle supplies a random ordering for unconstrained
        # cards and an unbiased choice among indistinguishable physical copies.
        constraints = dict(enumerate(hands[deal_index]))
        constraints.update(
            {
                int(k): v
                for k, v in (
                    self.opening.get("positions", {}).get(name, {})
                    if deal_index == len(hands) - 1
                    else {}
                ).items()
            }
        )
        for index, card_id in sorted(constraints.items()):
            found = next(
                (j for j in range(index, len(deck)) if deck[j].id == card_id), None
            )
            if found is None:
                raise ValueError("OPENING_PRIOR_INCONSISTENT")
            deck[index], deck[found] = deck[found], deck[index]
        player.hand, player.left = list(deck[:7]), list(deck[7:])
        from ptcg.core.enums import CardPosition

        for cards, zone in (
            (player.hand, CardPosition.HAND),
            (player.left, CardPosition.LEFT),
        ):
            for index, card in enumerate(cards):
                card.cardPosition, card.index = zone, index + 1


def proposal_game(config):
    proposal = config["openingProposal"]
    if (
        set(proposal) - {"schema", "hands", "positions", "redraws"}
        or proposal["schema"] != "observed-opening-v1"
        or not isinstance(proposal["hands"], dict)
        or not proposal["hands"]
        or any(
            player not in ("PLAYER1", "PLAYER2")
            or not isinstance(hand, list)
            or not 1 <= len(hand) <= 7
            or not all(isinstance(c, str) for c in hand)
            for player, hand in proposal["hands"].items()
        )
    ):
        raise ValueError("INVALID_OPENING_PROPOSAL")
    redraws = proposal.get("redraws", {})
    if not isinstance(redraws, dict) or any(
        player not in proposal["hands"]
        or not isinstance(hands, list)
        or len(hands) > 2500
        or any(
            not isinstance(hand, list)
            or not 1 <= len(hand) <= 7
            or not all(isinstance(c, str) for c in hand)
            for hand in hands
        )
        for player, hands in redraws.items()
    ):
        raise ValueError("INVALID_OPENING_PROPOSAL")
    positions = proposal.get("positions", {})
    if not isinstance(positions, dict) or any(
        player not in proposal["hands"]
        or not isinstance(values, dict)
        or any(
            not isinstance(k, str)
            or not k.isdigit()
            or not 7 <= int(k) < 60
            or not isinstance(v, str)
            for k, v in values.items()
        )
        for player, values in positions.items()
    ):
        raise ValueError("INVALID_OPENING_PROPOSAL")
    game = Adapter.__new__(Adapter)
    game.config = copy.deepcopy(config)
    game.env = OpeningProposalEngine(
        seed=config["seed"],
        deck1=config["deck1"],
        deck2=config["deck2"],
        record_game=False,
    )
    game.env.opening = copy.deepcopy(proposal)
    game.env.opening_applied = []
    game.obs, _, game.done, game.info = game.env.reset()
    game.version, game.commands, game.receipts, game.failed = 0, [], {}, False
    return game
