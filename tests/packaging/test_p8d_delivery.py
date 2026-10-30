"""Cross-delivery cannot be relabelled as native platform acceptance."""

from copy import deepcopy
import json
import struct

import pytest
from scripts.packaging.d_windows import pe_gui, VERIFY


def pe(tmp_path, machine=0x8664, magic=0x20B, subsystem=2):
    value = bytearray(512)
    value[:2] = b"MZ"
    struct.pack_into("<I", value, 60, 128)
    value[128:132] = b"PE\0\0"
    struct.pack_into("<H", value, 132, machine)
    struct.pack_into("<H", value, 152, magic)
    struct.pack_into("<H", value, 220, subsystem)
    path = tmp_path / "synthetic-header.exe"
    path.write_bytes(value)
    return path


def test_cross_artifact_requires_real_x64_gui_headers(tmp_path):
    assert pe_gui(pe(tmp_path)) == {
        "machine": "AMD64",
        "format": "PE32+",
        "subsystem": "WINDOWS_GUI",
    }
    for args in [(0x14C, 0x10B, 2), (0xAA64, 0x20B, 2), (0x8664, 0x20B, 3)]:
        with pytest.raises(ValueError):
            pe_gui(pe(tmp_path, *args))
    for value in [b"", b"MZ", b"MZ" + bytes(500)]:
        p = tmp_path / "bad"
        p.write_bytes(value)
        with pytest.raises(ValueError):
            pe_gui(p)


def test_windows_verifier_checks_extra_files_and_preserves_policy():
    assert "Compare-Object $actual $expected" in VERIFY
    assert "ReparsePoint" in VERIFY and "Get-FileHash" in VERIFY
    assert "--all-strategies" in VERIFY and "$LASTEXITCODE" in VERIFY
    assert not any(
        x in VERIFY
        for x in ["ExecutionPolicy", "Bypass", "Set-MpPreference", "Start-Process"]
    )
