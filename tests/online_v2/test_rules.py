from dataclasses import replace
import sys
from tests.providers.synthetic import write_bundle
from ptcg_core.identity import execution_identity
from ptcg_engine_provider.bundle import DirectoryEngineProvider
from ptcg_online_server.config import Settings
from ptcg_online_server.rules import RuleStatus


def test_approved_rule_inputs_are_pinned_without_executing_engine(tmp_path):
    directory = tmp_path / "bundle"
    pin = write_bundle(directory)
    bundle = DirectoryEngineProvider(directory, pin).load()
    settings = Settings(
        "postgresql://synthetic.invalid/db",
        "test",
        "https://api.example.test",
        rule_directory=str(directory),
        rule_manifest_sha256=pin,
        approved_rule_release_id=execution_identity(bundle),
    )
    before = set(sys.modules)
    loaded = RuleStatus(settings)
    assert loaded.failure is None
    assert loaded.catalog_version == bundle.catalog.catalog_version
    assert not any(
        m == "ptcg" or m.startswith("ptcg.") for m in set(sys.modules) - before
    )
    assert RuleStatus(replace(settings, approved_rule_release_id="unapproved")).failure
    assert RuleStatus(replace(settings, rule_manifest_sha256="0" * 64)).failure
    (directory / "assets/catalog.json").write_text("tampered")
    assert RuleStatus(settings).failure
