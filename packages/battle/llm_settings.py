"""Local practice provider configuration and connection check."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import httpx

from packages.battle import llm
from packages.collection.domain import DomainError
from packages.simulation.a2 import legal


def _path(db_path):
    return Path(db_path).parent / "llm-opponent.json"


def describe(db_path):
    current = llm.settings(db_path)
    return {
        "settings": current.model_dump(exclude={"apiKey"}) if current else None,
        "hasApiKey": bool(current and current.apiKey.get_secret_value()),
        "environmentManaged": any(
            name in os.environ for name in llm.SETTINGS_ENV.values()
        ),
    }


def resolve(db_path, values):
    if not isinstance(values, dict):
        raise DomainError("LLM_INVALID_SETTINGS", "请检查 LLM 配置。", 422)
    current = llm.settings(db_path)
    try:
        proposed = llm.Settings.model_validate(values)
    except (ValueError, httpx.InvalidURL):
        raise DomainError(
            "LLM_INVALID_SETTINGS", "请检查 API 地址、模型名称和其他参数。", 422
        ) from None

    url = urlsplit(proposed.baseUrl)
    if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise DomainError("LLM_HTTPS_REQUIRED", "远程 API 地址须使用 HTTPS。", 422)
    if (
        not proposed.apiKey.get_secret_value()
        and current
        and current.apiKey.get_secret_value()
    ):
        if proposed.baseUrl != current.baseUrl:
            raise DomainError(
                "LLM_KEY_REQUIRED", "API 地址已改变，请输入该服务的 API 密钥。", 422
            )
        return proposed.model_copy(update={"apiKey": current.apiKey})
    return proposed


def save(db_path, values):
    if any(name in os.environ for name in llm.SETTINGS_ENV.values()):
        raise DomainError("LLM_ENV_MANAGED", "此配置由启动环境变量管理。", 409)
    config = resolve(db_path, values)
    target = _path(db_path)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(
                {
                    **config.model_dump(exclude={"apiKey"}),
                    "apiKey": config.apiKey.get_secret_value(),
                },
                file,
                ensure_ascii=False,
            )
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(target)
    except OSError:
        raise DomainError("LLM_SAVE_FAILED", "无法保存本机 LLM 配置。", 500) from None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return describe(db_path)


def test(config):
    """Use the live completion format with a synthetic two-choice request."""
    view = {
        "stateVersion": 0,
        "phase": "playing",
        "observation": {"self": {"hand": []}, "opponent": {"hand": None}},
        "decision": {
            "id": "d0",
            "kind": "options",
            "options": [
                {"id": "d0:o0", "actionType": "PassTurn"},
                {"id": "d0:o1", "actionType": "PassTurn"},
            ],
        },
    }
    # One explicit test click sends one request and never invokes the battle fallback.
    config = config.model_copy(update={"maxRetries": 0})
    result, attempts, failure = llm._request_with_retries(
        config, llm.payload(view, [], [])
    )
    if not failure:
        try:
            answer = result["choices"][0]
            parsed = json.loads(answer["message"]["content"])
            choice = parsed["choice"]
            if answer["finish_reason"] != "stop" or not legal(
                view,
                {
                    "expectedStateVersion": 0,
                    "decisionId": "d0",
                    "choice": choice,
                },
            ):
                failure = "invalid-response"
        except (KeyError, IndexError, TypeError, ValueError):
            failure = "invalid-response"
    return {
        "ok": failure is None,
        "failure": failure,
        "milliseconds": attempts[0]["milliseconds"],
    }
