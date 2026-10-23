"""Asset-free contracts. Full engine execution is separately opt-in and private."""

import ast
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from ptcg_core.factory import CoreFactory, _command
from ptcg_core.engine_loader import RuleImports, VerifiedLoader
from ptcg_core.identity import execution_identity
from ptcg_engine_provider.bundle import DirectoryEngineProvider
from tests.providers.synthetic import write_bundle


def test_core_inert_and_closed_loader_outside_checkout(tmp_path):
    smoke = Path(__file__).with_name("smoke.py")
    subprocess.run([sys.executable, "-I", str(smoke)], cwd=tmp_path, check=True)


def test_only_explicit_rule_imports_are_relocated():
    tree = RuleImports().visit(
        ast.parse(
            "from packages.rules.sample import operation\nfrom ptcg.core.card import Card"
        )
    )
    assert tree.body[0].module == "ptcg_core.rules.sample"
    assert tree.body[1].module == "ptcg.core.card"


def test_unsafe_engine_module_is_rejected():
    with pytest.raises(ValueError, match="INVALID_ENGINE_MODULE"):
        VerifiedLoader({"ptcg/../escape.py": b""})


def valid_command():
    return {
        "commandId": "c1",
        "expectedStateVersion": 0,
        "decisionId": "d0",
        "choice": {"optionId": "d0:o0"},
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"expectedStateVersion": True},
        {"expectedStateVersion": -1},
        {"choice": {"optionId": "o", "selectedRefs": []}},
        {"choice": {}},
        {"choice": {"selectedRefs": "x"}},
        {"choice": {"selectedRefs": [None]}},
        {"choice": {"selectedRefs": ["x"] * 61}},
        {"commandId": ""},
        {"commandId": "a" * 17000},
        {"seat": "PLAYER2"},
        {"expectedMatchSeq": 0},
    ],
)
def test_invalid_commands_rejected(patch):
    with pytest.raises(ValueError):
        _command({**valid_command(), **patch})


def test_command_detached():
    command = valid_command()
    value = _command(command)
    value["choice"]["optionId"] = "changed"
    assert command["choice"]["optionId"] == "d0:o0"


def test_identity_is_bound_to_bundle():
    assert execution_identity(SimpleNamespace(bundle_id="a")) != execution_identity(
        SimpleNamespace(bundle_id="b")
    )


def test_admission_uses_pinned_policy(tmp_path):
    pin = write_bundle(tmp_path)
    bundle = DirectoryEngineProvider(tmp_path, pin).load()
    factory = CoreFactory.__new__(CoreFactory)
    factory.bundle = bundle
    entries = [
        {"printingId": "synthetic-basic", "quantity": 4},
        {"printingId": "synthetic-energy", "quantity": 56},
    ]
    assert factory.validate_deck(entries, as_of="2026-10-01")["playable"]
    entries[0]["quantity"] = 5
    result = factory.validate_deck(entries, as_of="2026-10-01")
    assert not result["playable"]
    assert "SAME_NAME_LIMIT" in {i["code"] for i in result["issues"]}


def test_refuse_legacy_or_different_identity_before_reconstruction():
    factory = CoreFactory.__new__(CoreFactory)
    factory.rule_release_id = "new"
    for record in (
        {"schema": "p01-replay-v1"},
        {"schema": "core-recovery-v2", "implementation": "old"},
    ):
        with pytest.raises(ValueError, match="ENGINE_VERSION_MISMATCH"):
            factory.restore_match(record)


def test_engine_preloaded_refused(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import sys
from types import ModuleType
from ptcg_core.runtime import activate
from ptcg_engine_provider.bundle import RuleBundle
sys.modules['ptcg'] = ModuleType('ptcg')
try:
    activate(object.__new__(RuleBundle))
except RuntimeError as e:
    assert str(e) == 'ENGINE_ALREADY_LOADED'
else:
    raise AssertionError('mixed runtimes allowed')
""",
        ],
        cwd=tmp_path,
        check=True,
    )


def test_failed_activation_poisoned_until_worker_replaced(tmp_path):
    pin = write_bundle(tmp_path)
    code = """
import sys
from pathlib import Path
from ptcg_core.runtime import activate, current
from ptcg_engine_provider.bundle import DirectoryEngineProvider
bundle = DirectoryEngineProvider(Path(sys.argv[1]), sys.argv[2]).load()
try:
    activate(bundle)
except RuntimeError as exc:
    assert str(exc) == 'engine must not be executed'
else:
    raise AssertionError('sentinel did not execute at explicit activation')
try:
    activate(bundle)
except RuntimeError as exc:
    assert str(exc) == 'CORE_ACTIVATION_FAILED'
else:
    raise AssertionError('poisoned worker reactivated')
try:
    current()
except RuntimeError as exc:
    assert str(exc) == 'CORE_NOT_ACTIVE'
else:
    raise AssertionError('poisoned worker returned state')
"""
    subprocess.run(
        [sys.executable, "-I", "-c", code, str(tmp_path), pin], cwd=tmp_path, check=True
    )
