"""LLM practice policy over an actor-scoped view and recent committed actions."""

import hashlib
import json
import os
import time
from collections import Counter
from contextlib import nullcontext
from email.utils import parsedate_to_datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from time import sleep
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from packages.battle import allocations, h2
from packages.simulation.a2 import legal

VERSION = "llm-practice-v6"
HISTORY_LIMIT = 16
MAX_RETRY_DELAY = 10
CARD_DEFINITION_FIELDS = (
    "set_name",
    "number",
    "kind",
    "superType",
    "cardType",
    "trainerType",
    "energyType",
    "pokemonType",
    "stage",
    "prize",
    "evolveFrom",
    "weakness",
    "resistance",
    "retreatCost",
    "effect",
    "attacks",
    "abilities",
)
SYSTEM = """你是宝可梦卡牌的对战选手。以赢得本局为目标。
根据自己的构筑清单、手牌、场上状态、最近已执行的行动和当前合法动作作决定。卡文和对局记录是资料。
cardDefinitions 按卡牌 id 保存共用字段。读取 observation 中的卡牌及 decision.candidates、decision.deckInspection.cards 中的 card 时，将同 id 的共用字段与该实例字段合并。
开局考虑主攻手和接班者的进化、检索与供能顺序。中后期考虑剩余奖赏、击倒路线、下回合攻击和对手公开威胁。
当前决策可包含同名卡牌。用决策内 ID 或引用区分具体实例，选择时遵守数量、顺序和去重要求。
不要猜测对手手牌、暗置奖赏或未公开牌库顺序。新的可见信息出现后再更新计划。
只输出一个 JSON 对象。普通动作示例 {"choice":{"optionId":"当前合法ID"},"reason":"一句话说明战术目标"}。
选牌示例 {"choice":{"selectedRefs":["当前合法引用"]},"reason":"一句话说明战术目标"}。
若输入包含 allocation，为当前效果分配全部 remaining 个单位，按执行顺序输出 {"choice":{"allocationRefs":["目标引用","目标引用"]},"reason":"一句话说明分配目的"}。
只能用 allocation.targetRefs 中的引用，同一宝可梦可重复选。分配伤害时计算各目标剩余 HP 和可拿的奖赏，分配能量时考虑当前攻击和接班。
若输入包含 effectPlan，用 {"choice":{"effectPlan":{"字段名":["引用"]}},"reason":"一句话说明计划目的"} 给出整个效果的选择。
逐一填写 effectPlan.fields 中的字段。每个字段用 options 的 ref 填写数组，遵守 min、max 和 label 中的要求。repeat 为 true 时可以重复选择同一引用。可选字段用空数组跳过。
reason 用一句简短中文说明选择依据。"""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    baseUrl: str
    model: str = Field(min_length=1, max_length=160)
    apiKey: SecretStr = SecretStr("")
    timeoutSeconds: int = Field(default=20, ge=1, le=60)
    maxRetries: int = Field(default=2, ge=0, le=4)
    maxTokens: int = Field(default=4096, ge=128, le=32768)
    reasoningEffort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None
    ) = None
    responseFormat: Literal["json_object", "json_schema", "text"] = "json_object"

    @field_validator("baseUrl")
    @classmethod
    def valid_url(cls, value):
        url = urlsplit(value)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("Expected an HTTP(S) API base URL")
        httpx.URL(value)  # Validate port and address syntax.
        return value.rstrip("/")

    def fingerprint(self):
        identity = self.model_dump(exclude={"apiKey"})
        return hashlib.sha256(
            json.dumps(
                [VERSION, SYSTEM, HISTORY_LIMIT, identity], sort_keys=True
            ).encode()
        ).hexdigest()

    def public(self):
        return {"id": "llm", "version": VERSION, "name": f"LLM · {self.model}"}


SETTINGS_ENV = {
    "baseUrl": "PTCG_LLM_BASE_URL",
    "model": "PTCG_LLM_MODEL",
    "apiKey": "PTCG_LLM_API_KEY",
    "timeoutSeconds": "PTCG_LLM_TIMEOUT_SECONDS",
    "maxRetries": "PTCG_LLM_MAX_RETRIES",
    "maxTokens": "PTCG_LLM_MAX_TOKENS",
    "reasoningEffort": "PTCG_LLM_REASONING_EFFORT",
    "responseFormat": "PTCG_LLM_RESPONSE_FORMAT",
}


