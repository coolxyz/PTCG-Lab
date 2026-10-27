# ruff: noqa: E402
"""Enforce registered package dependencies, publication and migration status."""

import argparse
import ast
import json
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.public.policy import source_files, safe_file
from scripts.migration.p4d_report import evidence_valid
from scripts.migration.p6b_report import valid as p6b_valid
from scripts.migration.p6c_report import valid as p6c_valid
from scripts.migration.p6d_report import valid as p6d_valid
from scripts.migration.p6e_report import valid as p6e_valid
from scripts.migration.p7a_report import valid as p7a_valid
from scripts.migration.p7b_report import valid as p7b_valid


def check(root=ROOT):
    root = Path(root).resolve()
    registry = json.loads((root / "releases/projects.json").read_text())
    projects = registry["projects"]
    errors = []
    export = set(source_files(root))
    python_modules = {p["module"]: p for p in projects if p["language"] == "python"}
    names = [p["distribution"] for p in projects]
    if len(names) != len(set(names)):
        errors.append("duplicate distribution")
    for project in projects:
        base = root / project["path"]
        protocol = project["distribution"] in {
            "ptcg-battle-protocol",
            "@ptcg/battle-protocol",
        }
        provider = project["distribution"] == "ptcg-engine-provider"
        core = project["distribution"] == "ptcg-battle-core"
        online = project["distribution"] == "ptcg-online-server"
        client = project["distribution"] == "ptcg-online-client"
        desktop = project["distribution"] == "ptcg-desktop-api"
        gui = project["distribution"] in {"@ptcg/battle-ui", "@ptcg/desktop-web"}
        expected_stage = (
            ("P5.5-C" if project["distribution"] == "@ptcg/desktop-web" else "P3-B")
            if gui
            else (
                "P6-E"
                if online
                else (
                    "P5-A"
                    if client
                    else (
                        "P5.5-C"
                        if desktop
                        else (
                            "P2-D"
                            if (protocol or core)
                            else "P2-B"
                            if provider
                            else "P1"
                        )
                    )
                )
            )
        )
        if (
            project["stage"] != expected_stage
            or project["implementationReady"] is not False
        ):
            errors.append(project["path"] + ": scaffold status drift")
        if project["language"] == "python":
            metadata = tomllib.loads(
                safe_file(root, project["path"] + "/pyproject.toml").read_text()
            )
            if metadata["project"]["name"] != project["distribution"]:
                errors.append(project["path"] + ": distribution mismatch")
            if (
                metadata["project"].get("dependencies", [])
                != project["allowedRuntimeRequirements"]
            ):
                errors.append(
                    project["path"] + ": runtime requirements differ from contract"
                )
            if (
                not (protocol or core or online or client or desktop)
                and project["allowedRuntimeRequirements"]
            ):
                errors.append(
                    project["path"] + ": inactive scaffold gained runtime requirements"
                )
            for file in sorted((base / "src").rglob("*.py")):
                rel = file.relative_to(root).as_posix()
                tree = ast.parse(safe_file(root, rel).read_text())
                for node in ast.walk(tree):
                    imports = []
                    if isinstance(node, ast.Import):
                        imports = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and not node.level:
                        imports = [node.module or ""]
                    elif isinstance(node, ast.Call):
                        # Dynamic imports and import-path modification bypass static contracts.
                        name = ast.unparse(node.func)
                        if name in {
                            "__import__",
                            "eval",
                            "exec",
                            "importlib.import_module",
                            "sys.path.insert",
                            "sys.path.append",
                        }:
                            audited = {
                                "packages/battle-core/src/ptcg_core/engine_loader.py": {
                                    "exec"
                                },
                                "packages/battle-core/src/ptcg_core/engine_registry.py": {
                                    "importlib.import_module"
                                },
                                "packages/battle-core/src/ptcg_core/compat.py": {
                                    "importlib.import_module"
                                },
                            }
                            if name not in audited.get(rel, set()):
                                errors.append(rel + ": dynamic import/path escape")
                    for name in imports:
                        top = name.split(".")[0]
                        allowed = {
                            project["module"],
                            *project["allowedInternalImports"],
                            *project["allowedExternalImports"],
                        }
                        if top not in allowed or (
                            (protocol or provider)
                            and top
                            in {
                                "apps",
                                "packages",
                                "ptcg",
                                "fastapi",
                                "sqlite3",
                                "torch",
                            }
                        ):
                            errors.append(rel + ": forbidden import " + top)
                        if (
                            top in python_modules
                            and top != project["module"]
                            and top not in project["allowedInternalImports"]
                        ):
                            errors.append(rel + ": undeclared project dependency")
                if file.name == "__init__.py":
                    # P1 imports must be inert: only docstrings and constant metadata.
                    for node in tree.body:
                        if isinstance(node, ast.Expr) and isinstance(
                            node.value, ast.Constant
                        ):
                            continue
                        if isinstance(node, ast.Assign) and isinstance(
                            node.value, ast.Constant
                        ):
                            continue
                        errors.append(rel + ": non-inert scaffold initialization")
                if rel not in export:
                    errors.append(rel + ": not exported")
        else:
            metadata = json.loads(
                safe_file(root, project["path"] + "/package.json").read_text()
            )
            if (
                metadata["name"] != project["distribution"]
                or metadata.get("private") is not True
            ):
                errors.append(
                    project["path"] + ": JS package identity/private flag mismatch"
                )
            for field, contract in (
                ("dependencies", "allowedPackageDependencies"),
                ("peerDependencies", "allowedPeerDependencies"),
                ("scripts", "allowedScripts"),
            ):
                if metadata.get(field, {}) != project.get(contract, {}):
                    errors.append(
                        project["path"]
                        + ": JS dependencies/scripts differ from contract"
                    )
            for file in (base / "src").rglob("*"):
                if file.is_file() and file.suffix in {".js", ".ts", ".tsx"}:
                    rel = file.relative_to(root).as_posix()
                    content = safe_file(root, rel).read_text()
                    refs = re.findall(
                        r"""(?:from\s*|import\s*(?:\(\s*)?|require\s*\(\s*)['"]([^'"]+)""",
                        content,
                    )
                    allowed_refs = set(
                        project["allowedInternalImports"]
                        + project["allowedExternalImports"]
                    )
                    if any(
                        (
                            not (file.parent / ref)
                            .resolve()
                            .is_relative_to(base.resolve())
                            if ref.startswith(".")
                            else ref not in allowed_refs
                        )
                        for ref in refs
                    ):
                        errors.append(rel + ": cross-package or external JS import")
                    capabilities = set(
                        re.findall(
                            r"\b(fetch|WebSocket|XMLHttpRequest|localStorage|sessionStorage|sendBeacon)\b",
                            content,
                        )
                    )
                    audited_capabilities = {
                        # Host-owned visual preferences; battle-ui stays storage-free.
                        "apps/desktop-web/src/appearance-storage.ts": {"localStorage"},
                        "apps/desktop-web/src/online/transport.ts": {"fetch"},
                        "apps/desktop-web/src/online/storage.ts": {"sessionStorage"},
                        "apps/desktop-web/src/practice/transport.ts": {"fetch"},
                        "apps/desktop-web/src/practice/storage.ts": {
                            "localStorage",
                            "sessionStorage",
                        },
                    }
                    if capabilities - audited_capabilities.get(rel, set()):
                        errors.append(
                            rel + ": networking/storage outside audited mode adapter"
                        )
                    if rel not in export:
                        errors.append(rel + ": not exported")
        for filename in [
            "LICENSE",
            "README.md",
            "pyproject.toml" if project["language"] == "python" else "package.json",
        ]:
            name = project["path"] + "/" + filename
            if name not in export:
                errors.append(name + ": metadata/license not exported")
    status = json.loads((root / "releases/migration-status.json").read_text())
    if (
        status.get("currentPhase") not in {"P5", "P5.5", "P6", "P7"}
        or status.get("productionRoutingChanged") is not False
    ):
        errors.append("migration phase/routing marker drift")
    phase = status["phases"].get("P2")
    if phase not in {"in_progress", "completed"} or (
        phase == "completed"
        and (
            status.get("subphases", {}).get("P2-D") != "completed"
            or not (root / "releases/p2d-acceptance.json").is_file()
        )
    ):
        errors.append("P2 implementation misrepresented as complete")
    p3_phase = status["phases"].get("P3")
    if p3_phase not in {"in_progress", "completed"} or (
        p3_phase == "completed"
        and (
            any(
                status.get("subphases", {}).get(p) != "completed"
                for p in ("P3-A", "P3-B", "P3-C")
            )
            or status.get("p3cAcceptanceDocument") != "docs/P3C完整产品接入验收.md"
            or not (root / "docs/P3C完整产品接入验收.md").is_file()
        )
    ):
        errors.append("P3 implementation misrepresented as complete")
    p4_phase = status["phases"].get("P4")
    if p4_phase not in {"in_progress", "completed"} or (
        p4_phase == "completed"
        and (
            any(
                status.get("subphases", {}).get(p) != "completed"
                for p in ("P4-A", "P4-B", "P4-C", "P4-D")
            )
            or status.get("p4dAcceptanceDocument")
            != "docs/P4D完整对局与故障恢复验收.md"
            or not (root / "docs/P4D完整对局与故障恢复验收.md").is_file()
            or not evidence_valid(root)
        )
    ):
        errors.append("P4 implementation misrepresented as complete")
    try:
        p5 = json.loads((root / "releases/p5d-acceptance.json").read_text())
        manual = json.loads((root / "releases/p5d-user-acceptance.json").read_text())
        p5_accepted = (
            p5.get("status") == "accepted_with_deferred_display_fixes"
            and p5.get("physicalDeviceAcceptance") == "user_reported_completed"
            and p5.get("gui", {}).get("status") == "passed"
            and manual.get("source") == "direct_user_report"
            and manual.get("decision") == "accepted_with_deferred_display_fixes"
            and manual.get("reportedResults")
            == {"roomCreation": "passed", "gameplay": "passed"}
            and manual.get("displayIssues", {}).get("target") == "after P5.5"
            and status.get("subphases", {}).get("P5-D") == "completed"
        )
    except (OSError, ValueError):
        p5_accepted = False
    if status["phases"].get("P5") not in {"in_progress", "completed"} or (
        status["phases"].get("P5") == "completed" and not p5_accepted
    ):
        errors.append("P5 implementation misrepresented as complete")
    if status.get("currentPhase") in {"P5.5", "P6", "P7"} and (
        status["phases"].get("P5") != "completed"
        or not p5_accepted
        or status["phases"].get("P5.5") not in {"in_progress", "completed"}
        or status.get("subphases", {}).get("P5.5-A") not in {"in_progress", "completed"}
    ):
        errors.append("product integration phase lacks accepted P5 baseline")
    if status["phases"].get("P5.5") == "completed":
        try:
            completion = json.loads(
                (root / "releases/p55-user-acceptance.json").read_text()
            )
            p55_accepted = (
                completion.get("schema") == 1
                and completion.get("stage") == "P5.5"
                and completion.get("source") == "direct_user_instruction"
                and completion.get("decision") == "completed_with_followups"
                and bool(completion.get("userStatement"))
                and bool(completion.get("limitations"))
                and completion.get("physicalDeviceAcceptance")
                == "not_individually_reported"
                and isinstance(completion.get("followups"), list)
                and bool(completion["followups"])
                and bool(
                    re.fullmatch(
                        r"[a-f0-9]{7,40}", completion.get("implementationCommit", "")
                    )
                )
                and completion["implementationCommit"]
                == status.get("p55ImplementationCommit")
                and status.get("p55UserAcceptanceDocument")
                == "releases/p55-user-acceptance.json"
                and status.get("p55CompletionDocument")
                == "docs/P5.5产品整合完成记录.md"
                and (root / "docs/P5.5产品整合完成记录.md").is_file()
                and all(
                    status.get("subphases", {}).get(p) == "completed"
                    for p in ("P5.5-A", "P5.5-B", "P5.5-C", "P5.5-D")
                )
            )
        except (OSError, ValueError, TypeError, AttributeError):
            p55_accepted = False
        if not p55_accepted:
            errors.append("P5.5 completion lacks user decision or follow-up scope")
    for filename in ("session", "lobby", "match"):
        name = filename + ".schema.json"
        if (
            safe_file(
                root, "packages/online-client/src/ptcg_online_client/schemas/" + name
            ).read_bytes()
            != safe_file(
                root, "apps/online-server/src/ptcg_online_server/schemas/" + name
            ).read_bytes()
        ):
            errors.append("native client/server envelope schema drift")
    if status.get("currentPhase") in {"P6", "P7"} and (
        status["phases"].get("P5.5") != "completed"
        or status["phases"].get("P6") not in {"in_progress", "completed"}
        or status.get("subphases", {}).get("P6-A") != "completed"
        or status.get("subphases", {}).get("P6-E") not in {"in_progress", "completed"}
        or status.get("subphases", {}).get("P6-D") != "completed"
        or status.get("p6dImplementationCommit") != "f7ce35d"
        or status.get("subphases", {}).get("P6-B") != "completed"
        or status.get("subphases", {}).get("P6-C") != "completed"
        or status.get("p6cImplementationCommit") != "691fc0a"
        or status.get("p6bImplementationCommit") != "3ca9ca0"
        or status.get("p6aImplementationCommit") != "5f1c16f"
        or status.get("p6ProgressDocument") != "docs/P6多局调度与性能实施记录.md"
        or not (root / "docs/P6多局调度与性能实施记录.md").is_file()
    ):
        errors.append("P6 baseline or bounded implementation scope missing")
    if status.get("subphases", {}).get("P6-A") == "completed":
        try:
            baseline = json.loads((root / "releases/p6a-baseline.json").read_text())
            service = json.loads((root / "releases/p6b-streams.json").read_text())
            p6a_valid = (
                baseline.get("stage") == "P6-A"
                and baseline.get("status") == "passed"
                and baseline.get("serverSourceSha256")
                == service.get("baselineServerSourceSha256")
                and baseline.get("capacityMeasured") is False
                and baseline.get("productionRoutingChanged") is False
                and baseline.get("workload", {}).get("samplesPerMode") == 3
                and set(baseline.get("modes", {})) == {"newConnection", "pool"}
                and status.get("p6aAcceptanceDocument") == "releases/p6a-baseline.json"
            )
        except (OSError, ValueError, TypeError, AttributeError):
            p6a_valid = False
        if not p6a_valid:
            errors.append("P6-A completion lacks matching measured baseline")
    if status.get("subphases", {}).get("P6-B") == "completed" and (
        not p6b_valid(root, historical=True)
        or status.get("p6bAcceptanceDocument") != "releases/p6b-streams.json"
    ):
        errors.append("P6-B completion lacks current stream acceptance")
    if status.get("subphases", {}).get("P6-C") == "completed" and (
        not p6c_valid(root, historical=True)
        or status.get("p6cAcceptanceDocument") != "releases/p6c-scheduler.json"
    ):
        errors.append("P6-C completion lacks current scheduler acceptance")
    if status.get("subphases", {}).get("P6-D") == "completed" and (
        not p6d_valid(root, historical=True)
        or status.get("p6dAcceptanceDocument") != "releases/p6d-workers.json"
    ):
        errors.append("P6-D completion lacks current worker acceptance")
    if status.get("subphases", {}).get("P6-E") == "completed" and (
        not p6e_valid(root)
        or status.get("p6eAcceptanceDocument") != "releases/p6e-budgets.json"
    ):
        errors.append("P6-E completion lacks current budget acceptance")
    if status["phases"].get("P6") == "completed" and (
        any(
            status.get("subphases", {}).get("P6-" + part) != "completed"
            for part in "ABCDE"
        )
        or not p6e_valid(root)
    ):
        errors.append("P6 completion lacks all bounded implementation stages")
    if status.get("currentPhase") == "P7" and (
        status["phases"].get("P6") != "completed"
        or status["phases"].get("P7") != "in_progress"
        or status.get("p7BaselineCommit") != "41fe0f1"
        or status.get("p7ProgressDocument") != "docs/P7容量与故障验收实施记录.md"
        or not (root / "docs/P7容量与故障验收实施记录.md").is_file()
        or status.get("subphases", {}).get("P7-A") not in {"in_progress", "completed"}
        or not p6e_valid(root)
    ):
        errors.append("P7 start lacks accepted P6 baseline or bounded scope")
    if status.get("subphases", {}).get("P7-A") == "completed" and (
        not p7a_valid(
            root, historical=status.get("subphases", {}).get("P7-B") != "planned"
        )
        or status.get("p7aAcceptanceDocument") != "releases/p7a-isolation.json"
    ):
        errors.append("P7-A completion lacks current isolated pilot evidence")
    if status.get("subphases", {}).get("P7-B", "planned") not in {
        "planned",
        "in_progress",
    } or (
        status.get("subphases", {}).get("P7-B") == "in_progress"
        and (
            not p7b_valid(root)
            or status.get("p7bProgressDocument") != "releases/p7b-matrix.json"
        )
    ):
        errors.append("P7-B progress lacks current formal matrix evidence")
    if any(
        status.get("subphases", {}).get("P7-" + p, "planned") != "planned" for p in "CD"
    ):
        errors.append("P7 formal matrix or soak misrepresented as complete")
    # A pilot and a tooling milestone cannot close the full capacity matrix.
    if (
        status["phases"].get("P7") not in {"planned", "in_progress"}
        or (
            status["phases"].get("P7") == "in_progress"
            and status.get("currentPhase") != "P7"
        )
        or status["phases"].get("P8") != "planned"
    ):
        errors.append("future implementation misrepresented as complete")
    for name in ["docs/产品化重构设计.md", "docs/产品化重构分阶段实施.md"]:
        text = (root / name).read_text()
        if (
            f"<!-- migration-phase: {status.get('currentPhase')}; production-routing: legacy -->"
            not in text
        ):
            errors.append(name + ": missing phase status")
    return sorted(set(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    errors = check()
    print(json.dumps({"boundaryErrors": errors}, ensure_ascii=False))
    raise SystemExit(bool(errors))
