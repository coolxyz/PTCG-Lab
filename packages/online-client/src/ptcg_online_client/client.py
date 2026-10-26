"""Native v2 client. GUI callers receive identities/views, never credentials."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import re
import secrets
from threading import Event
import time
import uuid
from websockets.exceptions import WebSocketException, InvalidStatus
from .contracts import ClientError, checked, unpack
from .transport import Transport
from .vault import Vault


def segment(value, *, command=False):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}" if command else r"[a-f0-9]{32}", value
    ):
        raise ClientError("INVALID_ID")
    return value


class Client:
    def __init__(self, server, storage, *, transport=None):
        self.transport = transport or Transport(server)
        self.vault = Vault(storage, server)
        self.stopped = Event()
        # Backfill old installations before the bounded snapshot cache evicts any
        # more matches. The index contains only metadata, never visible hands.
        try:
            with self.vault.transaction() as state:
                if "matchHistory" not in state:
                    state["matchHistory"] = {
                        mid: self._match_summary(view)
                        for mid, view in state["snapshots"].items()
                    }
                    self.vault.save()
        except Exception:
            self.vault.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.stopped.set()
        self.vault.close()

    def status(self):
        with self.vault.transaction() as state:
            credentials = state["credentials"]
            return {
                "identity": self._identity(credentials) if credentials else None,
                "reauthenticationRequired": state["refreshPending"],
                "pendingMatches": list(state["pending"]),
            }

    def recent_matches(self):
        """Member-scoped local index; never expose tokens, hands or controller data."""
        with self.vault.transaction() as state:
            if not state["credentials"]:
                return []
            return [
                self._match_summary(item)
                for item in reversed(list(state["matchHistory"].values()))
            ]

    @staticmethod
    def _match_summary(view):
        return {
            key: view[key]
            for key in ("matchId", "viewerSeat", "done", "winner", "matchSeq")
        }

    def recovery_status(self):
        """Safe recovery metadata for a trusted local bridge, never raw requests/tokens."""
        with self.vault.transaction() as state:
            return {
                "commands": [
                    {
                        "matchId": mid,
                        "commandId": item["body"]["commandId"],
                        "action": item["action"],
                    }
                    for mid, item in state["pending"].items()
                ],
                "controls": [
                    {
                        "matchId": mid,
                        "expectedControllerEpoch": item["expectedControllerEpoch"],
                    }
                    for mid, item in state.get("controlPending", {}).items()
                ],
            }

    def recover_control(self, match_id):
        segment(match_id)
        with self.vault.transaction() as state:
            pending = state.get("controlPending", {}).get(match_id)
            if pending is None:
                return None
            return self.take_control(match_id, pending["expectedControllerEpoch"])

    @staticmethod
    def _identity(credentials):
        return {
            k: credentials[k]
            for k in ("protocolVersion", "serverId", "userId", "deviceId", "sessionId")
        }

    @staticmethod
    def _backoff(attempt):
        time.sleep(min(0.15 * 2**attempt, 0.6) + secrets.randbelow(100) / 1000)

    def capabilities(self, rule_release_id=None):
        value = checked(
            "Capabilities", self._request("GET", "/capabilities", authenticated=False)
        )
        if "2.0" not in value["supportedProtocolVersions"]:
            raise ClientError("UNSUPPORTED_PROTOCOL")
        if (
            rule_release_id is not None
            and rule_release_id not in value["ruleReleaseIds"]
        ):
            raise ClientError("RULE_RELEASE_UNAVAILABLE")
        return value

    def create_guest(self, name="训练家", device_name="本地 APP"):
        with self.vault.transaction() as state:
            if state["credentials"] is not None or state["pending"]:
                raise ClientError("IDENTITY_ALREADY_EXISTS")
            self.capabilities()
            body = checked(
                "Create",
                {"protocolVersion": "2.0", "name": name, "deviceName": device_name},
            )
            credentials = checked(
                "Tokens", self._request("POST", "/sessions", body, authenticated=False)
            )
            state["credentials"] = credentials
            self.vault.save()
            return self._identity(credentials)

    def forget_identity(self, *, discard_pending=False):
        """Explicit local reset; guest identity is not recoverable without its tokens."""
        with self.vault.transaction() as state:
            if (
                state["pending"] or state.get("controlPending")
            ) and not discard_pending:
                raise ClientError("PENDING_COMMAND")
            state.update(
                credentials=None,
                refreshPending=False,
                controllers={},
                snapshots={},
                matchHistory={},
                pending={},
                controlPending={},
            )
            self.vault.save()

    def _access(self, rejected=None):
        # One native owner plus this lock coalesces refresh across all callers.
        with self.vault.transaction() as state:
            current = state["credentials"]
            if current is None or state["refreshPending"]:
                raise ClientError("REAUTHENTICATION_REQUIRED")
            checked("Tokens", current)
            expires = datetime.fromisoformat(current["accessExpiresAt"])
            fresh = (expires - datetime.now(timezone.utc)).total_seconds() > 20
            if (rejected is None and fresh) or (
                rejected is not None and rejected != current["accessToken"]
            ):
                return current["accessToken"]
            state["refreshPending"] = True
            self.vault.save()  # Never reuse a refresh after an unknown response/crash.
            try:
                rotated = checked(
                    "Tokens",
                    self.transport.request(
                        "POST",
                        "/sessions/refresh",
                        {
                            "protocolVersion": "2.0",
                            "refreshToken": current["refreshToken"],
                        },
                    ),
                )
                if self._identity(rotated) != self._identity(current):
                    raise ClientError("IDENTITY_MISMATCH")
            except ClientError:
                raise ClientError("REAUTHENTICATION_REQUIRED") from None
            state.update(credentials=rotated, refreshPending=False)
            self.vault.save()
            return rotated["accessToken"]

    def _request(
        self,
        method,
        path,
        body=None,
        *,
        authenticated=True,
        controller=None,
        retry_write=False,
    ):
        token = self._access() if authenticated else None
        refreshed = False
        attempts = 3 if method == "GET" or retry_write else 1
        for attempt in range(attempts):
            try:
                try:
                    return self.transport.request(method, path, body, token, controller)
                except ClientError as error:
                    if error.status == 401 and authenticated and not refreshed:
                        token, refreshed = self._access(rejected=token), True
                        return self.transport.request(
                            method, path, body, token, controller
                        )
                    raise
            except ClientError as error:
                if (
                    not error.retryable
                    or attempt + 1 == attempts
                    or error.status == 429
                ):
                    raise
                self._backoff(attempt)

    def _lobby(self, kind, path, body, result="RoomView"):
        payload = checked(
            kind, {"protocolVersion": "2.0", "requestId": uuid.uuid4().hex, **body}
        )
        return checked(result, self._request("POST", path, payload, retry_write=True))

    def submit_deck(self, entries, rule_release_id, *, request_id=None):
        self.capabilities(rule_release_id)
        return self._lobby(
            "DeckSubmission",
            "/decks",
            {
                "entries": entries,
                "ruleReleaseId": rule_release_id,
                **({"requestId": request_id} if request_id else {}),
            },
            "DeckAccepted",
        )

    def create_room(self, revision, rule_release_id, *, request_id=None):
        self.capabilities(rule_release_id)
        return self._lobby(
            "RoomCreate",
            "/rooms",
            {
                "deckRevision": revision,
                "ruleReleaseId": rule_release_id,
                **({"requestId": request_id} if request_id else {}),
            },
        )

    def join_room(self, invite_code, revision, *, request_id=None):
        return self._lobby(
            "RoomJoin",
            "/rooms/join",
            {
                "inviteCode": invite_code,
                "deckRevision": revision,
                **({"requestId": request_id} if request_id else {}),
            },
        )

    def room(self, room_id):
        return checked("RoomView", self._request("GET", "/rooms/" + segment(room_id)))

    def change_room(
        self, room_id, action, version, *, ready=None, revision=None, request_id=None
    ):
        kinds = {"ready": "RoomReady", "deck": "RoomDeck", "leave": "RoomLeave"}
        if action not in kinds:
            raise ClientError("INVALID_ACTION")
        extra = (
            {"ready": ready}
            if action == "ready"
            else {"deckRevision": revision}
            if action == "deck"
            else {}
        )
        return self._lobby(
            kinds[action],
            "/rooms/" + segment(room_id) + "/" + action,
            {
                "expectedRoomVersion": version,
                **extra,
                **({"requestId": request_id} if request_id else {}),
            },
            "RoomLeft" if action == "leave" else "RoomView",
        )

    def _snapshot(self, match_id, value):
        view = checked("MatchSnapshot", value)
        if view["matchId"] != match_id:
            raise ClientError("IDENTITY_MISMATCH")
        with self.vault.transaction() as state:
            old = state["snapshots"].get(match_id)
            if old and (
                old["viewerSeat"] != view["viewerSeat"]
                or old["ruleReleaseId"] != view["ruleReleaseId"]
            ):
                raise ClientError("IDENTITY_MISMATCH")
            if old and (
                view["matchSeq"] < old["matchSeq"]
                or view["controllerEpoch"] < old["controllerEpoch"]
            ):
                raise ClientError("STALE_STREAM")
            state["snapshots"][match_id] = deepcopy(view)
            state["matchHistory"][match_id] = self._match_summary(view)
            while len(state["snapshots"]) > 8:
                del state["snapshots"][
                    next(k for k in state["snapshots"] if k != match_id)
                ]
            self.vault.save()  # Cursor and visible snapshot are durable together.
        return view

    def snapshot(self, match_id):
        value = self._request("GET", "/matches/" + segment(match_id))
        try:
            return self._snapshot(match_id, value)
        except ClientError as error:
            if error.code != "STALE_STREAM":
                raise
            # A WSS update may finish while this HTTP snapshot is in flight.
            # _snapshot has already checked identity; never replace newer state.
            with self.vault.transaction() as state:
                latest = state["snapshots"].get(match_id)
                if latest is None:
                    raise error
                return deepcopy(latest)

    def take_control(self, match_id, expected_epoch):
        path = "/matches/" + segment(match_id)
        with self.vault.transaction() as state:
            if match_id in state["pending"]:
                raise ClientError("PENDING_COMMAND")
            controller = state["controllers"].setdefault(match_id, uuid.uuid4().hex)
            pending = state.setdefault("controlPending", {})
            body = pending.get(match_id) or checked(
                "MatchControl",
                {
                    "protocolVersion": "2.0",
                    "requestId": uuid.uuid4().hex,
                    "controllerId": controller,
                    "expectedControllerEpoch": expected_epoch,
                },
            )
            if body["expectedControllerEpoch"] != expected_epoch:
                raise ClientError("PENDING_CONTROL")
            pending[match_id] = body
            self.vault.save()
            try:
                value = self._request("POST", path + "/control", body, retry_write=True)
            except ClientError as error:
                if error.status in (400, 403, 409, 422) and not error.retryable:
                    del pending[match_id]
                    self.vault.save()
                raise
            # An old control receipt can be stale; always display the current view.
            checked("MatchSnapshot", value)
            del pending[match_id]
            self.vault.save()
            return self.snapshot(match_id)

    @staticmethod
    def _receipt(value, match_id, command_id):
        fields = {
            "protocolVersion",
            "matchId",
            "commandId",
            "accepted",
            "matchSeq",
            "stateVersion",
            "streamCursor",
        }
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value["protocolVersion"] != "2.0"
            or value["matchId"] != match_id
            or value["commandId"] != command_id
            or value["accepted"] is not True
            or any(
                type(value[k]) is not int or not 0 <= value[k] <= 9007199254740991
                for k in ("matchSeq", "stateVersion")
            )
            or not isinstance(value["streamCursor"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", value["streamCursor"])
        ):
            raise ClientError("INVALID_RESPONSE")
        return value

    def submit(self, match_id, command, *, resign=False):
        segment(match_id)
        checked("MatchResign" if resign else "Command", command)
        segment(command["commandId"], command=True)
        with self.vault.transaction() as state:
            controller = state["controllers"].get(match_id)
            if not controller:
                raise ClientError("CONTROL_REQUIRED")
            pending = {
                "body": deepcopy(command),
                "controller": controller,
                "action": "resign" if resign else "commands",
            }
            old = state["pending"].get(match_id)
            if old:
                if old != pending:
                    raise ClientError("PENDING_COMMAND")
                return self.recover(match_id)
            if len(state["pending"]) >= 8:
                raise ClientError("STORAGE_CAPACITY")
            state["pending"][match_id] = pending
            self.vault.save()
            return self._send_pending(match_id, pending, fresh=True)

    def _send_pending(self, match_id, pending, *, fresh=False):
        with self.vault.transaction() as state:
            try:
                result = self._request(
                    "POST",
                    "/matches/" + match_id + "/" + pending["action"],
                    pending["body"],
                    controller=pending["controller"],
                )
                receipt = self._receipt(result, match_id, pending["body"]["commandId"])
            except ClientError as error:
                if (
                    fresh
                    and error.status in (400, 403, 409, 422)
                    and not error.retryable
                ):
                    del state["pending"][match_id]
                    self.vault.save()
                raise
            del state["pending"][match_id]
            self.vault.save()
            return receipt

    def recover(self, match_id):
        segment(match_id)
        with self.vault.transaction() as state:
            pending = state["pending"].get(match_id)
            if not pending:
                return None
            command_id = segment(pending["body"]["commandId"], command=True)
            try:
                value = self._request(
                    "GET", "/matches/" + match_id + "/receipts/" + command_id
                )
            except ClientError as error:
                if error.status != 404:
                    raise
                # Retry the exact durable request, never manufacture a new ID.
                return self._send_pending(match_id, pending)
            receipt = self._receipt(value, match_id, command_id)
            del state["pending"][match_id]
            self.vault.save()
            return receipt

    def replay(self, match_id, cursor=None):
        path = "/matches/" + segment(match_id) + "/frames"
        if cursor is not None:
            path += "/" + segment(cursor)
        page = checked("ReplayPage", self._request("GET", path))
        view = self.snapshot(match_id)
        if any(page[k] != view[k] for k in ("matchId", "ruleReleaseId", "viewerSeat")):
            raise ClientError("IDENTITY_MISMATCH")
        return page

    def resolve_rejected(self, match_id):
        """Clear an unknown request only when current server fences rule out a late commit."""
        segment(match_id)
        with self.vault.transaction() as state:
            pending = state["pending"].get(match_id)
            if not pending:
                return None
            view = self.snapshot(match_id)
            body = pending["body"]
            if (
                view["matchSeq"] == body["expectedMatchSeq"]
                and view["controllerEpoch"] == body["controllerEpoch"]
            ):
                raise ClientError("OUTCOME_UNKNOWN")
            try:
                receipt = self._request(
                    "GET",
                    "/matches/"
                    + match_id
                    + "/receipts/"
                    + segment(body["commandId"], command=True),
                )
            except ClientError as error:
                if error.status != 404:
                    raise
                receipt = None
            if receipt is not None:
                receipt = self._receipt(receipt, match_id, body["commandId"])
            del state["pending"][match_id]
            self.vault.save()
            return receipt

    def events(self, match_id, stop=None):
        """Bounded reconnects per outage; persisted frames keep cursor/display aligned."""
        stop = stop or self.stopped
        view = self.snapshot(match_id)
        cursor, failures = view["streamCursor"], 0
        yield {"type": "snapshot", "snapshot": view}
        while not stop.is_set() and not self.stopped.is_set():
            token = self._access()
            try:
                with self.transport.stream(token) as ws:
                    ws.send(
                        json.dumps(
                            checked(
                                "MatchSubscribe",
                                {
                                    "protocolVersion": "2.0",
                                    "matchId": match_id,
                                    "streamCursor": cursor,
                                },
                            )
                        )
                    )
                    idle = time.monotonic()
                    while not stop.is_set() and not self.stopped.is_set():
                        try:
                            raw = ws.recv(timeout=1)
                        except TimeoutError:
                            if time.monotonic() - idle > 40:
                                raise ClientError("STREAM_TIMEOUT", retryable=True)
                            continue
                        idle = time.monotonic()
                        event = unpack(raw)
                        if isinstance(event, dict) and "code" in event:
                            error = checked("ProtocolError", event)
                            raise ClientError(
                                error["code"], retryable=error["retryable"]
                            )
                        if (
                            not isinstance(event, dict)
                            or event.get("protocolVersion") != "2.0"
                        ):
                            raise ClientError("INVALID_RESPONSE")
                        if event.get("type") == "heartbeat":
                            if event.get("streamCursor") != cursor:
                                raise ClientError("INVALID_RESPONSE")
                            continue
                        if event.get("type") not in (
                            "snapshot",
                            "update",
                            "control",
                        ) or set(event) != {"type", "snapshot", "protocolVersion"}:
                            raise ClientError("INVALID_RESPONSE")
                        view = self._snapshot(match_id, event["snapshot"])
                        cursor, failures = view["streamCursor"], 0
                        yield {"type": event["type"], "snapshot": view}
                return
            except ClientError as error:
                if error.code in ("RESYNC_REQUIRED", "STALE_STREAM"):
                    cursor = None
                elif error.code == "UNAUTHORIZED":
                    self._access(rejected=token)
                elif not error.retryable:
                    raise
            except InvalidStatus as error:
                if error.response.status_code == 403:
                    checked("Identity", self._request("GET", "/sessions/current"))
                elif error.response.status_code not in (429, 503):
                    raise ClientError("STREAM_REJECTED") from None
            except (OSError, WebSocketException):
                pass
            failures += 1
            if failures >= 3:
                raise ClientError("STREAM_UNAVAILABLE", retryable=True)
            if stop.wait(min(0.2 * 2**failures, 1) + secrets.randbelow(100) / 1000):
                return
