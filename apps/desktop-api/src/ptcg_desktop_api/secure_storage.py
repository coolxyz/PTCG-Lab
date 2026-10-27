"""macOS native vault: a Keychain key protects the whole durable client journal.

Only native entrypoints select this backend. The browser/POSIX backend retains
its explicit contract; there is no silent plaintext fallback or automatic import.
"""

import base64
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import sys

from ptcg_online_client.client import Client
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.vault import Vault, MAX_VAULT

MAGIC = b"PTCG-VAULT-v1\x00"
SERVICE = "org.ptcglab.desktop.preview.vault"


class KeychainCipher:
    def __init__(self, root, server, *, create, existing=None, keychain=None):
        if existing is not None and not existing.startswith(MAGIC):
            raise ClientError("NATIVE_IDENTITY_IMPORT_REQUIRED")
        self.account = sha256(
            (
                str(os.getuid()) + "\n" + str(Path(root).resolve()) + "\n" + server.key
            ).encode()
        ).hexdigest()
        self.aad = MAGIC + server.key.encode() + self.account.encode()
        try:
            if keychain is None:
                if sys.platform != "darwin":
                    raise ClientError("CREDENTIAL_BACKEND_UNAVAILABLE")
                # Select the OS backend explicitly, ignoring user/plugin fallback settings.
                from keyring.backends.macOS import Keyring

                keychain = Keyring()
            encoded = keychain.get_password(SERVICE, self.account)
            if encoded is None:
                if not create:
                    raise ClientError("CREDENTIAL_KEY_UNAVAILABLE")
                encoded = base64.b64encode(os.urandom(32)).decode("ascii")
                keychain.set_password(SERVICE, self.account, encoded)
                if keychain.get_password(SERVICE, self.account) != encoded:
                    raise ClientError("CREDENTIAL_BACKEND_UNAVAILABLE")
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32:
                raise ClientError("CREDENTIAL_KEY_UNAVAILABLE")
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            self.cipher = AESGCM(key)
        except ClientError:
            raise
        except Exception:
            raise ClientError("CREDENTIAL_BACKEND_UNAVAILABLE") from None

    def encode(self, raw):
        if len(raw) > MAX_VAULT:
            raise ClientError("STORAGE_CAPACITY")
        try:
            nonce = os.urandom(12)
            return MAGIC + nonce + self.cipher.encrypt(nonce, raw, self.aad)
        except Exception:
            raise ClientError("STORAGE_UNAVAILABLE") from None

    def decode(self, raw):
        try:
            if not raw.startswith(MAGIC) or len(raw) > MAX_VAULT + 64:
                raise ValueError()
            payload = raw[len(MAGIC) :]
            return self.cipher.decrypt(payload[:12], payload[12:], self.aad)
        except Exception:
            raise ClientError("INVALID_STORAGE") from None


def native_vault(root, server, *, keychain=None, initial_state=None):
    return Vault(
        root,
        server,
        initial_state=initial_state,
        codec_factory=lambda **options: KeychainCipher(
            root, server, keychain=keychain, **options
        ),
    )


def native_client(server, storage):
    return Client(server, storage, vault_factory=native_vault)


def import_identity(source, destination, server, *, keychain=None):
    """Explicit trusted operator call; source stays intact, destination must be new."""
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if not (source / (server.key + ".json")).is_file():
        raise ClientError("IDENTITY_SOURCE_MISSING")
    if (destination / (server.key + ".json")).exists():
        raise ClientError("IDENTITY_DESTINATION_EXISTS")
    original = Vault(source, server)
    try:
        with original.transaction() as state:
            imported = deepcopy(state)
        target = native_vault(
            destination, server, keychain=keychain, initial_state=imported
        )
        try:
            with target.transaction() as result:
                if result != imported:
                    raise ClientError("IDENTITY_IMPORT_FAILED")
        finally:
            target.close()
    finally:
        original.close()
