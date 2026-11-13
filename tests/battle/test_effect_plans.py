"""Compare requests and rule commands in the same seeded battle positions."""

import json

import httpx
import pytest

from packages.battle import effect_plans, llm
from packages.battle.allocations import checked_command
from packages.battle.runtime import Adapter, PlayerId, view
from packages.rules.effects import Munkidori, TechnicalMachine
from ptcg.core.action import AttackAction, UseAbilityAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardPosition
from ptcg.utils.utils import move_cards
from test_effects import zone
from test_rules import finish_setup, pass_turn
from tests.battle.test_llm import setup


ENERGIES = [
    "P4E-001",
    "SVE-004",
    "SVE-008",
    "SVE-002",
    "SVE-005",
    "SVE-007",
    "P4E-003",
    "P4E-006",
]
TRAINERS = ["PAF-091", "TEF-159", "PAF-080", "PAL-171"]


def card(id):
    return registry.get(id)()


def position(effect, variant):
    game = Adapter(7)
    finish_setup(game)
    if game.actor == PlayerId.PLAYER1:
        pass_turn(game)
    state = game.env.gamestate
    p, o = state.player2, state.player1
    active = (
        effect if effect in (effect_plans.JOLTIK, effect_plans.PIKACHU) else "TWM-128"
    )
    zone(p, "active", [card(active)])
    zone(p, "bench", [card("TWM-128"), Munkidori()])
    zone(o, "active", [card("TWM-200")])
    zone(o, "bench", [card("TWM-128")])
    p.firstTurn = p.supporterPlayedTurn = False
    zone(p, "hand", [card(id) for id in ENERGIES[:4]])
    zone(p, "left", [card(id) for id in ENERGIES + TRAINERS])
    if effect == effect_plans.ARVEN and variant == "no-item":
        zone(p, "left", [c for c in p.left if c.id != TRAINERS[0]])
    zone(p, "discard", [])

    def attach(target, *ids):
        for id in ids:
            energy = card(id)
            p.hand.append(energy)
            move_cards(
                energy,
                (p.id, CardPosition.HAND),
                (
                    p.id,
                    CardPosition.ACTIVE_ATTACHMENT
                    if target in p.active
                    else CardPosition.BENCH_ATTACHMENT,
                    target.index,
                ),
                state,
            )
        target.dynamic_energy = True

    if effect == effect_plans.JOLTIK:
        attach(p.active[0], "SVE-008")
        extras = ENERGIES[1:2] if variant == "no-grass" else ENERGIES[:2]
        if variant == "no-grass":
            zone(p, "left", [c for c in p.left if c.id != ENERGIES[0]])
        zone(p, "left", [*p.left, *map(card, extras)])
        trigger = p.active[0]
    elif effect == effect_plans.PIKACHU:
        attach(p.active[0], *ENERGIES[:3])
        if variant == "multi-unit":
            attach(p.active[0], "BRS-151")
        trigger = p.active[0]
    elif effect == effect_plans.MUNKIDORI:
        trigger = p.bench[1]
        attach(trigger, "SVE-007")
        p.active[0].hp -= 30
        p.bench[0].hp -= 10
    elif effect == effect_plans.EVOLUTION:
        zone(p, "bench", [card("TWM-128") for _ in range(3)])
        trigger = TechnicalMachine()
        p.active[0].attachment.append(trigger)
        trigger.cardPosition = CardPosition.ACTIVE_ATTACHMENT
        attach(p.active[0], "SVE-008")
        zone(p, "left", [*p.left, card("TWM-129"), card("TWM-129")])
    else:
        trigger = card(effect)
        zone(p, "hand", [*p.hand, trigger])
        if effect == effect_plans.ENERGY_SWITCH:
            attach(p.active[0], *ENERGIES[:2])
            attach(p.bench[0], ENERGIES[2])
    game.obs, _, game.done, game.info = game.env._yield_initial_state()
    index = next(
        i
        for i, action in enumerate(game.actions)
        if getattr(action, "effect_source", action.source) is trigger
        and (
            isinstance(action, (AttackAction, UseAbilityAction))
            or type(action).__name__ in ("UseItemAction", "UseSupporterAction")
        )
    )
    return game, {"optionId": f"d{game.version}:o{index}"}


