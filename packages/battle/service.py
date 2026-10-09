"""SQLite serializable commits + bounded engine cache; player-one projections only.

BEGIN IMMEDIATE fences other processes; the persisted revision fences stale caches.
No mutated engine survives a failed commit. HTTP ACK is sent after commit. A saved
projection at every sequence acts as a durable replay/outbox for polling clients.
"""

from packages.simulation.results import decorate
from packages.simulation.events import public_effects
from packages.simulation.registry import VERSION as RELEASE_VERSION, RELEASE, compatible_release

from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
import secrets
import sqlite3
import threading
import time

from packages.collection.domain import (
    CARDS,
    RAW,
    RULES,
    CATALOG_VERSION,
    AS_OF,
    content_hash,
    validation,
    require,
    DomainError,
)
from packages.collection.store import now
from packages.battle import agent
from packages.battle.decision import decide
from packages.simulation.a2 import VERSION as A2_VERSION

COOKIE = "ptcg_practice_session"
MAX_STEPS = 2500


def runtime():
    try:
        import importlib

        return importlib.import_module("packages.battle.runtime")
    except (ImportError, OSError, RuntimeError) as e:
        raise DomainError(
            "ENGINE_UNAVAILABLE", "对战内核未准备好，请运行 P2 安装脚本", 503
        ) from e