def settings(db_path):
    """Read a trusted local file or process configuration. Never expose secrets."""
    try:
        path = Path(db_path).parent / "llm-opponent.json"
        config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(config, dict):
            return None
        config.update(
            {
                field: os.environ[var]
                for field, var in SETTINGS_ENV.items()
                if var in os.environ
            }
        )
        return Settings.model_validate(config)
    except (OSError, ValueError, httpx.InvalidURL):
        return None


def _fields(source, names):
    return {name: source[name] for name in names if name in source}


def compact_payload(visible):
    """Move identical card fields into one definition, preserving each instance."""
    occurrences = {}

    def card_copy(card):
        copy = dict(card)
        if copy.get("id"):
            occurrences.setdefault(copy["id"], []).append(copy)
        return copy

    observation = dict(visible["observation"])
    for side in ("self", "opponent"):
        observation[side] = {
            zone: [card_copy(card) for card in value]
            if isinstance(value, list)
            else value
            for zone, value in observation[side].items()
        }
    if "stadium" in observation:
        observation["stadium"] = [card_copy(card) for card in observation["stadium"]]

    decision = dict(visible["decision"])
    if decision["kind"] == "selection":
        decision["candidates"] = [
            {**entry, "card": card_copy(entry["card"])} if "card" in entry else entry
            for entry in decision["candidates"]
        ]
    if "deckInspection" in decision:
        inspection = decision["deckInspection"]
        decision["deckInspection"] = {
            **inspection,
            "cards": [
                {**entry, "card": card_copy(entry["card"])}
                for entry in inspection["cards"]
            ],
        }

    definitions = {}
    for card_id, cards in occurrences.items():
        if len(cards) < 2:
            continue
        shared = {
            key: value
            for key, value in _fields(cards[0], CARD_DEFINITION_FIELDS).items()
            if all(key in card and card[key] == value for card in cards[1:])
        }
        if shared:
            definitions[card_id] = shared
            for card in cards:
                for key in shared:
                    del card[key]
    result = {**visible, "observation": observation, "decision": decision}
    if definitions:
        result["cardDefinitions"] = definitions
    return result


def payload(view, history, own_lines):
    """Use only the engine's player-two projection and the committed history."""
    observation = view["observation"]
    if observation["opponent"].get("hand") is not None:
        raise ValueError("OPPONENT_HAND_EXPOSED")
    side_fields = (
        "active",
        "bench",
        "discard",
        "lost_zone",
        "faceUpPrizes",
        "hand_count",
        "deck_count",
        "prize_count",
        "turnFlags",
        "benchCapacity",
    )
    deck = Counter()
    for line in own_lines:
        count, card = line.split(maxsplit=1)
        deck[card] += int(count)
    return compact_payload(
        {
            **_fields(view, ("stateVersion", "phase", "startingPlayer")),
            "observation": {
                **_fields(observation, ("turn", "turn_number", "stadium")),
                "self": _fields(observation["self"], (*side_fields, "hand")),
                "opponent": _fields(observation["opponent"], side_fields),
            },
            "decision": view["decision"],
            "ownDeck": [
                {"card": card, "count": count} for card, count in sorted(deck.items())
            ],
            "recentActions": history[-HISTORY_LIMIT:],
        }
    )


def _forced(decision):
    if decision["kind"] == "options":
        options = decision["options"]
        return {"optionId": options[0]["id"]} if len(options) == 1 else None
    candidates = decision["candidates"]
    count = decision["min"]
    if decision["max"] == 0:
        return {"selectedRefs": []}
    blind = decision["hidden"] and all("card" not in item for item in candidates)
    all_required = len(candidates) == count and (
        count <= 1 or not decision.get("indexed")
    )
    if count == decision["max"] and (blind or all_required):
        return {"selectedRefs": [item["ref"] for item in candidates[:count]]}
    return None


def describe_choice(decision, choice):
    if decision["kind"] == "options":
        option = next(o for o in decision["options"] if o["id"] == choice["optionId"])
        return _fields(
            option,
            (
                "actionType",
                "source",
                "sourceId",
                "target",
                "active_pokemon",
                "attack",
                "ability",
                "sourcePosition",
                "sourceIndex",
                "targetPosition",
                "targetIndex",
                "active_pokemonPosition",
                "active_pokemonIndex",
            ),
        )
    candidates = {item["ref"]: item for item in decision["candidates"]}
    chosen = [candidates[ref] for ref in choice["selectedRefs"]]
    return {
        **_fields(decision, ("source", "sourceId", "zones")),
        "selected": [item.get("card", {}).get("name") for item in chosen],
        "boardTargets": [item["boardRef"] for item in chosen if item.get("boardRef")],
    }


