"""Opt-in real-rule regression; run in an isolated process with a pinned bundle.

PTCG_CANDY_RULE_DIRECTORY and PTCG_CANDY_RULE_PIN select approved local assets.
No engine assets or player recovery records are embedded in this test.
"""

import os
import unittest


@unittest.skipUnless(os.environ.get("PTCG_CANDY_RULE_DIRECTORY"), "Pinned rule bundle required")
class RareCandyRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ptcg_engine_provider.bundle import DirectoryEngineProvider
        from ptcg_core.factory import CoreFactory

        cls.factory = CoreFactory(DirectoryEngineProvider(
            os.environ["PTCG_CANDY_RULE_DIRECTORY"], os.environ["PTCG_CANDY_RULE_PIN"]
        ).load())

    def setUp(self):
        from ptcg.core.card_registry import registry
        from ptcg.core.enums import CardPosition, PokemonPosition, PlayerId
        from ptcg_core.rules.adapter import Adapter
        from ptcg_core.rules.effects import Dragapult

        deck = ["4 Dreepy P4P CHS9A04C7B2B634259F", "56 Psychic Energy SVE 005"]
        self.game = Adapter(seed=23, deck1=deck, deck2=deck)
        self.state = self.game.env.gamestate
        self.state.turn = PlayerId.PLAYER1
        self.player = self.state.player1
        self.other = self.state.player2
        self.player.firstTurn = False
        self.basic = registry.get("P4P-CHS9A04C7B2B634259F")()
        self.basic.firstTurnPlayed = False
        self.basic.position = PokemonPosition.BENCH
        self.basic.cardPosition = CardPosition.BENCH
        self.basic.index = 1
        self.evolution = Dragapult()
        self.candy = registry.get("P4T-4960CF6C611B")()
        self.candy.cardPosition = self.evolution.cardPosition = CardPosition.HAND
        self.candy.index, self.evolution.index = 1, 2
        self.player.active = []
        self.player.bench = [self.basic]
        self.player.hand = [self.candy, self.evolution]
        self.player.discard = []

    def pairs(self):
        from ptcg_core.rules.final_trainers import candy_pairs
        return candy_pairs(self.player)

    def test_registered_stage_one_is_in_evolution_lookup(self):
        from ptcg_core.rules.final_trainers import evolution_links
        self.assertIn("Dreepy", evolution_links()["Drakloak"])

    def test_dragapult_is_playable_on_mature_dreepy(self):
        self.assertEqual(self.pairs(), [(self.evolution, self.basic)])
        self.assertTrue(self.candy.get_actions(self.state))

    def test_first_personal_turn_is_rejected(self):
        self.player.firstTurn = True
        self.assertEqual(self.pairs(), [])
        self.assertEqual(self.candy.get_actions(self.state), [])

    def test_basic_played_this_turn_is_rejected(self):
        self.basic.firstTurnPlayed = True
        self.assertEqual(self.pairs(), [])

    def test_unrelated_basic_is_rejected(self):
        self.basic.name = "Unrelated synthetic basic"
        self.assertEqual(self.pairs(), [])

    def test_stage_one_target_is_rejected(self):
        from ptcg.core.enums import Stage
        self.basic.stage = Stage.STAGE_1
        self.assertEqual(self.pairs(), [])

    def test_evolution_must_be_in_hand(self):
        self.player.hand.remove(self.evolution)
        self.player.discard.append(self.evolution)
        self.assertEqual(self.pairs(), [])

    def test_compiled_evolution_chain_still_works(self):
        from ptcg.core.card_registry import registry
        self.evolution = registry.get("P4P-5485406C2C86")()
        self.basic = registry.get("P4P-803EE52253F2")()
        self.basic.firstTurnPlayed = False
        self.player.hand = [self.candy, self.evolution]
        self.player.bench = [self.basic]
        self.assertEqual(self.pairs(), [(self.evolution, self.basic)])

    def test_compiled_evolution_entry_ability_is_still_offered(self):
        from ptcg.core.card_registry import registry
        from ptcg_core.rules.entry_effects import resolve

        card = registry.get("P4P-5485406C2C86")()
        self.player.bench = [card]
        choices = next(resolve(card, "evolve", self.state))[3]["raw_available_actions"]
        self.assertEqual([option.value for option in choices], [0, 1])

    def test_resolution_preserves_damage_and_attachments(self):
        from ptcg.core.action import UseItemAction
        from ptcg.core.card_registry import registry
        from ptcg.core.enums import CardPosition

        energy = registry.get("SVE-005")()
        tool = registry.get("P4T-F4DF1700FE55")()
        self.basic.attachment = [energy, tool]
        self.basic.hp -= 20
        printed_hp = self.evolution.hp
        action = UseItemAction(self.player.id, self.candy)
        reducer = self.candy.reduce_action(action, self.state)
        result = next(reducer)
        for expected in (self.evolution, self.basic):
            choices = result[3]["raw_available_actions"]
            self.assertEqual(choices.candidates, [expected])
            try:
                result = reducer.send(choices.action_for_candidate_indices([0]))
            except StopIteration:
                self.assertIs(expected, self.basic)
        self.assertEqual(self.player.bench, [self.evolution])
        self.assertEqual(self.evolution.hp, printed_hp - 20)
        self.assertIn(self.basic, self.evolution.evolved)
        self.assertIn(energy, self.evolution.attachment)
        self.assertIn(tool, self.evolution.attachment)
        self.assertEqual(self.evolution.cardPosition, CardPosition.BENCH)
        self.assertIn(self.candy, self.player.discard)
        self.assertNotIn(self.candy, self.player.hand)
        self.assertNotIn(self.evolution, self.player.hand)


if __name__ == "__main__":
    unittest.main()