def board_ref(dto, location):
    side, zone_name, index = location
    return dto["observation"][side][zone_name][index]["ref"]


ACTIVE = ("self", "active", 0)
FIRST_BENCH = ("self", "bench", 0)
SECOND_BENCH = ("self", "bench", 1)
OPP_BENCH = ("opponent", "bench", 0)


def pick(dto, picks):
    d = dto["decision"]
    if isinstance(picks, int):
        return {"optionId": next(o["id"] for o in d["options"] if o["value"] == picks)}
    refs = []
    for item in picks:
        candidate = next(
            c
            for c in d["candidates"]
            if c["ref"] not in refs
            and (
                c.get("boardRef") == board_ref(dto, item)
                if isinstance(item, tuple)
                else c["card"]["id"] == item
            )
        )
        refs.append(candidate["ref"])
    return {"selectedRefs": refs}


CASES = [
    (
        effect_plans.JOLTIK,
        "",
        6,
        1,
        [
            [ENERGIES[0]] * 2,
            [ACTIVE],
            [FIRST_BENCH],
            [ENERGIES[1]] * 2,
            [FIRST_BENCH],
            [FIRST_BENCH],
        ],
        {
            "grassTargets": [ACTIVE, FIRST_BENCH],
            "lightningTargets": [FIRST_BENCH, FIRST_BENCH],
        },
    ),
    (
        effect_plans.JOLTIK,
        "no-grass",
        3,
        1,
        [[], [ENERGIES[1]] * 2, [FIRST_BENCH], [FIRST_BENCH]],
        {"grassTargets": [], "lightningTargets": [FIRST_BENCH, FIRST_BENCH]},
    ),
    (
        effect_plans.SECRET_BOX,
        "",
        5,
        2,
        [ENERGIES[:3], *[[id] for id in TRAINERS]],
        {
            key: [id]
            for key, id in zip(("item", "tool", "supporter", "stadium"), TRAINERS)
        },
    ),
    (
        effect_plans.ARVEN,
        "",
        2,
        1,
        [[TRAINERS[0]], [TRAINERS[1]]],
        {"item": [TRAINERS[0]], "tool": [TRAINERS[1]]},
    ),
    (
        effect_plans.ARVEN,
        "skip-item",
        2,
        1,
        [[], [TRAINERS[1]]],
        {"item": [], "tool": [TRAINERS[1]]},
    ),
    (
        effect_plans.ARVEN,
        "no-item",
        1,
        1,
        [[], [TRAINERS[1]]],
        None,
    ),
    (
        effect_plans.CRISPIN,
        "",
        4,
        1,
        [[ENERGIES[0]], [ENERGIES[1]], [ENERGIES[0]], [SECOND_BENCH]],
        {"energies": ENERGIES[:2], "recipient": [SECOND_BENCH]},
    ),
    (
        effect_plans.CRISPIN,
        "stop",
        2,
        1,
        [[ENERGIES[0]], [], [ENERGIES[0]]],
        {"energies": ENERGIES[:1], "recipient": []},
    ),
    (
        effect_plans.MUNKIDORI,
        "",
        3,
        1,
        [[ACTIVE], 3, [OPP_BENCH]],
        {"donor": [ACTIVE], "count": ["3"], "target": [OPP_BENCH]},
    ),
    (
        effect_plans.EVOLUTION,
        "",
        3,
        2,
        [[SECOND_BENCH, FIRST_BENCH], ["TWM-129"], ["TWM-129"]],
        {"evolution1": ["TWM-129"], "evolution2": ["TWM-129"]},
    ),
    (
        effect_plans.ENERGY_SWITCH,
        "",
        3,
        1,
        [[ACTIVE], [ENERGIES[1]], [FIRST_BENCH]],
        {"energy": [("attached", ACTIVE, ENERGIES[1])], "recipient": [FIRST_BENCH]},
    ),
    (
        effect_plans.PIKACHU,
        "",
        2,
        1,
        [[ENERGIES[0]], [ENERGIES[1]], [ENERGIES[2]]],
        {"payment": ENERGIES[:3]},
    ),
    (
        effect_plans.PIKACHU,
        "multi-unit",
        3,
        1,
        [["BRS-151"], [ENERGIES[0]], []],
        {"payment": ["BRS-151", ENERGIES[0]]},
    ),
    (
        effect_plans.ENERGY_SEARCH_PRO,
        "",
        8,
        1,
        [[id] for id in ENERGIES],
        {"energies": ENERGIES},
    ),
    (
        effect_plans.ENERGY_SEARCH_PRO,
        "stop",
        4,
        1,
        [*[[id] for id in ENERGIES[:3]], []],
        {"energies": ENERGIES[:3]},
    ),
]