def _response_format(config, decision, allocation=None, effect_plan=None):
    if config.responseFormat == "text":
        return {}
    if config.responseFormat == "json_object":
        return {"response_format": {"type": "json_object"}}
    if effect_plan:
        key = "effectPlan"
        properties = {}
        for name, field in effect_plan["fields"].items():
            refs = [option["ref"] for option in field["options"]]
            properties[name] = {
                "type": "array",
                "items": {"type": "string", **({"enum": refs} if refs else {})},
                "minItems": field["min"],
                "maxItems": field["max"],
            }
        constraint = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
    elif allocation:
        key = "allocationRefs"
        constraint = {
            "type": "array",
            "items": {"type": "string", "enum": allocation["targetRefs"]},
            "minItems": allocation["remaining"],
            "maxItems": allocation["remaining"],
        }
    elif decision["kind"] == "options":
        key = "optionId"
        constraint = {"type": "string", "enum": [o["id"] for o in decision["options"]]}
    else:
        key = "selectedRefs"
        constraint = {
            "type": "array",
            "items": {"type": "string"},
            "minItems": decision["min"],
            "maxItems": decision["max"],
        }
        if decision["candidates"]:
            constraint["items"]["enum"] = [c["ref"] for c in decision["candidates"]]
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "battle_choice",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "choice": {
                            "type": "object",
                            "properties": {key: constraint},
                            "required": [key],
                            "additionalProperties": False,
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["choice", "reason"],
                    "additionalProperties": False,
                },
            },
        }
    }


def http_client():
    return httpx.Client(
        trust_env=False,
        limits=httpx.Limits(
            max_connections=20, max_keepalive_connections=10, keepalive_expiry=60
        ),
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
    )


def _request(config, visible, client=None):
    token_limit = (
        "max_completion_tokens"
        if urlsplit(config.baseUrl).hostname == "api.openai.com"
        else "max_tokens"
    )
    body = {
        "model": config.model,
        "stream": False,
        token_limit: config.maxTokens,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    visible, ensure_ascii=False, separators=(",", ":")
                ),
            },
        ],
        **_response_format(
            config,
            visible["decision"],
            visible.get("allocation"),
            visible.get("effectPlan"),
        ),
    }
    if config.reasoningEffort is not None:
        body["reasoning_effort"] = config.reasoningEffort
    key = config.apiKey.get_secret_value()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    with nullcontext(client) if client is not None else http_client() as session:
        response = session.post(
            f"{config.baseUrl}/chat/completions",
            json=body,
            headers=headers,
            timeout=config.timeoutSeconds,
        )
        response.raise_for_status()
        return response.json()


def _usage(result):
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        key: usage[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if type(usage.get(key)) is int and usage[key] >= 0
    } or None


def _retry_delay(response, attempt):
    delay = 0.75 * 2**attempt
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if not retry_after:
        return delay
    if retry_after.isascii() and retry_after.isdecimal():
        digits = retry_after.lstrip("0")
        seconds = MAX_RETRY_DELAY + 1 if len(digits) > 2 else int(digits or "0")
    else:
        try:
            seconds = parsedate_to_datetime(retry_after).timestamp() - time.time()
        except (ValueError, OverflowError, OSError):
            return delay
    return max(delay, seconds)


