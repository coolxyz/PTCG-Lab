"""Information-set root search. Never accepts an authoritative continuation.

Only independently generated, full-history-checked particles enter rollouts.
Each continuation policy reads its own actor DTO, including opponent responses.
Root actions are compared across the same particles to avoid sample-selection bias.
"""

import itertools
import json
import math
from packages.battle.agent import predict
from packages.battle.runtime import PlayerId, view
from packages.simulation.fork import RuleFork
from packages.simulation.heuristic import attack_potential, deficit, value
from packages.simulation.information import observation, sample


def candidates(dto, limit):
    first = predict(dto)[0]["choice"]
    choices = [first]
    decision = dto["decision"]
    if decision["kind"] == "options":
        # Rank each remaining action with the frozen DTO-only baseline. It sees
        # precisely the same information as the search, no candidate hidden state.
        remaining = list(decision["options"])
        while remaining and len(choices) < limit:
            subset = {**dto, "decision": {**decision, "options": remaining}}
            choice = predict(subset)[0]["choice"]
            remaining = [o for o in remaining if o["id"] != choice["optionId"]]
            if choice not in choices:
                choices.append(choice)
    else:
        refs = [c["ref"] for c in decision["candidates"]]
        # Include minimum/maximum counts and single replacements of the baseline
        # set before enumerating small subsets. No exponential full materialization.
        chosen = first["selectedRefs"]
        variants = [refs[: decision["min"]], refs[: decision["max"]]]
        variants += [
            chosen[:i] + [r] + chosen[i + 1 :]
            for i in range(len(chosen))
            for r in refs
            if r not in chosen
        ]
        for selection in itertools.chain(
            variants,
            itertools.islice(itertools.combinations(refs, decision["min"]), limit),
        ):
            choice = {"selectedRefs": list(selection)}
            if choice not in choices:
                choices.append(choice)
            if len(choices) >= limit:
                break
    return choices[:limit]


def utility(dto, viewer):
    if dto["done"]:
        return (
            100000
            if dto.get("winner") == viewer.name
            else -100000
            if dto.get("winner")
            else 0
        )
    own, other = dto["observation"]["self"], dto["observation"]["opponent"]

    def board(side):
        total = 0.0
        for c in side["active"] + side["bench"]:
            total += 30 + max(0, c.get("hp", 0)) * 0.3
            potential = attack_potential(c)
            readiness = min(
                (
                    deficit(a.get("cost", []), c.get("energy", []))
                    for a in c.get("attacks", [])
                ),
                default=5,
            )
            total += potential / (1 + readiness) * 0.5
            total += 8 * len(c.get("energy", []))
        return total

    # Only the viewer's known hand is evaluated. Opponent resources use public
    # counts, not the particle's hidden card identities or deck order.
    return (
        (other["prize_count"] - own["prize_count"]) * 350
        + board(own)
        - board(other)
        + sum(value(c, own, other) for c in own["hand"]) * 0.15
        + 3 * (own["hand_count"] - other["hand_count"])
        - (150 if own["deck_count"] < 3 else 0)
    )


def command(dto, choice):
    return {
        "commandId": f"a2-{dto['stateVersion']}",
        "expectedStateVersion": dto["stateVersion"],
        "decisionId": dto["decision"]["id"],
        "choice": choice,
    }


def search(info, budget, emit=lambda result: None, *, sampling_seed=0):
    info.validate()
    root = info.observations[-1]
    if root.get("decision") is None:
        raise ValueError("A2_NOT_ACTOR")
    viewer = PlayerId[info.viewer]
    choices = candidates(root, budget.candidates)
    result = {
        "command": command(root, choices[0]),
        "status": "no_particles",
        "simulations": 0,
        "particles": 0,
    }
    emit(result.copy())
    sampled = sample(
        info, sampling_seed=sampling_seed, trials=budget.trials, count=budget.particles
    )
    particles = sampled["particles"]
    if not particles:
        return result
    # Never compare one root action on more/friendlier particles than another.
    max_candidates = min(len(choices), budget.simulations // len(particles))
    if max_candidates < 2:
        return result
    scores = []
    simulations = 0
    for choice in choices[:max_candidates]:
        values = []
        for token in particles:
            branch = RuleFork.restore(token)
            if observation(view(branch.game, viewer)) != root:
                raise ValueError("A2_PARTICLE_INFORMATION_MISMATCH")
            branch.submit(viewer, command(root, choice))
            for _ in range(budget.depth):
                if branch.game.done:
                    break
                # Deterministic public-observation policy: equivalent information
                # sets choose the same continuation across sampled hidden worlds.
                dto = view(branch.game, branch.game.actor)
                branch.submit(branch.game.actor, predict(dto)[0])
            values.append(utility(view(branch.game, viewer), viewer))
            simulations += 1
        mean = sum(values) / len(values)
        if not math.isfinite(mean):
            raise ValueError("A2_NONFINITE_VALUE")
        scores.append(mean)
        best = max(range(len(scores)), key=lambda i: (scores[i], -i))
        result = {
            "command": command(root, choices[best]),
            "status": "searched" if len(scores) >= 2 else "baseline_evaluated",
            "simulations": simulations,
            "particles": len(particles),
            "evaluatedCandidates": len(scores),
        }
        # Publish only whole candidate evaluations; a killed worker leaves a
        # complete, root-legal best-so-far choice rather than a partial traversal.
        emit(json.loads(json.dumps(result)))
    return result