@pytest.mark.parametrize(
    ("effect", "variant", "baseline", "planned", "picks", "plan"),
    CASES,
    ids=[effect + (f"-{variant}" if variant else "") for effect, variant, *_ in CASES],
)
def test_plan_uses_fewer_provider_requests(
    tmp_path, monkeypatch, effect, variant, baseline, planned, picks, plan
):
    app, client, opened, config_path = setup(tmp_path, monkeypatch)
    service = app.state.battles
    owner = service.owner(client.cookies.get("ptcg_practice_session"))
    match_id = opened["matchId"]
    config = json.loads(config_path.read_text())
    config["responseFormat"] = "json_schema"
    config_path.write_text(json.dumps(config))
    real_window = effect_plans.window

    def run(planning):
        game, trigger = position(effect, variant)
        with service.db(True) as db:
            row = service._row(db, owner, match_id)
            game.config["provenance"] = json.loads(row["body"])["config"]["provenance"]
            game.config["provenance"]["llmConfigHash"] = llm.settings(
                service.path
            ).fingerprint()
            db.execute("DELETE FROM match_frames WHERE match_id=?", (match_id,))
            db.execute("DELETE FROM match_llm_steps WHERE match_id=?", (match_id,))
            current = view(game, game.actor)
            command = checked_command(current, trigger)
            service._save_llm_action(
                db,
                row,
                game,
                current,
                command,
                llm.decision_detail(
                    current, trigger, {"status": "forced", "requestCount": 0}
                ),
            )
        service.cache[match_id] = game
        first = game.version
        calls = []

        def respond(request):
            body = json.loads(request.content)
            visible = json.loads(body["messages"][-1]["content"])
            calls.append(visible)
            spec = visible.get("effectPlan")
            d = visible["decision"]
            if spec:
                if effect == effect_plans.ARVEN:
                    assert spec["effect"] == "arven"
                    assert set(spec["fields"]) == {"item", "tool"}
                    assert d["deckInspection"]["owner"] == "self"
                    assert d["deckInspection"]["ordered"] is False
                    assert d["deckInspection"]["total"] == len(ENERGIES + TRAINERS)
                    assert {
                        entry["card"]["id"] for entry in d["deckInspection"]["cards"]
                    } == set(ENERGIES + TRAINERS)
                    assert {c["card"]["id"] for c in d["candidates"]} == {TRAINERS[0]}
                fields = spec["fields"]
                answer = {}
                for key, items in plan.items():
                    refs = []
                    for item in items:
                        if isinstance(item, tuple) and item[0] == "attached":
                            ref = next(
                                o["ref"]
                                for o in fields[key]["options"]
                                if o["donor"] == board_ref(visible, item[1])
                                and o["name"] == card(item[2]).name
                            )
                        elif isinstance(item, tuple):
                            ref = board_ref(visible, item)
                        elif key == "payment":
                            ref = next(
                                c["ref"]
                                for c in d["candidates"]
                                if c["card"]["id"] == item and c["ref"] not in refs
                            )
                        else:
                            ref = item
                        refs.append(ref)
                    answer[key] = refs
                assert effect_plans.valid(answer, spec)
                schema = body["response_format"]["json_schema"]["schema"]
                assert schema["properties"]["choice"]["properties"]["effectPlan"][
                    "required"
                ] == list(fields)
                choice = {"effectPlan": answer}
            else:
                choice = pick(visible, picks[visible["stateVersion"] - first])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json.dumps(
                                    {"choice": choice, "reason": "同一局面比较调用次数"}
                                ),
                            },
                        }
                    ]
                },
            )

        with monkeypatch.context() as patch:
            patch.setattr(
                httpx,
                "Client",
                lambda **kwargs: real_client(transport=httpx.MockTransport(respond)),
            )
            patch.setattr(
                effect_plans, "window", real_window if planning else lambda *_: None
            )
            for _ in picks:
                if game.version >= first + len(picks):
                    break
                service.advance(owner, match_id)
            assert game.version == first + len(picks)
            service.close()

        with service.db() as db:
            rows = db.execute(
                "SELECT seq, body FROM match_llm_steps WHERE match_id=? AND seq>? ORDER BY seq",
                (match_id, first),
            ).fetchall()
            receipts = {row["seq"]: json.loads(row["body"]) for row in rows}
            public = db.execute(
                "SELECT body FROM match_frames WHERE match_id=? AND seq>? ORDER BY seq",
                (match_id, first),
            ).fetchall()
        assert len(public) == len(receipts) == len(picks)
        assert len(calls) == sum(r["requestCount"] for r in receipts.values())
        assert all(r["status"] != "fallback" for r in receipts.values()), receipts
        assert all("effectPlan" not in row["body"] for row in public)
        if planning and plan is not None:
            root = next(seq for seq, r in receipts.items() if "effectPlan" in r)
            assert all(
                r.get("effectPlanFromSeq") == root
                for seq, r in receipts.items()
                if seq > root
            )
            if effect in (effect_plans.SECRET_BOX, effect_plans.EVOLUTION):
                assert "deckInspection" not in calls[0]["decision"]
                assert "deckInspection" in calls[1]["decision"]
        if plan is None:
            assert all("effectPlan" not in call for call in calls)
        return len(calls), game.commands[-len(picks) :], game.private_digest()

    real_client = httpx.Client
    old, old_commands, old_digest = run(False)
    new, new_commands, new_digest = run(True)
    assert (old, new) == (baseline, planned)
    assert old_commands == new_commands
    assert old_digest == new_digest


