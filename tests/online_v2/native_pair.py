"""Two independent native identities; game decisions use only wire snapshots."""

import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
import uuid
from websockets.sync.client import connect
from tests.online_v2.test_process import call
from tests.online_v2.test_lobby import body


class NativePair:
    def __init__(self, origin, context):
        self.origin, self.context = origin, context
        self.controllers = [uuid.uuid4().hex for _ in range(2)]
        self.handshake_seconds = []

    def request(self, path, **kw):
        return call(self.origin, path, context=self.context, **kw)

    def prepare(self, inputs):
        self.tokens = [
            self.request("/sessions", body={"protocolVersion": "2.0"}) for _ in range(2)
        ]
        self.access = [t["accessToken"] for t in self.tokens]
        decks = [
            self.request(
                "/decks",
                access=self.access[i],
                body=body(
                    ruleReleaseId=inputs["release"], entries=inputs[f"deck{i+1}"]
                ),
            )["deckRevision"]
            for i in range(2)
        ]
        room = self.request(
            "/rooms",
            access=self.access[0],
            body=body(ruleReleaseId=inputs["release"], deckRevision=decks[0]),
        )
        room = self.request(
            "/rooms/join",
            access=self.access[1],
            body=body(inviteCode=room["inviteCode"], deckRevision=decks[1]),
        )
        self.roompath = "/rooms/" + room["roomId"]
        for i in range(2):
            room = self.request(
                self.roompath + "/ready",
                access=self.access[i],
                body=body(expectedRoomVersion=room["roomVersion"], ready=True),
            )
        self.mid = room["matchId"]
        self.path = "/matches/" + self.mid
        until = time.monotonic() + 25
        while True:
            try:
                self.view(0)
                break
            except HTTPError as error:
                assert error.code == 403 and time.monotonic() < until
                time.sleep(0.2)
        for i in range(2):
            self.request(
                self.path + "/control",
                access=self.access[i],
                body=body(controllerId=self.controllers[i], expectedControllerEpoch=0),
            )

    def view(self, i):
        return self.request(self.path, access=self.access[i])

    def submit(self, i, command):
        return self.request(
            self.path + "/commands",
            access=self.access[i],
            controller=self.controllers[i],
            body=command,
        )

    def stream(self, i, cursor=None):
        started = time.monotonic()
        ws = connect(
            self.origin.replace("https:", "wss:") + "/api/online/v2/stream",
            ssl=self.context,
            proxy=None,
            compression=None,
            additional_headers={"Authorization": "Bearer " + self.access[i]},
            # Handshake authenticates in SQL after durable rate accounting.
            # Allow the bounded DB operations to finish on the 0.5-CPU fixture;
            # no retries conceal a failed handshake.
            open_timeout=15,
            close_timeout=2,
        )
        self.handshake_seconds.append(time.monotonic() - started)
        ws.send(
            json.dumps(
                {"protocolVersion": "2.0", "matchId": self.mid, "streamCursor": cursor}
            )
        )
        return ws


def pushed(ws):
    while True:
        event = json.loads(ws.recv(timeout=8))
        if event.get("type") != "heartbeat":
            assert event["type"] in ("snapshot", "update", "control")
            return event["snapshot"]


def report(name, value):
    output = os.environ.get("PTCG_V2_REPORT_DIRECTORY")
    if output:
        # Only aggregate acceptance evidence, no tokens, IDs, seeds or card data.
        (Path(output) / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")
