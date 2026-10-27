# ruff: noqa: F811
"""Only the configured current release may compute another command."""

import inspect
from dataclasses import replace
from unittest.mock import Mock

import pytest
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.worker import RuleWorker
from tests.online_v2.test_matches import game, command  # noqa: F401


def test_old_rule_cannot_be_enabled_by_an_extra_worker_capability(game):
    service, _, tokens, mid, controllers = game
    request = command(service.get(tokens[0], mid))
    service.worker.supports_release = Mock(return_value=True)
    compute = service.worker.command = Mock()
    service.repo.settings = replace(service.repo.settings, approved_rule_release_id="new-current-rules")
    with pytest.raises(ServiceError, match="RULE_RELEASE_UNAVAILABLE"):
        service.command(tokens[0], mid, request, controllers[0])
    compute.assert_not_called()
    service.worker.supports_release.assert_not_called()


def test_rule_worker_has_no_alternate_code_directory():
    assert "pythonpath" not in inspect.signature(RuleWorker).parameters
    import importlib.util

    assert importlib.util.find_spec("ptcg_online_server.multirelease") is None
