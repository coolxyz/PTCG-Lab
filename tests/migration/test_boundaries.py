"""Boundary gates are exercised by deliberately invalid scaffold changes."""

import json
from pathlib import Path
import shutil

import pytest
from scripts.migration.check_boundaries import check
from scripts.migration.build_scaffolds import check_members

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def scaffold(tmp_path):
    registry = json.loads((ROOT / "releases/projects.json").read_text())
    for project in registry["projects"]:
        source = ROOT / project["path"]
        for p in source.rglob("*"):
            if p.is_file() and not any(
                x in p.parts for x in ["node_modules", "__pycache__"]
            ):
                q = tmp_path / p.relative_to(ROOT)
                q.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, q)
    for name in [
        "releases/projects.json",
        "releases/migration-status.json",
        "docs/产品化重构设计.md",
        "docs/产品化重构分阶段实施.md",
    ]:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, p)
    acceptance = ROOT / "releases/p2d-acceptance.json"
    if acceptance.is_file():
        shutil.copyfile(acceptance, tmp_path / "releases/p2d-acceptance.json")
    return tmp_path


def test_current_boundaries(scaffold):
    assert check(scaffold) == []


@pytest.mark.parametrize(
    "code",
    [
        "import apps.api.main",
        "from packages.ai import agent",
        "import sqlite3",
        "import fastapi",
        "import ptcg_ai_runtime",
        '__import__("os")',
        'importlib.import_module("apps.api")',
    ],
)
def test_core_dependency_leak_is_rejected(scaffold, code):
    (scaffold / "packages/battle-core/src/ptcg_core/leak.py").write_text(code)
    assert check(scaffold)


def test_import_side_effect_is_rejected(scaffold):
    (scaffold / "packages/battle-core/src/ptcg_core/__init__.py").write_text(
        'print("started")'
    )
    assert any("non-inert" in e for e in check(scaffold))


def test_gui_networking_is_rejected(scaffold):
    (scaffold / "packages/battle-ui/src/index.js").write_text(
        'fetch("https://example.com")'
    )
    assert any("networking" in e for e in check(scaffold))


def test_phase_marker_cannot_claim_p2_complete(scaffold):
    p = scaffold / "releases/migration-status.json"
    data = json.loads(p.read_text())
    data["phases"]["P2"] = "completed"
    data["subphases"]["P2-D"] = "planned"
    p.write_text(json.dumps(data))
    assert any("misrepresented" in e for e in check(scaffold))


def test_binary_artifact_member_allowlist():
    with pytest.raises(ValueError, match="Unexpected"):
        check_members(
            [("ptcg_core/var/private.json", b"private-sentinel")],
            {"ptcg_core/__init__.py"},
        )
    value = ("gh" + "p_" + "c" * 40).encode()
    with pytest.raises(ValueError) as exc:
        check_members([("ptcg_core/__init__.py", value)], {"ptcg_core/__init__.py"})
    assert value.decode() not in str(exc.value)


def test_gui_side_effect_import_cannot_bypass_boundary(scaffold):
    (scaffold / "packages/battle-ui/src/index.js").write_text(
        'import "some-network-client";'
    )
    assert any("external JS import" in e for e in check(scaffold))


@pytest.mark.parametrize(
    "code",
    [
        'import "../../../ai-runtime/src/index.js";',
        'import "@ptcg/online-client";',
        "new XMLHttpRequest();",
        'navigator.sendBeacon("/api/submit", "data");',
    ],
)
def test_shared_display_cannot_gain_mode_or_transport_dependencies(scaffold, code):
    (scaffold / "packages/battle-ui/src/leak.ts").write_text(code)
    assert check(scaffold)


def test_gui_dependency_contract_is_pinned(scaffold):
    p = scaffold / "packages/battle-ui/package.json"
    data = json.loads(p.read_text())
    data["dependencies"]["some-network-client"] = "1.0.0"
    p.write_text(json.dumps(data))
    assert any("contract" in e for e in check(scaffold))
