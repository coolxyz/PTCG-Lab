"""Inspect pinned immutable inputs without importing or running the rule engine."""

from ptcg_core.identity import execution_identity
from ptcg_engine_provider.bundle import DirectoryEngineProvider


class RuleStatus:
    def __init__(self, settings):
        self.release_id = None
        self.catalog_version = "unavailable"
        self.failure = "RULE_RELEASE_UNAVAILABLE"
        if not all(
            (
                settings.rule_directory,
                settings.rule_manifest_sha256,
                settings.approved_rule_release_id,
            )
        ):
            return
        try:
            bundle = DirectoryEngineProvider(
                settings.rule_directory, settings.rule_manifest_sha256
            ).load()
            release = execution_identity(bundle)
            if release != settings.approved_rule_release_id:
                return
            self.release_id = release
            self.catalog_version = bundle.catalog.catalog_version
            self.failure = None
        except Exception:
            # Never put paths, manifest contents or loader exceptions on the wire.
            return
