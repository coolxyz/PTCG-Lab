"""A public action without an explicit card ID can commit and recover exactly once."""

from dataclasses import replace
import uuid

import pytest
from ptcg_online_server import public_events
from ptcg_online_server.initialization import Initializer
from ptcg_online_server.lobby import Lobby
from ptcg_online_server.matches import Matches
from ptcg_online_server.repository import Repository
from ptcg_protocol.validation import ProtocolViolation, validate
from tests.online_v2.test_lobby import RELEASE, prepare, body
from tests.online_v2.test_matches import GameWorker, command


class StadiumWorker(GameWorker):
    def project(self, record):
        value = super().project(record)
        for view in value["views"].values():
            source = dict(
                name="Synthetic stadium with spaces",
                set_name="SYN",
                number="042",
                ref="v0:stadium:0",
            )
            view["observation"]["stadium"] = [source]
            if view["decision"]:
                view["decision"]["options"][0].update(
                    actionType="UseStadiumAction", sourceRef=source["ref"]
                )
        return value

    def command(self, record, seat, body):
        before = self.project(record)["views"][seat]
        value = super().command(record, seat, body)
        events = public_events.action_events(before, seat, body["choice"])
        value["events"] = {s: events for s in value["views"]}
        return value


def test_invalid_event_rolls_back_then_same_command_commits_both_frames_once(
    repo, monkeypatch
):
    repo = Repository(replace(repo.settings, approved_rule_release_id=RELEASE))
    worker = StadiumWorker()
    lobby = Lobby(repo, worker)
    a, b, room, _ = prepare(lobby)
    assert Initializer(repo, worker).tick()
    service = Matches(repo, worker)
    assert service.promote_one()
    mid, controller = room["matchId"], uuid.uuid4().hex
    service.control(a, mid, body(controllerId=controller, expectedControllerEpoch=0))
    request = command(service.get(a, mid))

    corrected = public_events.printed_identity

    def old_identity(card):
        return {**corrected(card), "cardId": card["name"]}

    monkeypatch.setattr(public_events, "printed_identity", old_identity)
    with pytest.raises(ProtocolViolation):
        service.command(a, mid, request, controller)
    with repo.transaction() as db:
        assert (
            db.execute(
                "SELECT match_seq FROM ptcg_v2.matches WHERE id=%s", (mid,)
            ).fetchone()["match_seq"]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.match_receipts WHERE match_id=%s",
                (mid,),
            ).fetchone()["n"]
            == 0
        )

    monkeypatch.setattr(public_events, "printed_identity", corrected)
    receipt = service.command(a, mid, request, controller)
    assert receipt["matchSeq"] == 1
    assert service.command(a, mid, request, controller) == receipt
    for token in (a, b):
        frame = service.get(token, mid)
        validate("MatchSnapshot", frame)
        assert frame["matchSeq"] == 1
        assert (
            frame["events"][0]["cards"][0]["cardId"]
            == frame["observation"]["stadium"][0]["card"]["cardId"]
            == "SYN-042"
        )
        assert service.frames(token, mid)["frames"][-1]["events"] == frame["events"]
    with repo.transaction() as db:
        assert (
            db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.match_receipts WHERE match_id=%s",
                (mid,),
            ).fetchone()["n"]
            == 1
        )
        assert (
            db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.match_frames WHERE match_id=%s",
                (mid,),
            ).fetchone()["n"]
            == 4
        )