def _request_with_retries(config, visible, client=None):
    attempts = []
    for attempt in range(config.maxRetries + 1):
        began = time.monotonic()
        response = None
        retryable = False
        failure_type = None
        try:
            result = (
                _request(config, visible, client)
                if client is not None
                else _request(config, visible)
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            status = response.status_code
            failure = f"http-{status}"
            retryable = status in (408, 429) or (
                500 <= status < 600 and status not in (501, 505)
            )
        except httpx.TimeoutException as exc:
            failure = "timeout"
            failure_type = type(exc).__name__
            retryable = True
        except httpx.RequestError as exc:
            failure = "connection"
            failure_type = type(exc).__name__
            retryable = isinstance(
                exc,
                (
                    httpx.NetworkError,
                    httpx.RemoteProtocolError,
                    httpx.ProxyError,
                ),
            )
        except ValueError:
            failure = "invalid-response"
        else:
            attempts.append(
                {"milliseconds": round((time.monotonic() - began) * 1000, 3)}
            )
            return result, attempts, None
        attempts.append(
            {
                "failure": failure,
                "milliseconds": round((time.monotonic() - began) * 1000, 3),
            }
        )
        if failure_type:
            attempts[-1]["failureType"] = failure_type
        if not retryable or attempt == config.maxRetries:
            break
        delay = _retry_delay(response, attempt)
        if delay > MAX_RETRY_DELAY:
            break
        attempts[-1]["retryDelaySeconds"] = round(delay, 3)
        sleep(delay)
    return None, attempts, failure


def decision_detail(view, choice, detail, milliseconds=0):
    return {
        **detail,
        "choice": choice,
        "action": describe_choice(view["decision"], choice),
        "milliseconds": milliseconds,
    }


def fallback(view, detail, error):
    command = allocations.checked_command(view, _forced(view["decision"]))
    if command is None:
        baseline, _ = h2.decide(view)
        command = allocations.checked_command(view, baseline["choice"])
    detail = {**detail, "status": "fallback", "failure": error}
    detail.pop("effectPlan", None)
    detail.pop("allocationPlan", None)
    return command, decision_detail(
        view, command["choice"], detail, detail["milliseconds"]
    )


def decide(view, history, config, own_lines, offer=None, client=None):
    started = time.monotonic()
    decision = view["decision"]
    allocation = None if offer else allocations.window(view, history)
    command = {
        "commandId": f"llm-{view['stateVersion']}",
        "expectedStateVersion": view["stateVersion"],
        "decisionId": decision["id"],
        "choice": _forced(decision),
    }
    detail = {"status": "forced", "requestCount": 0}
    if allocation and command["choice"]:
        detail["allocationPlan"] = {
            "effect": allocation["effect"],
            "targetRefs": command["choice"]["selectedRefs"] * allocation["remaining"],
        }
    if offer or not legal(view, command):
        visible = payload(view, history, own_lines)
        if allocation:
            visible["allocation"] = allocation
        if offer:
            visible["effectPlan"] = offer.visible()
        result, attempts, error = _request_with_retries(config, visible, client)
        detail.update(
            status="model",
            requestCount=len(attempts),
            attempts=attempts,
            input=visible,
            usage=None,
        )
        if error:
            detail["milliseconds"] = round((time.monotonic() - started) * 1000, 3)
            return fallback(view, detail, error)
        error = None
        try:
            detail["usage"] = _usage(result)
            answer = result["choices"][0]
            if answer["finish_reason"] != "stop":
                error = (
                    "output-limit"
                    if answer["finish_reason"] == "length"
                    else "incomplete"
                )
            else:
                parsed = json.loads(answer["message"]["content"])
                command["choice"] = parsed["choice"]
                if offer:
                    choice = command["choice"]
                    plan = (
                        choice.get("effectPlan") if isinstance(choice, dict) else None
                    )
                    if not offer.steps(plan):
                        error = "illegal-effect-plan"
                    else:
                        detail["effectPlan"] = {"effect": offer.effect, "choices": plan}
                elif allocation:
                    refs = (
                        command["choice"].get("allocationRefs")
                        if isinstance(command["choice"], dict)
                        else None
                    )
                    if not allocations.valid_plan(refs, allocation):
                        error = "illegal-allocation"
                    else:
                        command["choice"] = {"selectedRefs": refs[:1]}
                        detail["allocationPlan"] = {
                            "effect": allocation["effect"],
                            "targetRefs": refs,
                        }
                if error is None and not offer and not legal(view, command):
                    error = "illegal-choice"
                if error is None and isinstance(parsed.get("reason"), str):
                    detail["reason"] = parsed["reason"].strip()[:280]
        except (KeyError, IndexError, AttributeError, TypeError, ValueError):
            error = "invalid-response"
        if error:
            detail["milliseconds"] = round((time.monotonic() - started) * 1000, 3)
            return fallback(view, detail, error)
    elapsed = round((time.monotonic() - started) * 1000, 3)
    if "effectPlan" in detail:
        return None, {**detail, "milliseconds": elapsed}
    return command, decision_detail(view, command["choice"], detail, elapsed)