class MatchService:
    def __init__(self, store):
        self.store = store
        self.path = store.path
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS battle_sessions(owner TEXT PRIMARY KEY,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS matches(id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_id TEXT NOT NULL,request_hash TEXT NOT NULL,revision_id TEXT NOT NULL,opponent TEXT NOT NULL,status TEXT NOT NULL,seq INTEGER NOT NULL,engine_version TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(owner,request_id));
            CREATE TABLE IF NOT EXISTS match_frames(match_id TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL,event TEXT NOT NULL,PRIMARY KEY(match_id,seq));
            CREATE TABLE IF NOT EXISTS match_commands(match_id TEXT NOT NULL,command_id TEXT NOT NULL,request_hash TEXT NOT NULL,receipt TEXT NOT NULL,PRIMARY KEY(match_id,command_id));
            CREATE TABLE IF NOT EXISTS match_ai_views(match_id TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(match_id,seq));
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def session(self, token=None):
        if token:
            try:
                return token, self.owner(token)
            except DomainError:
                pass
        token = secrets.token_urlsafe(32)
        owner = hashlib.sha256(token.encode()).hexdigest()
        with self.db(True) as db:
            db.execute("INSERT INTO battle_sessions VALUES(?,?)", (owner, now()))
        return token, owner

    def owner(self, token):
        require(
            isinstance(token, str) and 20 <= len(token) <= 100,
            "AUTH_REQUIRED",
            "请重新进入对战大厅",
            401,
        )
        owner = hashlib.sha256(token.encode()).hexdigest()
        with self.db() as db:
            require(
                db.execute(
                    "SELECT 1 FROM battle_sessions WHERE owner=?", (owner,)
                ).fetchone(),
                "AUTH_REQUIRED",
                "会话已失效，请重新进入对战大厅",
                401,
            )
        return owner

    def _row(self, db, owner, id):
        row = db.execute(
            "SELECT * FROM matches WHERE id=? AND owner=?", (id, owner)
        ).fetchone()
        require(row is not None, "MATCH_NOT_FOUND", "对局不存在或不属于当前会话", 404)
        return row

    def list(self, owner):
        with self.db() as db:
            return [
                {**dict(r), 'status': r['status'] if r['engine_version'] == runtime().ENGINE_VERSION else 'archived'}
                for r in db.execute(
                    "SELECT id,revision_id AS revisionId,opponent,status,seq,engine_version,created_at AS createdAt,updated_at AS updatedAt FROM matches WHERE owner=? ORDER BY updated_at DESC LIMIT 100",
                    (owner,),
                )
            ]

    def revisions(self):
        result = []
        for deck in self.store.decks():
            for r in self.store.revisions(deck["id"]):
                try:
                    with self.db() as db:
                        self._admit(db, r["id"])
                    valid, reason = True, ""
                except DomainError as exc:
                    valid, reason = False, str(exc)
                result.append(
                    {
                        "id": r["id"],
                        "name": deck["name"],
                        "number": r["number"],
                        "playable": valid,
                        "reason": reason,
                    }
                )
        return result

    def _admit(self, db, rid):
        r = db.execute("SELECT * FROM revisions WHERE id=?", (rid,)).fetchone()
        require(r is not None, "REVISION_REQUIRED", "请先在构筑页保存卡组版本")
        require(not db.execute("SELECT 1 FROM deleted_revisions WHERE id=?", (rid,)).fetchone(),
                "REVISION_DELETED", "此卡组版本已删除，请选择其他版本", 409)
        body = json.loads(r["body"])
        require(
            content_hash(body) == r["hash"], "REVISION_CORRUPT", "卡组版本校验失败", 409
        )
        checked = validation(body["entries"])
        require(
            checked["playable"],
            "DECK_UNSUPPORTED",
            "；".join(i["explanation"] for i in checked["issues"] if i["severity"] == "error")
            or "卡组包含未发布效果或不符合赛制，请查看构筑校验明细",
        )
        # Revalidate immutable card content against this runtime. A new match
        # freezes current mappings in deck lines and current versions in provenance.
        return body

    @staticmethod
    def _lines(entries):
        return [
            f"{e['quantity']} {CARDS[e['printingId']]['engineLine']}" for e in entries
        ]

    def create(self, owner, request):
        request = dict(request)
        if request.get("aiLevel") == "A1":
            request.pop("aiLevel")  # Preserve pre-A2 creation idempotency hashes.
        rt = runtime()
        with self.lock, self.db(True) as db:
            h = content_hash(request)
            old = db.execute(
                "SELECT * FROM matches WHERE owner=? AND request_id=?",
                (owner, request["requestId"]),
            ).fetchone()
            if old:
                require(
                    old["request_hash"] == h,
                    "IDEMPOTENCY_CONFLICT",
                    "请求编号已用于其他对局",
                    409,
                )
                return self._snapshot(db, old)
            require(
                db.execute(
                    "SELECT COUNT(*) FROM matches WHERE owner=? AND status='playing'",
                    (owner,),
                ).fetchone()[0]
                < 10,
                "TOO_MANY_MATCHES",
                "最多保留10场未结束对局，请先完成或认输",
                409,
            )
            revision = self._admit(db, request["revisionId"])
            require(
                RELEASE["engineVersion"] == rt.ENGINE_VERSION,
                "EFFECT_ENGINE_MISMATCH",
                "效果发布与引擎版本不一致",
                409,
            )
            opponent_id = request.get("opponentRevisionId")
            if opponent_id:
                opponent = self._admit(db, opponent_id)
                opponent_key = "revision:" + opponent_id
            else:
                template = next(
                    (t for t in RAW["templates"] if t["id"] == request.get("opponent")),
                    None,
                )
                require(
                    template is not None
                    and validation(template["entries"])["playable"],
                    "BAD_OPPONENT",
                    "请选择合法的 AI 卡组版本或预组",
                )
                opponent = {"entries": template["entries"]}
                opponent_key = template["id"]
            ai_level = request.get("aiLevel", "A1")
            require(ai_level in ("A1", "A2"), "BAD_AI_LEVEL", "请选择 A1 或实验性 A2")
            provenance = {
                "syncReleaseId": __import__("os").getenv("PTCG_RELEASE_ID"),
                "catalogVersion": CATALOG_VERSION,
                "formatAsOf": AS_OF,
                "revisionId": request["revisionId"],
                "formatHash": content_hash(RULES),
                "aiVersion": A2_VERSION if ai_level == "A2" else agent.VERSION,
                "effectReleaseVersion": RELEASE_VERSION,
                "opponentRevisionId": opponent_id,
                "opponentContentHash": content_hash(opponent),
            }
            game = rt.Adapter(
                seed=secrets.randbits(63),
                deck1=self._lines(revision["entries"]),
                deck2=self._lines(opponent["entries"]),
                provenance=provenance,
            )
            id = secrets.token_hex(16)
            stamp = now()
            db.execute(
                "INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    id,
                    owner,
                    request["requestId"],
                    h,
                    request["revisionId"],
                    opponent_key,
                    "playing",
                    0,
                    rt.ENGINE_VERSION,
                    json.dumps(game.export_private_replay()),
                    stamp,
                    stamp,
                ),
            )
            self._frame(
                db,
                id,
                game,
                {"actor": "system", "text": "对局已创建，抽签决定先后攻选择权"},
                "playing",
            )
            return self._snapshot(db, self._row(db, owner, id))

    def _game(self, row):
        rt = runtime()
        require(
            row["engine_version"] == rt.ENGINE_VERSION,
            "ENGINE_VERSION_MISMATCH",
            "内核版本已变化，本局仅可查看已保存回放",
            409,
        )
        record = json.loads(row["body"])
        require(
            record["config"].get("provenance", {}).get("aiVersion")
            in (agent.VERSION, A2_VERSION),
            "AI_VERSION_MISMATCH",
            "AI 版本已变化，本局仅可查看已保存回放",
            409,
        )
        require(
            compatible_release(
                record["config"].get("provenance", {}).get("effectReleaseVersion")
            ),
            "EFFECT_RELEASE_MISMATCH",
            "效果发布版本已变化，本局仅可查看已保存回放",
            409,
        )
        cached = self.cache.get(row["id"])
        if cached and cached.version == row["seq"]:
            self.cache.move_to_end(row["id"])
            return cached
        try:
            game = rt.Adapter.replay(record)
        except Exception as e:
            raise DomainError(
                "RECOVERY_FAILED", "对局恢复校验失败，已保留日志，请查看回放", 409
            ) from e
        self.cache[row["id"]] = game
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        return game

    def _frame(self, db, id, game, event, status):
        rt = runtime()
        v = rt.view(game, rt.PlayerId.PLAYER1)
        ai_version = game.config.get("provenance", {}).get("aiVersion", agent.VERSION)
        v.update(matchId=id, status=status, aiVersion=ai_version)
        if ai_version == A2_VERSION:
            from packages.simulation.information import observation

            db.execute(
                "INSERT INTO match_ai_views VALUES(?,?,?)",
                (
                    id,
                    game.version,
                    json.dumps(observation(rt.view(game, rt.PlayerId.PLAYER2))),
                ),
            )
        if status == "truncated":
            v.update(done=True, decision=None, winner=None)
        previous = db.execute(
            "SELECT body FROM match_frames WHERE match_id=? ORDER BY seq DESC LIMIT 1",
            (id,),
        ).fetchone()
        changes = []
        if previous:
            old = json.loads(previous[0])["observation"]
            current = v["observation"]
            for role, label in (("self", "你"), ("opponent", "AI")):
                for field, name in (
                    ("hand_count", "手牌"),
                    ("deck_count", "牌库"),
                    ("prize_count", "奖赏"),
                ):
                    if old[role][field] != current[role][field]:
                        changes.append(
                            f"{label}{name}：{old[role][field]} → {current[role][field]}"
                        )
                for zone, name in (("active", "战斗区"), ("bench", "备战区")):
                    prior = old[role][zone]
                    for i, card in enumerate(current[role][zone]):
                        if (
                            i < len(prior)
                            and prior[i]["name"] == card["name"]
                            and prior[i].get("hp") != card.get("hp")
                        ):
                            changes.append(
                                f"{label}{name}{i + 1} HP：{prior[i].get('hp')} → {card.get('hp')}"
                            )
        event = {
            **event,
            "coins": [
                "heads" if message == "Coin flip: HEADS." else "tails"
                for message in game.info.get("auto_executed", [])
                if message in ("Coin flip: HEADS.", "Coin flip: TAILS.")
            ],
            "effects": public_effects(
                json.loads(previous[0])["observation"] if previous else None,
                v["observation"],
            ),
            "changes": changes,
            "seq": game.version,
            "turn": v["observation"]["turn_number"],
        }
        db.execute(
            "INSERT INTO match_frames VALUES(?,?,?,?)",
            (id, game.version, json.dumps(v), json.dumps(event)),
        )
        return v

    def _snapshot(self, db, row):
        f = db.execute(
            "SELECT body FROM match_frames WHERE match_id=? AND seq=?",
            (row["id"], row["seq"]),
        ).fetchone()
        view = json.loads(f[0])
        view["events"] = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT event FROM match_frames WHERE match_id=? ORDER BY seq DESC LIMIT 60",
                (row["id"],),
            )
        ][::-1]
        if row['engine_version'] != runtime().ENGINE_VERSION:
            view.update(readOnly=True, readOnlyReason='旧引擎版本已停用，此对局仅供回放。请重新保存卡组后开始新对局。', done=True, decision=None, status='archived')
        return decorate(view)

    def snapshot(self, owner, id):
        with self.db() as db:
            return self._snapshot(db, self._row(db, owner, id))

    @staticmethod
    def _event(view, command, ai=False):
        d = view["decision"]
        if d["kind"] == "selection":
            # AI candidate identities, including hidden/private searches, never enter journal.
            text = f"完成选牌（{len(command['choice'].get('selectedRefs', []))} 张）"
        else:
            o = next(
                o for o in d["options"] if o["id"] == command["choice"]["optionId"]
            )
            # Setup bench identities stay hidden. Ordinary sources are public once played.
            labels = {
                "PlayPokemonAction": "放置宝可梦",
                "EvolvePokemonAction": "进化",
                "AttachEnergyAction": "附加能量",
                "UseAbilityAction": "使用特性",
                "AttackAction": "攻击",
                "UseSupporterAction": "使用支援者",
                "UseItemAction": "使用物品",
                "UseToolAction": "附加道具",
                "PutStadiumAction": "放置竞技场",
                "UseStadiumAction": "使用竞技场",
                "RetreatAction": "撤退",
                "PassTurn": "结束回合",
                "SetupOption": "确认开局选择",
            }
            text = labels.get(o["actionType"], "执行动作")
            if view["phase"] == "playing" and o.get("source"):
                card = next(
                    (
                        c
                        for c in CARDS.values()
                        if c.get("englishName") == o["source"]
                        and c["effectStatus"] == "verified"
                    ),
                    None,
                )
                text += " · " + (card["cnName"] if card else o["source"])
        structured = {}
        if d["kind"] == "options":
            chosen = next(
                (
                    o
                    for o in d["options"]
                    if o["id"] == command["choice"].get("optionId")
                ),
                {},
            )
            structured["actionType"] = chosen.get("actionType")
            for role in ("source", "target", "active_pokemon"):
                ref = chosen.get(role + "Ref")
                if ref:
                    location = ref.split(":")[1:]
                    if ai and location and location[0] in ("self", "opponent"):
                        location[0] = "opponent" if location[0] == "self" else "self"
                    # Hand positions are private and unnecessary for public animation.
                    if "hand" not in location:
                        structured[role + "Location"] = ":".join(location)
        return {"actor": "ai" if ai else "you", "text": text, **structured}

    def _save(self, db, row, game, event):
        status = (
            "finished"
            if game.done
            else "truncated"
            if game.version >= MAX_STEPS
            else "playing"
        )
        self._frame(db, row["id"], game, event, status)
        db.execute(
            "UPDATE matches SET body=?,seq=?,status=?,updated_at=? WHERE id=?",
            (
                json.dumps(game.export_private_replay()),
                game.version,
                status,
                now(),
                row["id"],
            ),
        )

    def command(self, owner, id, command):
        rt = runtime()
        with self.lock:
            try:
                with self.db(True) as db:
                    row = self._row(db, owner, id)
                    old = db.execute(
                        "SELECT * FROM match_commands WHERE match_id=? AND command_id=?",
                        (id, command["commandId"]),
                    ).fetchone()
                    h = content_hash(command)
                    if old:
                        require(
                            old["request_hash"] == h,
                            "IDEMPOTENCY_CONFLICT",
                            "该命令编号已提交其他内容",
                            409,
                        )
                        return {
                            "receipt": json.loads(old["receipt"]),
                            "view": self._snapshot(db, row),
                        }
                    require(
                        row["status"] == "playing", "MATCH_ENDED", "对局已结束", 409
                    )
                    game = self._game(row)
                    before = rt.view(game, rt.PlayerId.PLAYER1)
                    try:
                        receipt = game.submit(rt.PlayerId.PLAYER1, command)
                    except rt.RejectedCommand as e:
                        raise DomainError(
                            str(e), "当前选择已失效或不合法，请刷新局面后重试", 409
                        ) from e
                    self._save(db, row, game, self._event(before, command))
                    db.execute(
                        "INSERT INTO match_commands VALUES(?,?,?,?)",
                        (id, command["commandId"], h, json.dumps(receipt)),
                    )
                    result = {
                        "receipt": receipt,
                        "view": self._snapshot(db, self._row(db, owner, id)),
                    }
                return result
            except Exception:
                self.cache.pop(id, None)
                raise

    def advance(self, owner, id, steps=8):
        rt = runtime()
        with self.lock, self.db() as db:
            row = self._row(db, owner, id)
            version = (
                json.loads(row["body"])["config"].get("provenance", {}).get("aiVersion")
            )
        if version == A2_VERSION:
            return self._advance_a2(owner, id)
        # Bounded batches, each action independently durable. A lost response is
        # resumed from latest seq; parallel requests cannot apply a choice twice.
        began = time.monotonic()
        with self.lock:
            try:
                for _ in range(steps):
                    with self.db(True) as db:
                        row = self._row(db, owner, id)
                        if row["status"] != "playing":
                            break
                        game = self._game(row)
                        if game.actor != rt.PlayerId.PLAYER2:
                            break
                        before = rt.view(game, rt.PlayerId.PLAYER2)
                        start = time.perf_counter()
                        command, fallback = decide(before)
                        game.submit(rt.PlayerId.PLAYER2, command)
                        event = self._event(before, command, True)
                        event.update(
                            fallback=fallback,
                            aiMilliseconds=round(
                                (time.perf_counter() - start) * 1000, 3
                            ),
                        )
                        # Keep explanations generic: no unseen hand/candidate clues.
                        event["explanation"] = "A1 根据其可见手牌与场面选择合法动作"
                        self._save(db, row, game, event)
                    if time.monotonic() - began > 0.75:
                        break
            except Exception:
                self.cache.pop(id, None)
                raise
        return self.snapshot(owner, id)

    def _advance_a2(self, owner, id):
        """Compute outside both the SQLite transaction and the authority lock.

        A concurrent command/resign/advance invalidates the proposal's sequence;
        applying it requires re-reading and comparing the committed version.
        """
        from packages.simulation.a2 import decide as search_decide, legal
        from packages.simulation.information import InformationSet

        rt = runtime()
        with self.lock, self.db() as db:
            row = self._row(db, owner, id)
            if row["status"] != "playing":
                return self._snapshot(db, row)
            game = self._game(row)
            if game.actor != rt.PlayerId.PLAYER2:
                return self._snapshot(db, row)
            sequence = game.version
            history = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT body FROM match_ai_views WHERE match_id=? ORDER BY seq",
                    (id,),
                )
            ]
            info = InformationSet(
                "PLAYER2",
                history,
                {
                    str(r["command"]["expectedStateVersion"]): r["command"]["choice"]
                    for r in game.commands
                    if r["viewer"] == "PLAYER2"
                },
                list(game.config["deck2"]),
                [self._lines(t["entries"]) for t in RAW["templates"]],
            )
        result = search_decide(info)
        with self.lock, self.db(True) as db:
            row = self._row(db, owner, id)
            if row["status"] != "playing" or row["seq"] != sequence:
                return self._snapshot(db, row)
            game = self._game(row)
            before = rt.view(game, rt.PlayerId.PLAYER2)
            require(
                legal(before, result["command"]),
                "INVALID_AI_PROPOSAL",
                "AI 提案已失效",
                409,
            )
            try:
                game.submit(rt.PlayerId.PLAYER2, result["command"])
                event = self._event(before, result["command"], True)
                event.update(
                    fallback=result["status"] != "searched",
                    aiMilliseconds=round(result["seconds"] * 1000, 3),
                    explanation="A2 根据其可见信息搜索；预算不足时使用合法启发式提案",
                    searchStatus=result["status"],
                    searchStopReason=result.get("stopReason"),
                    simulations=result["simulations"],
                )
                self._save(db, row, game, event)
                return self._snapshot(db, self._row(db, owner, id))
            except Exception:
                self.cache.pop(id, None)
                raise

    def resign(self, owner, id, expected):
        with self.lock, self.db(True) as db:
            row = self._row(db, owner, id)
            if row["status"] == "resigned":
                return self._snapshot(db, row)
            require(
                row["status"] == "playing" and expected == row["seq"],
                "STALE_DECISION",
                "局面已更新，请重试",
                409,
            )
            v = self._snapshot(db, row)
            v.pop("events", None)
            seq = row["seq"] + 1
            v.update(
                stateVersion=seq,
                status="resigned",
                done=True,
                decision=None,
                winner="PLAYER2",
            )
            event = {
                "seq": seq,
                "actor": "you",
                "text": "认输，对局结束",
                "turn": v["observation"]["turn_number"],
            }
            db.execute(
                "INSERT INTO match_frames VALUES(?,?,?,?)",
                (id, seq, json.dumps(v), json.dumps(event)),
            )
            db.execute(
                "UPDATE matches SET seq=?,status='resigned',updated_at=? WHERE id=?",
                (seq, now(), id),
            )
            self.cache.pop(id, None)
            return self._snapshot(db, self._row(db, owner, id))

    def replay_timeline(self, owner, id):
        """Index saved public projections without restoring a private engine."""
        with self.db() as db:
            row = self._row(db, owner, id)
            checkpoints = []
            previous = None
            for frame in db.execute(
                "SELECT seq,body FROM match_frames WHERE match_id=? AND seq<=? ORDER BY seq",
                (id, row["seq"]),
            ):
                view = json.loads(frame["body"])
                observation = view["observation"]
                # Setup has several prompts but is one navigation destination.
                playing = view["phase"] == "playing"
                turn = observation["turn_number"] if playing else 0
                player = observation["turn"] if playing else None
                key = (playing, turn, player)
                if key != previous:
                    checkpoints.append(
                        {
                            "seq": frame["seq"],
                            "turn": turn,
                            "player": player,
                            "phase": "playing" if playing else "setup",
                        }
                    )
                    previous = key
            return {"matchId": id, "lastSeq": row["seq"], "checkpoints": checkpoints}

    def replay(self, owner, id, after=-1, limit=100):
        with self.db() as db:
            row = self._row(db, owner, id)
            frames = [
                {
                    "seq": r["seq"],
                    "view": decorate(json.loads(r["body"])),
                    "event": json.loads(r["event"]),
                }
                for r in db.execute(
                    "SELECT * FROM match_frames WHERE match_id=? AND seq>? ORDER BY seq LIMIT ?",
                    (id, after, limit),
                )
            ]
            return {
                "matchId": id,
                "perspective": "PLAYER1",
                "lastSeq": row["seq"],
                "frames": frames,
                "nextAfter": frames[-1]["seq"] if frames else after,
            }
