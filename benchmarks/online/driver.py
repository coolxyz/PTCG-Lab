"""Two wire-only players per game. Private journals never enter aggregate reports."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import random
import re
import resource
import threading
import time
import uuid

from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server, Transport
from ptcg_protocol.projection import replay_page
from ptcg_protocol.validation import validate

try:
    from .measure import latency
except ImportError:
    from measure import latency

ORIGIN = "https://p7-api:8784"
LOCK = threading.Lock()
STOP = threading.Event()
PHASE = "setup"
METRICS = {
    p: {"http": [], "visible": [], "errors": {}, "accepted": 0}
    for p in ("warmup", "steady", "fault")
}
RECONNECT = []
REPLAY_SLOTS = threading.BoundedSemaphore(2)
OPERATIONS = (
    "session",
    "refresh",
    "deck",
    "room_create",
    "room_join",
    "room_ready",
    "snapshot",
    "initialization_wait",
    "control",
    "command",
    "receipt",
    "replay",
    "other",
)
REQUESTS = {p: {} for p in ("setup", "warmup", "steady", "fault", "cleanup")}
REPLAY_WAITS = []


def operation(path):
    fixed = {
        "/sessions": "session",
        "/sessions/refresh": "refresh",
        "/decks": "deck",
        "/rooms": "room_create",
        "/rooms/join": "room_join",
    }
    if path in fixed:
        return fixed[path]
    for suffix, name in (
        ("ready", "room_ready"),
        ("control", "control"),
        ("commands", "command"),
    ):
        if path.endswith("/" + suffix):
            return name
    if re.fullmatch(r"/matches/[^/]+/frames(?:/[^/]+)?", path):
        return "replay"
    if "/receipts/" in path:
        return "receipt"
    return "snapshot" if re.fullmatch(r"/matches/[^/]+", path) else "other"


def failure(error, stage):
    """Never stringify an exception or retain URLs, request data or credentials."""
    value = dict(
        type=type(error).__name__,
        operation=stage
        if stage in (*OPERATIONS, "stream_connect", "replay_verify")
        else "other",
    )
    if isinstance(error, ClientError):
        value.update(
            code=error.code
            if isinstance(error.code, str) and re.fullmatch(r"[A-Z_]{1,64}", error.code)
            else "REMOTE_FAILED",
            status=error.status
            if type(error.status) is int and 0 <= error.status <= 599
            else 0,
            retryable=error.retryable is True,
        )
    return value


def body(**kwargs):
    return dict(protocolVersion="2.0", requestId=uuid.uuid4().hex, **kwargs)


def digest(view):
    frame = replay_page([view])["frames"][0]
    return sha256(
        json.dumps(frame, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def choose(view):
    d = view["decision"]
    if d["kind"] == "selection":
        choice = {"selectedRefs": [c["ref"] for c in d["candidates"][: d["min"]]]}
    else:
        option = next(
            (o for o in d["options"] if o["actionType"] == "PassTurn"), d["options"][0]
        )
        choice = {"optionId": option["id"]}
    return dict(
        protocolVersion="2.0",
        commandId=uuid.uuid4().hex,
        expectedMatchSeq=view["matchSeq"],
        expectedStateVersion=view["stateVersion"],
        controllerEpoch=view["controllerEpoch"],
        decisionId=d["id"],
        choice=choice,
    )


def private(view, match_id, seat):
    validate("MatchSnapshot", view)
    assert view["matchId"] == match_id and view["viewerSeat"] == seat
    assert view["observation"]["opponent"]["hand"] is None
    assert not {"seed", "recovery", "stateDigest"} & view.keys()
    assert not view["decision"] or view["decision"]["actor"] == seat


def pushed(ws, timeout=15):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        event = json.loads(ws.recv(timeout=max(0.1, until - time.monotonic())))
        if event.get("type") == "heartbeat":
            continue
        if event.get("type") == "error" or "code" in event:
            raise ValueError("STREAM_ERROR")
        return event["snapshot"]
    raise TimeoutError("STREAM_DEADLINE")


class Game:
    def __init__(self, index, inputs, state):
        self.index, self.inputs, self.state = index, inputs, Path(state)
        self.transport = Transport(Server(ORIGIN, ca_file="/tls/server.pem"))
        self.controllers = [uuid.uuid4().hex for _ in range(2)]
        self.tokens, self.revisions, self.sockets, self.views = [], [], [], []
        self.hashes, self.last, self.pending = [{}, {}], None, None
        self.commands, self.duplicates, self.completed = 0, 0, 0
        self.current_operation = "other"

    def request(self, path, *, i=None, data=None, controller=False, initializing=False):
        name, phase = operation(path), PHASE
        if initializing:
            if name != "snapshot":
                raise ValueError("INITIALIZATION_POLL_REQUIRES_SNAPSHOT")
            name = "initialization_wait"
        self.current_operation = name
        started = time.perf_counter()
        if name == "replay":
            REPLAY_SLOTS.acquire()
            with LOCK:
                REPLAY_WAITS.append(time.perf_counter() - started)
        error, pending = None, False
        try:
            return self.transport.request(
                "POST" if data is not None else "GET",
                path,
                data,
                token=self.tokens[i]["accessToken"] if i is not None else None,
                controller=self.controllers[i] if controller else None,
            )
        except Exception as caught:
            error = failure(caught, name)
            pending = (
                name == "initialization_wait"
                and isinstance(caught, ClientError)
                and caught.status == 403
                and caught.code == "FORBIDDEN"
            )
            raise
        finally:
            if name == "replay":
                REPLAY_SLOTS.release()
            with LOCK:
                row = REQUESTS[phase].setdefault(
                    name,
                    dict(
                        attempts=0,
                        successes=0,
                        pending=0,
                        errors=0,
                        firstAttemptErrors=[],
                    ),
                )
                row["attempts"] += 1
                row["successes"] += error is None
                row["pending"] += pending
                if error is not None and not pending:
                    row["errors"] += 1
                    # The run stops on the first failure; keep a bounded ledger.
                    row["firstAttemptErrors"] = (row["firstAttemptErrors"] + [error])[
                        :32
                    ]

    def setup(self):
        self.tokens = [
            self.request(
                "/sessions", data=dict(protocolVersion="2.0", name="P7 test player")
            )
            for _ in range(2)
        ]
        self.revisions = [
            self.request(
                "/decks",
                i=i,
                data=body(
                    ruleReleaseId=self.inputs["release"],
                    entries=self.inputs[f"deck{i + 1}"],
                ),
            )["deckRevision"]
            for i in range(2)
        ]
        self.new_match()

    @property
    def route(self):
        return "/matches/" + self.mid

    def new_match(self):
        room = self.request(
            "/rooms",
            i=0,
            data=body(
                ruleReleaseId=self.inputs["release"], deckRevision=self.revisions[0]
            ),
        )
        room = self.request(
            "/rooms/join",
            i=1,
            data=body(inviteCode=room["inviteCode"], deckRevision=self.revisions[1]),
        )
        for i in range(2):
            room = self.request(
                "/rooms/" + room["roomId"] + "/ready",
                i=i,
                data=body(expectedRoomVersion=room["roomVersion"], ready=True),
            )
        self.mid = room["matchId"]
        until = time.monotonic() + 60
        while True:
            try:
                self.request(self.route, i=0, initializing=True)
                break
            except ClientError as e:
                if e.code != "FORBIDDEN" or time.monotonic() >= until:
                    raise
                time.sleep(0.25)
        for i in range(2):
            self.request(
                self.route + "/control",
                i=i,
                data=body(controllerId=self.controllers[i], expectedControllerEpoch=0),
            )
        self.hashes = [{}, {}]
        self.last = self.pending = None
        self.connect()

    def close(self):
        for ws in self.sockets:
            ws.close()
        self.sockets = []

    def connect(self):
        self.current_operation = "stream_connect"
        self.close()
        views = []
        for i in range(2):
            started = time.perf_counter()
            ws = self.transport.stream(self.tokens[i]["accessToken"])
            self.sockets.append(ws)
            ws.send(
                json.dumps(
                    dict(protocolVersion="2.0", matchId=self.mid, streamCursor=None)
                )
            )
            view = pushed(ws)
            private(view, self.mid, f"PLAYER{i + 1}")
            views.append(view)
            if PHASE in METRICS:
                with LOCK:
                    RECONNECT.append(time.perf_counter() - started)
        assert views[0]["matchSeq"] == views[1]["matchSeq"]
        self.views = views
        for i, view in enumerate(views):
            old = self.hashes[i].get(str(view["matchSeq"]))
            assert old is None or old == digest(view)
            self.hashes[i][str(view["matchSeq"])] = digest(view)

    def refresh(self):
        if any(
            datetime.fromisoformat(t["accessExpiresAt"])
            <= datetime.now(timezone.utc).replace(microsecond=0)
            for t in self.tokens
        ):
            raise ValueError("DRIVER_TOKEN_EXPIRED")
        # Rotate early; never let the steady test silently lose all WSS at 15 minutes.
        if any(
            (
                datetime.fromisoformat(t["accessExpiresAt"])
                - datetime.now(timezone.utc)
            ).total_seconds()
            < 180
            for t in self.tokens
        ):
            self.close()
            self.tokens = [
                self.request(
                    "/sessions/refresh",
                    data=dict(protocolVersion="2.0", refreshToken=t["refreshToken"]),
                )
                for t in self.tokens
            ]
            self.connect()

    def step(self):
        self.refresh()
        if self.views[0]["done"]:
            self.verify_replay()
            self.completed += 1
            self.close()
            self.new_match()
        actor = next(i for i, v in enumerate(self.views) if v["decision"])
        command = choose(self.views[actor])
        self.pending = dict(actor=actor, command=command)
        self.save()
        phase, started = PHASE, time.perf_counter()
        try:
            receipt = self.request(
                self.route + "/commands", i=actor, data=command, controller=True
            )
        except ClientError as e:
            with LOCK:
                if phase in METRICS:
                    errors = METRICS[phase]["errors"]
                    errors[e.code] = errors.get(e.code, 0) + 1
            # Unknown results remain journaled. Normal load never conceals a retry.
            raise
        http = time.perf_counter() - started
        assert (
            receipt["accepted"]
            and receipt["matchSeq"] == self.views[actor]["matchSeq"] + 1
        )
        views = [pushed(ws) for ws in self.sockets]
        visible = time.perf_counter() - started
        for i, view in enumerate(views):
            private(view, self.mid, f"PLAYER{i + 1}")
            assert view["matchSeq"] == receipt["matchSeq"]
            self.hashes[i][str(view["matchSeq"])] = digest(view)
        self.views = views
        self.commands += 1
        self.last = dict(actor=actor, command=command, receipt=receipt)
        self.pending = None
        self.save()
        with LOCK:
            if phase in METRICS:
                m = METRICS[phase]
                m["accepted"] += 1
                m["http"].append(http)
                m["visible"].append(visible)

    def duplicate(self):
        old = self.last
        assert old is not None
        assert (
            self.request(
                self.route + "/commands",
                i=old["actor"],
                data=old["command"],
                controller=True,
            )
            == old["receipt"]
        )
        self.duplicates += 1

    def save(self):
        value = {
            k: getattr(self, k)
            for k in (
                "index",
                "controllers",
                "tokens",
                "revisions",
                "mid",
                "hashes",
                "last",
                "pending",
                "commands",
                "duplicates",
                "completed",
            )
        }
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / f"game-{self.index}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value))
        temp.chmod(0o600)
        temp.replace(path)

    def restore(self):
        value = json.loads((self.state / f"game-{self.index}.json").read_text())
        for k, v in value.items():
            setattr(self, k, v)
        self.connect()

    def recover(self):
        pending = self.pending
        if pending:
            try:
                receipt = self.request(
                    self.route + "/receipts/" + pending["command"]["commandId"],
                    i=pending["actor"],
                )
            except ClientError as e:
                if e.status != 404:
                    raise
                receipt = self.request(
                    self.route + "/commands",
                    i=pending["actor"],
                    data=pending["command"],
                    controller=True,
                )
            self.commands += 1
            self.last = {**pending, "receipt": receipt}
            self.pending = None
            self.connect()
            self.save()
        elif self.last:
            self.duplicate()
        self.step()
        self.verify_replay()
        self.save()

    def verify_replay(self):
        self.current_operation = "replay_verify"
        for i in range(2):
            cursor = None
            seen = {}
            cursors = set()
            while True:
                page = self.request(
                    self.route + "/frames" + ("/" + cursor if cursor else ""), i=i
                )
                assert (
                    page["viewerSeat"] == f"PLAYER{i + 1}"
                    and page["matchId"] == self.mid
                )
                validate("ReplayPage", page)
                for f in page["frames"]:
                    seen[str(f["matchSeq"])] = sha256(
                        json.dumps(f, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                cursor = page["nextCursor"]
                if cursor is None:
                    break
                if cursor in cursors or not page["frames"]:
                    raise ValueError("REPLAY_CURSOR_STALLED")
                cursors.add(cursor)
                if len(cursors) > len(self.hashes[i]) + 1:
                    raise ValueError("REPLAY_PAGE_LIMIT")
            assert seen == self.hashes[i], "PRIVATE_REPLAY_DIFFERS_FROM_VISIBLE_HISTORY"


def phase(value):
    global PHASE
    PHASE = value
    path = Path("/measure/phase")
    temp = path.with_suffix(".new")
    temp.write_text(value)
    temp.replace(path)


def validate_run(label, matches, warmup, steady):
    if (
        matches not in (1, 10, 25, 50, 100)
        or warmup < 0
        or steady < 1
        or steady > 14400
    ):
        raise ValueError("INVALID_LOAD_PARAMETERS")
    if label in ("matrix", "soak") and (
        matches == 1 or warmup < 300 or steady < (7200 if label == "soak" else 1200)
    ):
        raise ValueError("FORMAL_DURATION_REQUIRED")


def load(args, inputs):
    validate_run(args.label, args.matches, args.warmup, args.steady)
    games = [Game(i, inputs, args.state) for i in range(args.matches)]
    for game in games:
        game.setup()
        game.save()
    started_wall = time.time()
    started = time.monotonic()
    cpu_started = time.process_time()
    warm_end = started + args.warmup
    end = warm_end + args.steady
    phase("warmup" if args.warmup else "steady")
    failures = []
    steady_started = started if not args.warmup else None

    def play(game):
        rng = random.Random(args.seed + game.index)
        try:
            STOP.wait(rng.uniform(0, 5))
            while not STOP.is_set() and time.monotonic() < end:
                game.step()
                STOP.wait(rng.uniform(args.think_min, args.think_max))
        except Exception as e:
            with LOCK:
                failures.append(failure(e, game.current_operation))
            STOP.set()

    try:
        with ThreadPoolExecutor(max_workers=len(games)) as pool:
            jobs = [pool.submit(play, g) for g in games]
            while time.monotonic() < end and not STOP.is_set():
                if PHASE == "warmup" and time.monotonic() >= warm_end:
                    phase("steady")
                    steady_started = time.monotonic()
                    end = steady_started + args.steady
                time.sleep(0.1)
            STOP.set()
            for job in jobs:
                job.result()
        stopped = time.monotonic()
        phase("cleanup")
        if not failures:
            for game in games:
                try:
                    game.verify_replay()
                except Exception as e:
                    failures.append(failure(e, game.current_operation))
                    break
        result = dict(
            schema=1,
            workload="real-wire-think-v1",
            label=args.label,
            formalSample=args.label in ("matrix", "soak"),
            capacityValidated=False,
            matches=args.matches,
            players=2 * args.matches,
            warmupSeconds=args.warmup,
            steadySeconds=args.steady,
            startedUnix=started_wall,
            elapsedSeconds=stopped - started,
            measuredSteadySeconds=0
            if steady_started is None
            else stopped - steady_started,
            driverCpuSeconds=time.process_time() - cpu_started,
            driverMaxRssKiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            jitterSeed=args.seed,
            thinkSeconds=[args.think_min, args.think_max],
            status="passed" if not failures else "failed",
            failures=failures,
            privateReplayVerified=not failures,
            completedMatches=sum(g.completed for g in games),
            perGameCommands=[g.commands for g in games],
            reconnectSeconds=latency(RECONNECT),
            replayReadConcurrency=2,
            replayQueueWaitSeconds=latency(REPLAY_WAITS),
            requestOperations=REQUESTS,
            phases={
                p: dict(
                    accepted=m["accepted"],
                    firstAttemptErrors=m["errors"],
                    httpRoundTripSeconds=latency(m["http"]),
                    bothPlayersVisibleSeconds=latency(m["visible"]),
                )
                for p, m in METRICS.items()
            },
        )
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in ("status", "label", "matches", "capacityValidated")
                }
            )
        )
        if failures:
            raise SystemExit(1)
    finally:
        for game in games:
            game.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=["load", "checkpoint", "recover", "unavailable"]
    )
    parser.add_argument("--inputs", default="/inputs/decks.json")
    parser.add_argument("--state", default="/state")
    parser.add_argument("--output", default="/results/driver.json")
    parser.add_argument("--label", choices=["pilot", "matrix", "soak"], default="pilot")
    parser.add_argument("--matches", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--steady", type=int, default=60)
    parser.add_argument("--seed", type=int, default=711)
    parser.add_argument("--think-min", type=float, default=5)
    parser.add_argument("--think-max", type=float, default=15)
    args = parser.parse_args()
    try:
        assert 0 < args.think_min <= args.think_max <= 60
        inputs = json.loads(Path(args.inputs).read_text())
        if args.mode == "load":
            return load(args, inputs)
        phase("fault")
        game = Game(0, inputs, args.state)
        try:
            if args.mode == "checkpoint":
                game.setup()
                game.step()
                game.step()
                game.duplicate()
                game.verify_replay()
                game.save()
            elif args.mode == "recover":
                game.restore()
                game.recover()
            else:
                # Existing private journal supplies the decision; no DB read is needed first.
                value = json.loads((game.state / "game-0.json").read_text())
                for k, v in value.items():
                    setattr(game, k, v)
                # Host prepares this request before pausing the dedicated database.
                pending = json.loads((game.state / "next-command.json").read_text())
                game.pending = pending
                game.save()
                try:
                    game.request(
                        game.route + "/commands",
                        i=pending["actor"],
                        data=pending["command"],
                        controller=True,
                    )
                except ClientError as e:
                    assert e.retryable
                else:
                    raise ValueError("DB_FAILURE_ACCEPTED_WRITE")
            if args.mode != "unavailable":
                views = game.views
                actor = next(i for i, v in enumerate(views) if v["decision"])
                (game.state / "next-command.json").write_text(
                    json.dumps(dict(actor=actor, command=choose(views[actor])))
                )
                (game.state / "next-command.json").chmod(0o600)
            result = dict(
                status="passed",
                fault=args.mode,
                acceptedCommands=game.commands,
                duplicates=game.duplicates,
                privateReplayVerified=args.mode != "unavailable",
                capacityValidated=False,
            )
            Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result))
        finally:
            game.close()
    except (Exception,) as error:
        print(
            json.dumps(
                dict(
                    status="failed",
                    error=type(error).__name__,
                    code=getattr(error, "code", "BENCHMARK_FAILED"),
                )
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
