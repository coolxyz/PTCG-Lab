"""Trusted offline rule-fork substrate, NOT an AI input or hidden-world sampler.

A continuation consists of an action-boundary checkpoint and the choices made
inside that action. Generators are reconstructed by replaying only that action.
Setup uses deterministic reconstruction until the first regular decision.
Private checkpoint objects must never cross the ObservationDTO boundary.
"""

import copy
from dataclasses import dataclass
from packages.battle.runtime import Adapter, ENGINE_VERSION, PlayerId


def resume(env, initial):
    action = yield initial
    while True:
        action = env._validate_action(action)
        env._log_action(action)
        yield from env._execute_action(action)
        result = env._prepare_step_result()
        if result[2]:
            env._handle_game_end(result[3])
        else:
            env.state_checker.check(env.gamestate)
        action = yield result


@dataclass
class Continuation:
    engine_version: str
    checkpoint: dict | None
    config: dict
    choices: list

    def to_json(self):
        from packages.simulation.checkpoint import dumps

        return dumps(
            {
                "engine_version": self.engine_version,
                "checkpoint": self.checkpoint,
                "config": self.config,
                "choices": self.choices,
            }
        )

    @classmethod
    def from_json(cls, payload):
        from packages.simulation.checkpoint import loads

        data = loads(payload)
        if not isinstance(data, dict) or set(data) != {
            "engine_version",
            "checkpoint",
            "config",
            "choices",
        }:
            raise ValueError("CONTINUATION_SCHEMA")
        if data["engine_version"] != ENGINE_VERSION:
            raise ValueError("ENGINE_VERSION_MISMATCH")
        if not isinstance(data["config"], dict) or not isinstance(
            data["choices"], list
        ):
            raise ValueError("CONTINUATION_SCHEMA")
        return cls(**data)


class RuleFork:
    def __init__(self, game):
        if game.commands:
            raise ValueError(
                "Wrap a fresh adapter before playing; do not guess a paused generator"
            )
        self.game = game
        self.base = None
        self.trail = []

    @staticmethod
    def regular(game):
        return (
            game.env.phase == "playing"
            and not game.done
            and isinstance(game.actions, list)
            and game.env.reducer.gi_yieldfrom is None
            and not game.env.gamestate.is_choosing_card
            and all(hasattr(a, "source") for a in game.actions)
        )

    def submit(self, actor, command):
        game = self.game
        if self.regular(game):
            # Copy in a single operation so all card/action/RNG references retain
            # their identity relationships. The suspended generator is excluded.
            self.base = copy.deepcopy(
                {
                    "game": {k: v for k, v in vars(game).items() if k != "env"},
                    "env": {k: v for k, v in vars(game.env).items() if k != "reducer"},
                }
            )
            self.trail = []
        before = game.version
        result = game.submit(actor, command)
        if game.version != before:
            self.trail.append({"actor": actor.name, "command": copy.deepcopy(command)})
        return result

    def checkpoint(self):
        return Continuation(
            ENGINE_VERSION,
            copy.deepcopy(self.base),
            copy.deepcopy(self.game.config),
            copy.deepcopy(self.trail),
        )

    @classmethod
    def restore(cls, token):
        if token.engine_version != ENGINE_VERSION:
            raise ValueError("ENGINE_VERSION_MISMATCH")
        if token.checkpoint is None:
            if "openingProposal" in token.config:
                from packages.simulation.proposals import proposal_game

                game = proposal_game(token.config)
            else:
                game = Adapter(**token.config)
        else:
            state = copy.deepcopy(token.checkpoint)
            game = Adapter.__new__(Adapter)
            from packages.rules.engine import RulesEngine

            env = RulesEngine.__new__(RulesEngine)
            vars(env).update(state["env"])
            vars(game).update(state["game"])
            game.env = env
            env.reducer = resume(env, (game.obs, 0, game.done, game.info))
            next(env.reducer)
        branch = cls.__new__(cls)
        branch.game = game
        branch.base = copy.deepcopy(token.checkpoint)
        branch.trail = copy.deepcopy(token.choices)
        for row in token.choices:
            game.submit(PlayerId[row["actor"]], row["command"])
        return branch

    def fork(self):
        return self.restore(self.checkpoint())
