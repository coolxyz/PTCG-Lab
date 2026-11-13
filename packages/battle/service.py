"""SQLite serializable commits + bounded engine cache; player-one projections only.

BEGIN IMMEDIATE fences other processes; the persisted revision fences stale caches.
No mutated engine survives a failed commit. HTTP ACK is sent after commit. A saved
projection at every sequence acts as a durable replay/outbox for polling clients.
"""

from packages.simulation.results import decorate
from packages.simulation.events import public_effects
from packages.simulation.registry import VERSION as RELEASE_VERSION, RELEASE

from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
import os
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
from packages.battle import agent, allocations, effect_plans, h2, llm
from packages.battle.presets import PRESETS
from packages.battle.decision import decide
from packages.battle.presentation import TOOL_SOURCE_LABEL, tool_attack_event
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
        self.llm_in_flight = set()
        self.llm_client = None
        self.release_id = lambda: os.environ.get("PTCG_RELEASE_ID")
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS battle_sessions(owner TEXT PRIMARY KEY,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS matches(id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_id TEXT NOT NULL,request_hash TEXT NOT NULL,revision_id TEXT NOT NULL,opponent TEXT NOT NULL,status TEXT NOT NULL,seq INTEGER NOT NULL,engine_version TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(owner,request_id));
            CREATE TABLE IF NOT EXISTS match_frames(match_id TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL,event TEXT NOT NULL,PRIMARY KEY(match_id,seq));
            CREATE TABLE IF NOT EXISTS match_commands(match_id TEXT NOT NULL,command_id TEXT NOT NULL,request_hash TEXT NOT NULL,receipt TEXT NOT NULL,PRIMARY KEY(match_id,command_id));
            CREATE TABLE IF NOT EXISTS match_ai_views(match_id TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(match_id,seq));
            CREATE TABLE IF NOT EXISTS match_llm_steps(match_id TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(match_id,seq));
            """)

    def close(self):
        if self.llm_client is not None:
            self.llm_client.close()
            self.llm_client = None

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
            result = []
            for row in db.execute(
                "SELECT id,revision_id AS revisionId,opponent,status,seq,engine_version,body,created_at AS createdAt,updated_at AS updatedAt FROM matches WHERE owner=? ORDER BY updated_at DESC LIMIT 100",
                (owner,),
            ):
                item = dict(row)
                item.pop("body")
                if not self._current_rules(row):
                    item["status"] = "archived"
                result.append(item)
            return result

    def _current_rules(self, row):
        provenance = json.loads(row["body"]).get("config", {}).get("provenance", {})
        return (
            row["engine_version"] == runtime().ENGINE_VERSION
            and provenance.get("effectReleaseVersion") == RELEASE_VERSION
            and provenance.get("syncReleaseId") == self.release_id()
            and not (
                row["status"] == "playing"
                and provenance.get("llmStrategy")
                and provenance.get("aiVersion") != llm.VERSION
            )
        )

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

    def llm_opponent(self):
        config = llm.settings(self.path)
        return config.public() if config else None

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
        ai_level = request.setdefault("aiLevel", "H2")
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
                    (t for t in PRESETS + RAW["templates"] if t["id"] == request.get("opponent")),
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
            versions = {"A1": agent.VERSION, "H2": h2.VERSION, "A2": A2_VERSION, "LLM": llm.VERSION}
            require(ai_level in versions, "BAD_AI_LEVEL", "请选择可用的 AI 策略")
            provenance = {
                "syncReleaseId": __import__("os").getenv("PTCG_RELEASE_ID"),
                "catalogVersion": CATALOG_VERSION,
                "formatAsOf": AS_OF,
                "revisionId": request["revisionId"],
                "formatHash": content_hash(RULES),
                "aiVersion": versions[ai_level],
                "effectReleaseVersion": RELEASE_VERSION,
                "opponentRevisionId": opponent_id,
                "opponentContentHash": content_hash(opponent),
            }
            if ai_level == "H2":
                provenance["aiSourceHash"] = h2.fingerprint()
            elif ai_level == "LLM":
                config = llm.settings(self.path)
                require(config is not None, "LLM_NOT_CONFIGURED",
                        "请在本机配置 LLM 服务后开始对局", 409)
                provenance.update(
                    llmStrategy=config.public(),
                    llmConfigHash=config.fingerprint(),
                    llmConfig=config.model_dump(exclude={"apiKey", "baseUrl"}),
                )
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
            in (agent.VERSION, h2.VERSION, A2_VERSION, llm.VERSION),
            "AI_VERSION_MISMATCH",
            "AI 版本已变化，本局仅可查看已保存回放",
            409,
        )
        require(
            record["config"].get("provenance", {}).get("effectReleaseVersion") == RELEASE_VERSION,
            "EFFECT_RELEASE_MISMATCH",
            "效果发布版本已变化，本局仅可查看已保存回放",
            409,
        )
        require(
            record["config"].get("provenance", {}).get("syncReleaseId") == self.release_id(),
            "RULE_RELEASE_UNAVAILABLE",
            "规则发布已更新，本局仅可查看已保存回放，请开始新对局",
            409,
        )
        if record["config"].get("provenance", {}).get("aiVersion") == h2.VERSION:
            require(record["config"]["provenance"].get("aiSourceHash") == h2.fingerprint(),
                    "H2_SOURCE_MISMATCH", "H2 策略或资料版本已变化，本局仅可查看已保存回放", 409)
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
        if ai_version == llm.VERSION:
            v["aiStrategy"] = game.config["provenance"]["llmStrategy"]
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
        groups = list(event.get("cardGroups", []))
        previous_reveals = json.loads(previous[0]).get("publicReveals", []) if previous else []
        reveals = v.get("publicReveals", [])
        if reveals[:len(previous_reveals)] == previous_reveals:
            for reveal in reveals[len(previous_reveals):]:
                groups.append({
                    "label": ("你" if reveal["actor"] == "PLAYER1" else "AI") + " · 按效果展示卡牌",
                    "cards": reveal["cards"],
                })
        event = {
            **event,
            "cardGroups": groups,
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
            "turn": json.loads(previous[0])["observation"]["turn_number"] if previous else v["observation"]["turn_number"],
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
            tool_attack_event(json.loads(r["event"]), json.loads(r["previous_body"])["observation"] if r["previous_body"] else None)
            for r in db.execute(
                "SELECT f.event,p.body AS previous_body FROM match_frames f "
                "LEFT JOIN match_frames p ON p.match_id=f.match_id AND p.seq=f.seq-1 "
                "WHERE f.match_id=? ORDER BY f.seq DESC LIMIT 60",
                (row["id"],),
            )
        ][::-1]
        if not self._current_rules(row):
            view.update(readOnly=True, readOnlyReason='对战规则或 AI 版本已更新，此对局仅供回放。请开始新对局。', done=True, decision=None, status='archived')
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
            if o["actionType"] == "SetupOption" and o.get("kind") == "choose_order":
                text = "选择先攻" if o["value"] == "first" else "选择后攻"
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
            if view["phase"] == "playing" and chosen.get("actionType") in labels:
                public = [c for side in ("self", "opponent") for zone in ("active", "bench") for c in view["observation"][side][zone]] + view["observation"]["stadium"]
                plays = {"PlayPokemonAction", "EvolvePokemonAction", "AttachEnergyAction", "UseSupporterAction", "UseItemAction", "UseToolAction", "PutStadiumAction"}
                sources = public + (view["observation"]["self"].get("hand") or [] if chosen["actionType"] in plays else [])
                shown_cards = []
                for role in ("source", "active_pokemon", "target"):
                    ref = chosen.get(role + "Ref")
                    card = next((c for c in (public if role == "target" else sources) if ref and c.get("ref") == ref), None)
                    if card and not any(c.get("id") == card.get("id") and c["name"] == card["name"] for c in shown_cards):
                        shown_cards.append({k: card[k] for k in ("id", "name", "attacks", "abilities") if k in card})
                if shown_cards:
                    structured["cardGroups"] = [{"label": "使用 / 涉及的卡牌", "cards": shown_cards}]
                if chosen.get("actionType") == "AttackAction" and chosen.get("effectSource"):
                    structured.setdefault("cardGroups", []).append({
                        "label": TOOL_SOURCE_LABEL, "cards": [chosen["effectSource"]],
                    })
                if (chosen.get("attack") or {}).get("name"):
                    text += " · " + chosen["attack"]["name"]
                if chosen.get("ability"):
                    text += " · " + chosen["ability"]
            for role in ("source", "target", "active_pokemon"):
                ref = chosen.get(role + "Ref")
                if ref:
                    location = ref.split(":")[1:]
                    if ai and location and location[0] in ("self", "opponent"):
                        location[0] = "opponent" if location[0] == "self" else "self"
                    # Hand positions are private and unnecessary for public animation.
                    if "hand" not in location:
                        structured[role + "Location"] = ":".join(location)
        event = {"actor": "ai" if ai else "you", "text": text, **structured}
        # Newly generated declarations and saved visible frames use the same
        # presentation fallback. The persisted command and engine are untouched.
        before = view["observation"]
        if ai:
            before = {**before, "self": before["opponent"], "opponent": before["self"]}
        return tool_attack_event(event, before)

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
        if version == h2.VERSION:
            return self._advance_h2(owner, id)
        if version == llm.VERSION:
            return self._advance_llm(owner, id)
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

    def _advance_h2(self, owner, id):
        """Compute outside the authority lock; stale or duplicate proposals never commit."""
        import copy
        from packages.simulation.a2 import legal

        rt = runtime()
        with self.lock, self.db() as db:
            row = self._row(db, owner, id)
            if row["status"] != "playing":
                return self._snapshot(db, row)
            game = self._game(row)
            if game.actor != rt.PlayerId.PLAYER2:
                return self._snapshot(db, row)
            sequence = game.version
            before = copy.deepcopy(rt.view(game, rt.PlayerId.PLAYER2))
        command, detail = h2.decide(before)
        with self.lock, self.db(True) as db:
            row = self._row(db, owner, id)
            if row["status"] != "playing" or row["seq"] != sequence:
                return self._snapshot(db, row)
            game = self._game(row)
            current = rt.view(game, rt.PlayerId.PLAYER2)
            require(legal(current, command), "INVALID_AI_PROPOSAL", "AI 提案已失效", 409)
            try:
                game.submit(rt.PlayerId.PLAYER2, command)
                event = self._event(current, command, True)
                event.update(detail)
                event["explanation"] = "H2 根据可见信息选择动作；异常时使用基础策略降级"
                self._save(db, row, game, event)
                return self._snapshot(db, self._row(db, owner, id))
            except Exception:
                self.cache.pop(id, None)
                raise

    @staticmethod
    def _llm_history(db, id):
        history = []
        for row in db.execute(
            "SELECT f.event,s.body FROM match_frames f LEFT JOIN match_llm_steps s "
            "ON s.match_id=f.match_id AND s.seq=f.seq "
            "WHERE f.match_id=? ORDER BY f.seq DESC LIMIT ?",
            (id, llm.HISTORY_LIMIT),
        ):
            event = json.loads(row["event"])
            entry = {k: event[k] for k in ("seq", "turn", "actor", "text", "changes") if k in event}
            cards = [card["name"] for group in event.get("cardGroups", [])
                     for card in group["cards"] if card.get("name")]
            if cards:
                entry["publicCards"] = list(dict.fromkeys(cards))
            if row["body"]:
                entry["ownAction"] = json.loads(row["body"])["action"]
            history.append(entry)
        return history[::-1]

    def _save_llm_action(self, db, row, game, current, command, detail):
        game.submit(game.actor, command)
        event = self._event(current, command, True)
        status = detail["status"]
        event.update(
            aiMilliseconds=detail["milliseconds"],
            fallback=status == "fallback",
            explanation={
                "model": "LLM 根据可见局面和近期行动作出选择",
                "planned": "引擎按 LLM 的整体计划处理本步",
                "forced": "引擎直接处理唯一动作或固定数量的盲选",
                "fallback": "LLM 未能提交合法选择，本步由引擎或 H2 执行",
            }[status],
        )
        if detail.get("allocationPlan") and status == "model":
            event["explanation"] = "LLM 为当前效果提交整体分配计划"
        if detail.get("effectPlan") and status == "model":
            event["explanation"] = "LLM 为当前效果提交整体计划"
        if detail["requestCount"] > 1:
            event["explanation"] += f"（重试 {detail['requestCount'] - 1} 次）"
        if detail.get("allocationPlan") or detail.get("allocationFromSeq"):
            choice = command["choice"]["selectedRefs"][0]
            target = next(item["card"] for item in current["decision"]["candidates"]
                          if item["ref"] == choice)
            label = ("放置伤害指示物" if allocations.effect(current) == allocations.PHANTOM_DIVE
                     else "附加基本恶能量")
            event["text"] = f"{label} · {target['name']}"
            event["cardGroups"] = [{"label": "分配目标", "cards": [
                {k: target[k] for k in ("id", "name") if k in target},
            ]}]
        db.execute("INSERT INTO match_llm_steps VALUES(?,?,?)",
                   (row["id"], game.version, json.dumps(detail, ensure_ascii=False)))
        self._save(db, row, game, event)

    def _advance_llm(self, owner, id):
        from packages.simulation.a2 import legal

        rt = runtime()
        with self.lock, self.db() as db:
            row = self._row(db, owner, id)
            if row["status"] != "playing" or id in self.llm_in_flight:
                return self._snapshot(db, row)
            game = self._game(row)
            if game.actor != rt.PlayerId.PLAYER2:
                return self._snapshot(db, row)
            config = llm.settings(self.path)
            require(config is not None and config.fingerprint() == game.config["provenance"]["llmConfigHash"],
                    "LLM_CONFIG_MISMATCH", "LLM 配置已变化，请恢复原配置或开始新对局", 409)
            sequence = game.version
            before = rt.view(game, rt.PlayerId.PLAYER2)
            history = self._llm_history(db, id)
            own_lines = tuple(game.config["deck2"])
            offer = effect_plans.window(game, before, history)
            if self.llm_client is None:
                self.llm_client = llm.http_client()
            client = self.llm_client
            self.llm_in_flight.add(id)
        try:
            command, detail = llm.decide(before, history, config, own_lines, offer, client)
            with self.lock, self.db(True) as db:
                row = self._row(db, owner, id)
                if row["status"] != "playing" or row["seq"] != sequence:
                    return self._snapshot(db, row)
                game = self._game(row)
                current = rt.view(game, rt.PlayerId.PLAYER2)
                try:
                    effect_plan = detail.get("effectPlan")
                    allocation = detail.get("allocationPlan")
                    if effect_plan:
                        # The cached game may have been evicted while the model ran.
                        # Bind the answer to the current engine's physical objects.
                        current_offer = effect_plans.window(game, current, history)
                        effect_steps = (
                            current_offer.steps(effect_plan["choices"])
                            if current_offer and current_offer.effect == effect_plan["effect"]
                            else None
                        )
                        steps = (
                            effect_plans.follow(game, current, effect_steps, rt.view)
                            if effect_steps else ()
                        )
                        link = "effectPlanFromSeq"
                    elif allocation:
                        steps = allocations.follow(game, current, allocation, rt.view)
                        link = "allocationFromSeq"
                    else:
                        steps = ()
                    for view, step in steps:
                        if game.version == sequence:
                            receipt = llm.decision_detail(
                                view, step["choice"], detail, detail["milliseconds"]
                            )
                        else:
                            receipt = llm.decision_detail(view, step["choice"], {
                                "status": "planned" if detail["status"] == "model" else "forced",
                                "requestCount": 0,
                                link: sequence + 1,
                            })
                        self._save_llm_action(db, row, game, view, step, receipt)
                        if game.version >= MAX_STEPS:
                            break
                    if game.version == sequence:
                        if effect_plan:
                            command, detail = llm.fallback(current, detail, "effect-plan-mismatch")
                        require(legal(current, command), "INVALID_AI_PROPOSAL", "AI 提案已失效", 409)
                        self._save_llm_action(db, row, game, current, command, detail)
                    return self._snapshot(db, self._row(db, owner, id))
                except Exception:
                    self.cache.pop(id, None)
                    raise
        finally:
            with self.lock:
                self.llm_in_flight.discard(id)

    def llm_review(self, owner, id):
        """Export the saved AI inputs and public actions after play ends."""
        with self.db() as db:
            row = self._row(db, owner, id)
            provenance = json.loads(row["body"])["config"].get("provenance", {})
            require(provenance.get("llmStrategy") is not None,
                    "LLM_REVIEW_NOT_FOUND", "这场对局没有 LLM 复盘", 404)
            require(row["status"] != "playing" or not self._current_rules(row),
                    "LLM_REVIEW_IN_PROGRESS", "对局结束后可导出 LLM 复盘", 409)
            frames = db.execute(
                "SELECT f.seq,f.event,s.body FROM match_frames f LEFT JOIN match_llm_steps s "
                "ON s.match_id=f.match_id AND s.seq=f.seq WHERE f.match_id=? ORDER BY f.seq",
                (id,),
            ).fetchall()
            view = self._snapshot(db, row)
            decisions = [
                {"seq": frame["seq"], **json.loads(frame["body"])}
                for frame in frames if frame["body"] is not None
            ]
            return {
                "schemaVersion": 1,
                "matchId": id,
                "engineVersion": row["engine_version"],
                "strategy": provenance["llmStrategy"],
                "configuration": provenance["llmConfig"],
                "configurationHash": provenance["llmConfigHash"],
                "opponent": row["opponent"],
                "status": view["status"],
                "winner": view["winner"],
                "result": view["result"],
                "events": [json.loads(frame["event"]) for frame in frames],
                "summary": {
                    "modelActions": sum(step["status"] == "model" for step in decisions),
                    "plannedActions": sum(step["status"] == "planned" for step in decisions),
                    "forcedActions": sum(step["status"] == "forced" for step in decisions),
                    "fallbackActions": sum(step["status"] == "fallback" for step in decisions),
                    "requestCount": sum(step["requestCount"] for step in decisions),
                    "retryCount": sum(max(0, step["requestCount"] - 1) for step in decisions),
                    "recoveredActions": sum(
                        step["requestCount"] > 1 and step["status"] == "model"
                        for step in decisions
                    ),
                    "requestsWithoutUsage": sum(
                        step["requestCount"] - bool(step.get("usage")) for step in decisions
                    ),
                },
                "decisions": decisions,
            }

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
                    "event": tool_attack_event(json.loads(r["event"]), json.loads(r["previous_body"])["observation"] if r["previous_body"] else None),
                }
                for r in db.execute(
                    "SELECT f.*,p.body AS previous_body FROM match_frames f "
                    "LEFT JOIN match_frames p ON p.match_id=f.match_id AND p.seq=f.seq-1 "
                    "WHERE f.match_id=? AND f.seq>? ORDER BY f.seq LIMIT ?",
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
