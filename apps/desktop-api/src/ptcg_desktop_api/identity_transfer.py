"""Explicit encrypted handoff between OS vaults. Never plaintext or an HTTP API."""

from copy import deepcopy
import json
import os

from ptcg_online_client.contracts import ClientError, unpack
from ptcg_online_client.vault import MAX_VAULT, Vault
from .sealed_file import access, read_encrypted
from .secure_storage import native_vault

MAGIC = b"PTCG-IDENTITY-v1\x00"
LIMIT = MAX_VAULT + 4096


def key(password, salt):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ClientError("IDENTITY_PASSWORD_LENGTH")
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(
        password.encode("utf-8")
    )


def seal(state, server, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not server.origin.startswith("https://"):
        raise ClientError("INVALID_SERVER")
    raw = json.dumps(
        {"schema": 1, "profile": server.key, "state": state},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if len(raw) > MAX_VAULT:
        raise ClientError("STORAGE_CAPACITY")
    salt, nonce = os.urandom(16), os.urandom(12)
    return (
        MAGIC
        + salt
        + nonce
        + AESGCM(key(password, salt)).encrypt(
            nonce, raw, MAGIC + server.key.encode("ascii")
        )
    )


def unseal(raw, server, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not server.origin.startswith("https://"):
        raise ClientError("INVALID_SERVER")
    if not raw.startswith(MAGIC) or not len(MAGIC) + 44 <= len(raw) <= LIMIT:
        raise ClientError("INVALID_IDENTITY_BUNDLE")
    payload = raw[len(MAGIC) :]
    derived = key(password, payload[:16])
    try:
        plain = AESGCM(derived).decrypt(
            payload[16:28], payload[28:], MAGIC + server.key.encode("ascii")
        )
        if len(plain) > MAX_VAULT:
            raise ValueError()
        result = unpack(plain)
        if (
            set(result) != {"schema", "profile", "state"}
            or result["schema"] != 1
            or result["profile"] != server.key
            or not isinstance(result["state"], dict)
        ):
            raise ValueError()
        return result["state"]
    except Exception:
        raise ClientError("INVALID_IDENTITY_BUNDLE") from None


def export_identity(
    source, output, server, password, *, source_kind="browser", keychain=None
):
    if source_kind == "browser":
        owner = Vault(source, server, require_existing=True)
    elif source_kind == "native":
        owner = native_vault(source, server, keychain=keychain, require_existing=True)
    else:
        raise ClientError("IDENTITY_SOURCE_KIND")
    try:
        with owner.transaction() as state:
            encrypted = seal(state, server, password)
        access(output, data=encrypted, limit=LIMIT)
    finally:
        owner.close()


def import_bundle(bundle, destination, server, password, *, keychain=None):
    state = unseal(read_encrypted(bundle, limit=LIMIT), server, password)
    owner = native_vault(
        destination,
        server,
        keychain=keychain,
        initial_state=deepcopy(state),
        require_new=True,
    )
    try:
        with owner.transaction() as restored:
            if restored != state:
                raise ClientError("IDENTITY_IMPORT_FAILED")
    finally:
        owner.close()