def begin_effect(effect):
    game, trigger = position(effect, "")
    before = view(game, game.actor)
    history = [{"ownAction": llm.describe_choice(before["decision"], trigger)}]
    game.submit(game.actor, checked_command(before, trigger))
    before = view(game, game.actor)
    return game, before, effect_plans.window(game, before, history), history


def test_rejects_invalid_cross_field_plan_and_falls_back(monkeypatch):
    game, before, offer, history = begin_effect(effect_plans.MUNKIDORI)
    plan = {
        "donor": [board_ref(before, FIRST_BENCH)],
        "count": ["3"],  # The selected donor has only one counter.
        "target": [board_ref(before, OPP_BENCH)],
    }
    assert effect_plans.valid(plan, offer.visible())
    assert offer.steps(plan) is None
    monkeypatch.setattr(
        llm,
        "_request",
        lambda *_: {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps({"choice": {"effectPlan": plan}})
                    },
                }
            ]
        },
    )
    config = llm.Settings(baseUrl="https://provider.example/v1", model="test")
    command, receipt = llm.decide(before, history, config, game.config["deck2"], offer)
    assert receipt["failure"] == "illegal-effect-plan"
    assert receipt["status"] == "fallback" and receipt["requestCount"] == 1
    assert "effectPlan" not in receipt
    game.submit(game.actor, command)


def test_stops_before_submitting_a_step_for_a_different_prompt_source():
    game, before, offer, _ = begin_effect(effect_plans.ENERGY_SWITCH)
    plan = {
        "energy": [offer.fields["energy"]["options"][0]["ref"]],
        "recipient": [board_ref(before, FIRST_BENCH)],
    }
    replay = effect_plans.follow(game, before, offer.steps(plan), view)
    _, first = next(replay)
    game.submit(game.actor, first)
    game.info["prompt"].source = card(effect_plans.ENERGY_SWITCH)
    assert list(replay) == []
    assert game.version == before["stateVersion"] + 1
