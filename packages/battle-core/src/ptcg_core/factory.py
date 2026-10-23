"""Service-neutral match API. No database, network, AI, or working-directory input.

Views/commands here retain the legacy GUI shape. P2-A v2 transport envelopes are
not silently accepted; the later service adapter must map every visible field.
Recovery records are server-private, never a client replay payload.
"""

from ptcg_engine_provider.values import canonical, parse_json
from .cn_format import validate
from .runtime import activate


class AdmissionError(ValueError):
    pass


def _seat(viewer):
    from ptcg.core.enums import PlayerId

    if viewer not in ("PLAYER1", "PLAYER2"):
        raise ValueError("INVALID_VIEWER")
    return PlayerId[viewer]


def _command(command):
    raw = canonical(command)
    if len(raw) > 16384:
        raise ValueError("COMMAND_TOO_LARGE")
    result = parse_json(raw)
    if not isinstance(result, dict) or set(result) != {
        "commandId",
        "expectedStateVersion",
        "decisionId",
        "choice",
    }:
        raise ValueError("INVALID_COMMAND")
    if (
        type(result["expectedStateVersion"]) is not int
        or not 0 <= result["expectedStateVersion"] <= 9007199254740991
        or not isinstance(result["commandId"], str)
        or not 1 <= len(result["commandId"]) <= 128
        or not isinstance(result["decisionId"], str)
        or len(result["decisionId"]) > 128
    ):
        raise ValueError("INVALID_COMMAND")
    choice = result["choice"]
    if not isinstance(choice, dict) or set(choice) not in (
        {"optionId"},
        {"selectedRefs"},
    ):
        raise ValueError("INVALID_COMMAND")
    if "optionId" in choice and not isinstance(choice["optionId"], str):
        raise ValueError("INVALID_COMMAND")
    if "selectedRefs" in choice and (
        not isinstance(choice["selectedRefs"], list)
        or len(choice["selectedRefs"]) > 60
        or not all(isinstance(r, str) for r in choice["selectedRefs"])
    ):
        raise ValueError("INVALID_COMMAND")
    return result


class Match:
    """Host serializes access per match. This class starts no threads or workers."""

    def __init__(self, adapter, rule_release_id):
        self._adapter = adapter
        self.rule_release_id = rule_release_id

    @property
    def state_version(self):
        return self._adapter.version

    def project_view(self, viewer):
        from .projection import view

        return view(self._adapter, _seat(viewer))

    def apply_command(self, viewer, command):
        seat, checked = _seat(viewer), _command(command)
        if checked["commandId"] not in self._adapter.receipts:
            decision = self._adapter.view(seat)["decision"]
            if decision is not None:
                key = "selectedRefs" if decision["kind"] == "selection" else "optionId"
                if key not in checked["choice"]:
                    raise ValueError("INVALID_CHOICE_KIND")
        return self._adapter.submit(seat, checked)

    def export_recovery_record(self):
        return self._adapter.export_private_replay()


class CoreFactory:
    def __init__(self, bundle):
        self.runtime = activate(bundle)
        self.bundle = self.runtime.bundle
        self.rule_release_id = self.runtime.release_id

    def validate_deck(self, entries, *, as_of):
        rows = self.bundle.catalog.resolve(entries)
        result = validate(
            [{"quantity": r.quantity, "printing": r.printing} for r in rows],
            as_of,
            rules=self.bundle.documents["format"].value,
        )
        for code, support in (
            ("OUTSIDE_RULE_SCOPE", self.bundle.scope_support(entries)),
            ("UNSUPPORTED_EFFECT", self.bundle.effect_support(entries)),
        ):
            if not support.supported:
                result["issues"].append({"code": code, "level": "engine"})
                result["playable"] = False
        return result

    def create_match(self, deck1, deck2, *, seed, as_of):
        if type(seed) is not int or not 0 <= seed <= 9007199254740991:
            raise ValueError("INVALID_SEED")
        lines = []
        for entries in (deck1, deck2):
            if not self.validate_deck(entries, as_of=as_of)["playable"]:
                raise AdmissionError("DECK_NOT_PLAYABLE")
            lines.append(list(self.bundle.catalog.resolve_engine_lines(entries)))
        from .rules.adapter import Adapter

        provenance = {
            "catalogVersion": self.bundle.catalog.catalog_version,
            "formatAsOf": as_of,
            "deck1": parse_json(canonical(deck1)),
            "deck2": parse_json(canonical(deck2)),
        }
        return Match(Adapter(seed, *lines, provenance=provenance), self.rule_release_id)

    def restore_match(self, record):
        # Private, authenticated storage input, never a network deserialization API.
        record = parse_json(canonical(record))
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "schema",
                "implementation",
                "baseCommit",
                "config",
                "commands",
                "stateDigest",
            }
            or record.get("schema") != "core-recovery-v2"
            or record.get("implementation") != self.rule_release_id
        ):
            raise ValueError("ENGINE_VERSION_MISMATCH")
        from .rules.adapter import Adapter

        return Match(Adapter.replay(record), self.rule_release_id)
