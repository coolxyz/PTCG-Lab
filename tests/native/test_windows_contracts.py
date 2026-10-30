"""Platform-independent policy tests. Mocks never count as native Windows proof."""

import os
import sys
import xml.etree.ElementTree as ET

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ptcg_desktop_api.windows_storage import (
    DPAPICipher,
    FILE_ALL_ACCESS,
    MAGIC,
    SYSTEM_SID,
    private_acl,
    windows_vault,
)
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server

USER = "S-1-5-21-100-200-300-1001"


class ModelDPAPI:
    """Authenticated test primitive to exercise entropy/flags, not emulate DPAPI."""

    def __init__(self):
        self.key = AESGCM(os.urandom(32))
        self.flags = []

    def CryptProtectData(self, raw, description, entropy, reserved, prompt, flags):
        assert description and reserved is prompt is None
        self.flags.append(flags)
        nonce = os.urandom(12)
        return nonce + self.key.encrypt(nonce, raw, entropy)

    def CryptUnprotectData(self, raw, entropy, reserved, prompt, flags):
        assert reserved is prompt is None
        self.flags.append(flags)
        return "synthetic", self.key.decrypt(raw[:12], raw[12:], entropy)


def test_dpapi_contract_binds_directory_and_server_and_forbids_machine_scope(tmp_path):
    crypt = ModelDPAPI()
    server = Server("https://windows.example.test")
    codec = DPAPICipher(tmp_path / "中文 profile", server, crypt=crypt)
    raw = b'{"pending":{"commandId":"exact-original"}}'
    encoded = codec.encode(raw)
    assert encoded.startswith(MAGIC) and raw not in encoded
    assert codec.decode(encoded) == raw
    assert codec.encode(raw) != encoded
    assert crypt.flags and set(crypt.flags) == {1}
    for root, profile in [
        (tmp_path / "other", server),
        (tmp_path / "中文 profile", Server("https://other.example.test")),
    ]:
        with pytest.raises(ClientError, match="INVALID_STORAGE"):
            DPAPICipher(root, profile, crypt=crypt).decode(encoded)
    with pytest.raises(ClientError, match="INVALID_STORAGE"):
        codec.decode(encoded[:-1] + bytes([encoded[-1] ^ 1]))


def test_dpapi_denial_or_plaintext_never_resets_or_falls_back(tmp_path):
    server = Server("https://windows.example.test")

    class Denied:
        def CryptProtectData(self, *args):
            raise RuntimeError("sensitive-system-diagnostic")

    with pytest.raises(ClientError) as result:
        DPAPICipher(tmp_path, server, crypt=Denied()).encode(b"synthetic")
    assert result.value.code == "STORAGE_UNAVAILABLE"
    assert "sensitive-system-diagnostic" not in str(result.value)
    with pytest.raises(ClientError, match="NATIVE_IDENTITY_IMPORT_REQUIRED"):
        DPAPICipher(tmp_path, server, existing=b"{}", crypt=Denied())


@pytest.mark.parametrize(
    "owner,entries,protected",
    [
        (SYSTEM_SID, [(0, 0, FILE_ALL_ACCESS, USER)], True),
        (USER, None, True),
        (USER, [(0, 0, FILE_ALL_ACCESS, USER)], False),
        (USER, [(0, 0, FILE_ALL_ACCESS, USER), (0, 0, 1, "S-1-1-0")], True),
        (USER, [(1, 0, FILE_ALL_ACCESS, USER)], True),
        (USER, [(0, 8, FILE_ALL_ACCESS, USER)], True),
        (USER, [(0, 0, 1, USER)], True),
    ],
)
def test_private_acl_rejects_foreign_null_inherited_or_ineffective_grants(
    owner, entries, protected
):
    with pytest.raises(ClientError, match="UNSAFE_STORAGE"):
        private_acl(owner, entries, USER, protected)


def test_private_acl_accepts_only_effective_owner_and_system():
    private_acl(
        USER,
        [(0, 3, FILE_ALL_ACCESS, USER), (0, 3, FILE_ALL_ACCESS, SYSTEM_SID)],
        USER,
        True,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows refusal contract")
def test_windows_store_cannot_fall_back_to_posix_or_create_plaintext(tmp_path):
    root = tmp_path / "unsupported"
    with pytest.raises(ClientError, match="CREDENTIAL_BACKEND_UNAVAILABLE"):
        windows_vault(root, Server("https://windows.example.test"))
    assert not root.exists()


def test_windows_report_rejects_skips_and_missing_real_cases(tmp_path):
    from scripts.native.windows_report import REQUIRED, summarize

    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for name in REQUIRED:
        ET.SubElement(suite, "testcase", name=name)
    path = tmp_path / "synthetic-report.xml"
    ET.ElementTree(root).write(path)
    assert summarize(path) == len(REQUIRED)
    ET.SubElement(suite[0], "skipped")
    ET.ElementTree(root).write(path)
    with pytest.raises(ValueError, match="incomplete"):
        summarize(path)
    suite.remove(suite[0])
    ET.ElementTree(root).write(path)
    with pytest.raises(ValueError, match="incomplete"):
        summarize(path)


def test_windows_rust_report_requires_all_cases_with_no_ignored_or_filtered():
    from scripts.native.windows_shell_check import REQUIRED, summarize

    lines = [f"test {name} ... ok" for name in sorted(REQUIRED)]
    summary = f"test result: ok. {len(lines)} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;"
    text = "\n".join([*lines, summary])
    assert summarize(text) == len(lines)
    for bad in [
        text.replace("0 ignored", "1 ignored"),
        text.replace("0 filtered", "1 filtered"),
        "\n".join([*lines[1:], summary]),
        text.replace(" ... ok", " ... FAILED", 1),
    ]:
        with pytest.raises(ValueError, match="incomplete"):
            summarize(bad)
