"""Native-owned online bridge. No application database, engine or AI dependencies."""

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re
import secrets
from threading import Event, Lock, RLock, Thread
import time
from ptcg_online_client.client import Client, segment
from ptcg_online_client.contracts import ClientError, checked


class BridgeError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def fields(body, required=(), optional=()):
    if (
        not isinstance(body, dict)
        or not set(required) <= body.keys()
        or not body.keys() <= set(required) | set(optional)
    ):
        raise BridgeError("INVALID_REQUEST")


@dataclass
class Profile:
    server: object
    lock: object = field(default_factory=Lock)
    client: object = None
    leases: dict = field(default_factory=dict)


@dataclass
class Feed:
    watchers: dict = field(default_factory=dict)
    stop: object = field(default_factory=Event)
    thread: object = None
    revision: int = 0
    event: object = None
    error: object = None
    done: bool = False


class Bridge:
    """A single trusted native owner shared by authorized browser windows."""

    def __init__(self, profiles, storage, *, client_factory=Client, allow_offline=False):
        if not (0 if allow_offline else 1) <= len(profiles) <= 8 or any(
            not re.fullmatch(r"[a-z0-9-]{1,32}", key) for key in profiles
        ):
            raise ValueError("Invalid native profile configuration")
        if len({s.key for s in profiles.values()}) != len(profiles):
            raise ValueError("Duplicate server profile")
        self.profiles = {key: Profile(server) for key, server in profiles.items()}
        self.storage, self.factory = Path(storage), client_factory
        self.lock, self.closed = RLock(), False
        self.windows, self.feeds = {}, {}
        self.product = None
        self.stopped = Event()
        self.reaper = Thread(target=self._reap, daemon=True)
        self.reaper.start()

    def _reap(self):
        while not self.stopped.wait(1):
            with self.lock:
                self._prune()

    def issue_window(self):
        """Native host only: deliver via trusted shell IPC, never a public HTTP route."""
        with self.lock:
            self._prune()
            if self.closed:
                raise BridgeError("BRIDGE_CLOSED", 503)
            if len(self.windows) >= 8:
                raise BridgeError("WINDOW_LIMIT", 429)
            token = secrets.token_urlsafe(32)
            self.windows[token] = time.monotonic() + 8 * 3600
            return token

    def authorize(self, token):
        with self.lock:
            self._prune()
            if self.closed:
                raise BridgeError("BRIDGE_CLOSED", 503)
            if token not in self.windows:
                raise BridgeError("LOCAL_AUTH_REQUIRED", 403)

    def _prune(self):
        now = time.monotonic()
        for token, expiry in list(self.windows.items()):
            if expiry <= now:
                self._revoke(token)
        for feed in self.feeds.values():
            feed.watchers = {k: t for k, t in feed.watchers.items() if t > now}
            if not feed.watchers:
                feed.stop.set()

    def _revoke(self, token):
        self.windows.pop(token, None)
        for feed in self.feeds.values():
            feed.watchers.pop(token, None)
            if not feed.watchers:
                feed.stop.set()
        # Lease capability remains unusable because authorization is checked first.

    def revoke_window(self, token):
        with self.lock:
            self._revoke(token)

    def list_profiles(self, token):
        self.authorize(token)
        return [
            {"id": key, "origin": p.server.origin} for key, p in self.profiles.items()
        ]

    def call(self, token, profile_id, action, body):
        self.authorize(token)
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise BridgeError("UNKNOWN_PROFILE", 404)
        if action in ("product-decks", "product-freeze", "product-check"):
            if self.product is None:
                raise BridgeError("PRODUCT_UNAVAILABLE", 404)
            # Local SQLite work must not contend with the remote profile owner
            # during initial status/capabilities loading or a slow remote request.
            return self.product.call(profile, action, body)
        # Reject rather than enqueue unbounded remote calls on one owner.
        if not profile.lock.acquire(blocking=False):
            raise BridgeError("PROFILE_BUSY", 409)
        try:
            self.authorize(token)
            if profile.client is None:
                profile.client = self.factory(profile.server, self.storage)
            return self._dispatch(profile, token, action, body)
        finally:
            profile.lock.release()

    def _dispatch(self, p, token, action, body):
        c = p.client
        if action in (
            "product-decks",
            "product-deck",
            "product-freeze",
            "product-check",
        ):
            if self.product is None:
                raise BridgeError("PRODUCT_UNAVAILABLE", 404)
            return self.product.call(p, action, body)
        if action in ("status", "capabilities"):
            fields(body)
            if action == "capabilities":
                return c.capabilities()
            return {
                **c.status(),
                "recovery": c.recovery_status(),
                "recentMatches": c.recent_matches(),
                **({"productPending": self.product.pending(p)} if self.product else {}),
            }
        if action == "guest":
            fields(body, optional=("name", "deviceName"))
            return c.create_guest(
                body.get("name", "训练家"), body.get("deviceName", "本地 APP")
            )
        if action == "deck":
            fields(body, ("entries", "ruleReleaseId", "requestId"))
            return c.submit_deck(
                body["entries"], body["ruleReleaseId"], request_id=body["requestId"]
            )
        if action == "create-room":
            fields(body, ("deckRevision", "ruleReleaseId", "requestId"))
            return c.create_room(
                body["deckRevision"],
                body["ruleReleaseId"],
                request_id=body["requestId"],
            )
        if action == "join-room":
            fields(body, ("inviteCode", "deckRevision", "requestId"))
            return c.join_room(
                body["inviteCode"], body["deckRevision"], request_id=body["requestId"]
            )
        if action == "room":
            fields(body, ("roomId",))
            return c.room(body["roomId"])
        if action in ("ready", "room-deck", "leave-room"):
            extra = {
                "ready": ("ready",),
                "room-deck": ("deckRevision",),
                "leave-room": (),
            }[action]
            fields(body, ("roomId", "expectedRoomVersion", "requestId", *extra))
            return c.change_room(
                body["roomId"],
                {"room-deck": "deck", "leave-room": "leave"}.get(action, action),
                body["expectedRoomVersion"],
                ready=body.get("ready"),
                revision=body.get("deckRevision"),
                request_id=body["requestId"],
            )
        if action in (
            "snapshot",
            "recover",
            "resolve-rejected",
            "watch",
            "events",
            "unwatch",
            "recover-control",
        ):
            fields(body, ("matchId",))
            mid = segment(body["matchId"])
            if action == "snapshot":
                return c.snapshot(mid)
            if action == "recover":
                return {"receipt": c.recover(mid)}
            if action == "resolve-rejected":
                return {"receipt": c.resolve_rejected(mid)}
            if action == "recover-control":
                if len(p.leases) >= 64 and mid not in p.leases:
                    raise BridgeError("CONTROL_LIMIT", 429)
                p.leases.pop(mid, None)
                view = c.recover_control(mid)
                if view is None:
                    raise BridgeError("NO_PENDING_CONTROL", 409)
                return self._lease(p, token, mid, view)
            return self._feed(c, token, mid, action)
        if action == "control":
            fields(body, ("matchId", "expectedControllerEpoch"))
            mid = segment(body["matchId"])
            if len(p.leases) >= 64 and mid not in p.leases:
                raise BridgeError("CONTROL_LIMIT", 429)
            p.leases.pop(mid, None)
            view = c.take_control(mid, body["expectedControllerEpoch"])
            return self._lease(p, token, mid, view)
        if action in ("command", "resign"):
            fields(body, ("matchId", "lease", "command"))
            mid = segment(body["matchId"])
            if not isinstance(body["lease"], str) or p.leases.get(mid) != (
                token,
                body["lease"],
            ):
                raise BridgeError("LOCAL_CONTROL_REQUIRED", 409)
            return c.submit(mid, body["command"], resign=action == "resign")
        if action == "replay":
            fields(body, ("matchId",), ("cursor",))
            return c.replay(body["matchId"], body.get("cursor"))
        # Identity deletion, arbitrary endpoints and remote headers are not exposed.
        raise BridgeError("UNKNOWN_OPERATION", 404)

    def _lease(self, p, token, mid, view):
        lease = secrets.token_urlsafe(32)
        p.leases[mid] = (token, lease)
        return {"lease": lease, "snapshot": view}

    def _feed(self, client, token, mid, action):
        key = (client, mid)
        with self.lock:
            self._prune()
            feed = self.feeds.get(key)
            if action == "unwatch":
                if feed:
                    feed.watchers.pop(token, None)
                    if not feed.watchers:
                        feed.stop.set()
                return {"watching": False}
            if action == "watch":
                if feed and feed.stop.is_set() and not feed.done:
                    raise BridgeError("STREAM_STOPPING", 409)
                if feed is None or feed.done:
                    self.feeds = {k: f for k, f in self.feeds.items() if not f.done}
                    if len(self.feeds) >= 4:
                        raise BridgeError("STREAM_LIMIT", 429)
                    feed = Feed()
                    self.feeds[key] = feed
                    feed.watchers[token] = time.monotonic() + 60
                    feed.thread = Thread(
                        target=self._pump, args=(client, mid, feed), daemon=True
                    )
                    feed.thread.start()
                else:
                    feed.watchers[token] = time.monotonic() + 60
            elif feed is None or token not in feed.watchers:
                raise BridgeError("NOT_WATCHING", 409)
            feed.watchers[token] = time.monotonic() + 60
            return deepcopy(
                {
                    "revision": feed.revision,
                    "event": feed.event,
                    "error": feed.error,
                    "done": feed.done,
                }
            )

    def _pump(self, client, mid, feed):
        # One stream per profile/match; slow windows only retain the latest view.
        try:
            for event in client.events(mid, feed.stop):
                checked("MatchSnapshot", event["snapshot"])
                with self.lock:
                    self._prune()
                    if feed.stop.is_set() or self.closed:
                        break
                    feed.event = {
                        "type": event["type"],
                        "snapshot": deepcopy(event["snapshot"]),
                    }
                    feed.revision += 1
        except ClientError as error:
            with self.lock:
                feed.error = {"code": error.code, "retryable": error.retryable}
        except Exception:
            with self.lock:
                feed.error = {"code": "STREAM_FAILED", "retryable": False}
        finally:
            with self.lock:
                feed.done = True

    def close(self, timeout=20):
        deadline = time.monotonic() + max(0, timeout)
        with self.lock:
            self.closed = True
            self.stopped.set()
            self.windows.clear()
            feeds = list(self.feeds.values())
            for feed in feeds:
                feed.stop.set()
        # Keep locks on still-running owners; the native host can retry shutdown.
        self.reaper.join(timeout=max(0, deadline - time.monotonic()))
        for feed in feeds:
            if feed.thread:
                feed.thread.join(timeout=max(0, deadline - time.monotonic()))
        pending = False
        for p in self.profiles.values():
            if not p.lock.acquire(timeout=max(0, deadline - time.monotonic())):
                pending = True
                continue
            try:
                if p.client:
                    if any(
                        f.thread and f.thread.is_alive()
                        for (c, _), f in self.feeds.items()
                        if c is p.client
                    ):
                        pending = True
                        continue
                    p.client.close()
            finally:
                p.lock.release()
        if pending:
            raise BridgeError("SHUTDOWN_PENDING", 503)
