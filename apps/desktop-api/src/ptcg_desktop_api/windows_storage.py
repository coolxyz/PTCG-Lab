"""Windows-only private NTFS journal storage. Imports Win32 bindings lazily.

No ACL repair, network shares, reparse traversal, shared writer, machine-wide
DPAPI or plaintext fallback. Native Windows acceptance is required separately.
"""

from hashlib import sha256
import os
from pathlib import Path
import re
import sys
import uuid

from ptcg_online_client.contracts import ClientError
from ptcg_online_client.vault import MAX_VAULT, Vault

MAGIC = b"PTCG-DPAPI-v1\x00"
MAX_ENCODED = MAX_VAULT + 16384
UI_FORBIDDEN = 1
FILE_ALL_ACCESS = 0x1F01FF
SYSTEM_SID = "S-1-5-18"
# These SDK flags are accepted by Win32 but absent from pywin32 312 win32con.
FILE_READ_ATTRIBUTES = 0x0080
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
MOVEFILE_WRITE_THROUGH = 0x00000008


def private_acl(owner, entries, user, protected):
    """Reject null/unprotected/foreign ACLs; require effective owner full control."""
    if owner != user or not protected or not entries:
        raise ClientError("UNSAFE_STORAGE")
    owner_full = False
    for kind, flags, mask, sid in entries:
        if kind != 0 or sid not in {user, SYSTEM_SID}:
            raise ClientError("UNSAFE_STORAGE")
        if (
            sid == user
            and not flags & 0x08
            and mask & FILE_ALL_ACCESS == FILE_ALL_ACCESS
        ):
            owner_full = True
    if not owner_full:
        raise ClientError("UNSAFE_STORAGE")


class DPAPICipher:
    def __init__(self, root, server, *, existing=None, crypt=None, **_):
        if existing is not None and not existing.startswith(MAGIC):
            raise ClientError("NATIVE_IDENTITY_IMPORT_REQUIRED")
        self.entropy = sha256(
            (server.key + "\0" + os.path.normcase(str(Path(root).resolve()))).encode()
        ).digest()
        if crypt is None:
            if sys.platform != "win32":
                raise ClientError("CREDENTIAL_BACKEND_UNAVAILABLE")
            try:
                import win32crypt

                crypt = win32crypt
            except ImportError:
                raise ClientError("CREDENTIAL_BACKEND_UNAVAILABLE") from None
        self.crypt = crypt

    def encode(self, raw):
        if len(raw) > MAX_VAULT:
            raise ClientError("STORAGE_CAPACITY")
        try:
            result = MAGIC + self.crypt.CryptProtectData(
                raw, "PTCG Lab native journal", self.entropy, None, None, UI_FORBIDDEN
            )
            if len(result) > MAX_ENCODED:
                raise ValueError()
            return result
        except Exception:
            raise ClientError("STORAGE_UNAVAILABLE") from None

    def decode(self, raw):
        try:
            if not raw.startswith(MAGIC) or len(raw) > MAX_ENCODED:
                raise ValueError()
            _, result = self.crypt.CryptUnprotectData(
                raw[len(MAGIC) :], self.entropy, None, None, UI_FORBIDDEN
            )
            if len(result) > MAX_VAULT:
                raise ValueError()
            return result
        except Exception:
            raise ClientError("INVALID_STORAGE") from None


class WindowsDirectory:
    """Pin directory ancestors without delete sharing and validate the target ACL."""

    def __init__(self, root):
        if sys.platform != "win32":
            raise ClientError("CREDENTIAL_BACKEND_UNAVAILABLE")
        try:
            import pywintypes
            import win32api
            import win32con
            import win32file
            import win32security
        except ImportError:
            raise ClientError("CREDENTIAL_BACKEND_UNAVAILABLE") from None
        self.error = pywintypes.error
        self.file, self.con, self.security = win32file, win32con, win32security
        self.handles = []
        path = Path(root)
        if (
            not path.is_absolute()
            or str(path).startswith(("\\\\", "//"))
            or any(
                p in {".", ".."} or p.rstrip(". ") != p or ":" in p
                for p in path.parts[1:]
            )
        ):
            raise ClientError("UNSAFE_STORAGE")
        self.directory = path
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        try:
            self.user = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
        finally:
            token.Close()
        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"O:{self.user}D:P(A;OICI;FA;;;{self.user})(A;OICI;FA;;;SY)", 1
        )
        self.attributes = pywintypes.SECURITY_ATTRIBUTES()
        self.attributes.SECURITY_DESCRIPTOR = descriptor
        self.attributes.bInheritHandle = False
        try:
            volume = win32api.GetVolumeInformation(path.anchor)
            if (
                win32file.GetDriveType(path.anchor) != 3
                or not volume[3] & 8
                or volume[4].upper() != "NTFS"
            ):
                raise ClientError("UNSAFE_STORAGE")
            # Pin from the drive down, including existing ancestors. Never
            # normalize a junction away before inspecting its actual handle.
            for part in reversed((path, *path.parents)):
                try:
                    handle = self._directory_handle(part)
                except self.error as error:
                    if error.winerror not in {2, 3}:
                        raise
                    win32file.CreateDirectory(self.full(part), self.attributes)
                    handle = self._directory_handle(part)
                self.handles.append(handle)
                self.inspect(handle, directory=True, acl=part == path)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def full(path):
        return "\\\\?\\" + str(path)

    def _directory_handle(self, path):
        c = self.con
        return self.file.CreateFile(
            self.full(path),
            FILE_READ_ATTRIBUTES | c.READ_CONTROL,
            c.FILE_SHARE_READ | c.FILE_SHARE_WRITE,
            None,
            c.OPEN_EXISTING,
            c.FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )

    def inspect(self, handle, *, directory=False, acl=True):
        info = self.file.GetFileInformationByHandle(handle)
        attrs = info[0]
        if (
            attrs & self.con.FILE_ATTRIBUTE_REPARSE_POINT
            or bool(attrs & self.con.FILE_ATTRIBUTE_DIRECTORY) != directory
            or (not directory and info[7] != 1)
        ):
            raise ClientError("UNSAFE_STORAGE")
        if acl:
            s = self.security
            sd = s.GetSecurityInfo(
                handle,
                s.SE_FILE_OBJECT,
                s.OWNER_SECURITY_INFORMATION | s.DACL_SECURITY_INFORMATION,
            )
            dacl = sd.GetSecurityDescriptorDacl()
            entries = []
            if dacl is not None:
                for index in range(dacl.GetAceCount()):
                    ace = dacl.GetAce(index)
                    if len(ace) != 3:
                        raise ClientError("UNSAFE_STORAGE")
                    entries.append(
                        (ace[0][0], ace[0][1], ace[1], s.ConvertSidToStringSid(ace[2]))
                    )
            private_acl(
                s.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner()),
                entries,
                self.user,
                bool(sd.GetSecurityDescriptorControl()[0] & 0x1000),
            )
        return (info[5] << 32) | info[6]

    def open_file(self, path, access, disposition):
        return self.file.CreateFile(
            self.full(path),
            access | self.con.READ_CONTROL,
            0,
            self.attributes,
            disposition,
            self.con.FILE_ATTRIBUTE_NORMAL
            | FILE_FLAG_OPEN_REPARSE_POINT
            | self.con.FILE_FLAG_WRITE_THROUGH,
            None,
        )

    def close(self):
        for handle in reversed(self.handles):
            handle.Close()
        self.handles.clear()


class WindowsFiles:
    def __init__(self, root, key):
        if not re.fullmatch(r"[a-f0-9]{64}|native", key):
            raise ClientError("UNSAFE_STORAGE")
        self.directory = Path(root)
        self.path = self.directory / (key + ".json")
        self.owner = None
        self.lock = None
        try:
            self.owner = WindowsDirectory(root)
            c = self.owner.con
            self.lock = self.owner.open_file(
                self.directory / (key + ".lock"),
                c.GENERIC_READ | c.GENERIC_WRITE,
                c.OPEN_ALWAYS,
            )
            self.owner.inspect(self.lock)
        except BaseException as error:
            self.close()
            if getattr(error, "winerror", None) in {32, 33}:
                raise ClientError("PROFILE_IN_USE") from None
            if isinstance(error, ClientError) or not isinstance(error, Exception):
                raise
            raise ClientError("UNSAFE_STORAGE") from None

    def read(self, limit):
        try:
            handle = self.owner.open_file(
                self.path, self.owner.con.GENERIC_READ, self.owner.con.OPEN_EXISTING
            )
        except self.owner.error as error:
            if error.winerror == 2:
                return None
            raise ClientError("UNSAFE_STORAGE") from None
        try:
            size = self.owner.inspect(handle)
            if size > limit:
                raise ClientError("UNSAFE_STORAGE")
            raw = bytearray()
            while len(raw) < size:
                _, chunk = self.owner.file.ReadFile(handle, min(65536, size - len(raw)))
                if not chunk:
                    raise ClientError("INVALID_STORAGE")
                raw.extend(chunk)
            return bytes(raw)
        finally:
            handle.Close()

    def replace(self, data):
        self.read(MAX_ENCODED)  # Validate any existing target before replacing it.
        temp = self.directory / ("." + uuid.uuid4().hex)
        try:
            c = self.owner.con
            handle = self.owner.open_file(temp, c.GENERIC_WRITE, c.CREATE_NEW)
            try:
                self.owner.inspect(handle)
                offset = 0
                while offset < len(data):
                    _, count = self.owner.file.WriteFile(
                        handle, data[offset : offset + 65536]
                    )
                    if count <= 0:
                        raise ClientError("STORAGE_UNAVAILABLE")
                    offset += count
                self.owner.file.FlushFileBuffers(handle)
            finally:
                handle.Close()
            self.owner.file.MoveFileEx(
                self.owner.full(temp),
                self.owner.full(self.path),
                c.MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        except Exception:
            raise ClientError("STORAGE_UNAVAILABLE") from None
        finally:
            try:
                self.owner.file.DeleteFile(self.owner.full(temp))
            except self.owner.error:
                pass

    def close(self):
        if self.lock is not None:
            self.lock.Close()
            self.lock = None
        if self.owner is not None:
            self.owner.close()


def windows_vault(
    root, server, *, initial_state=None, require_existing=False, require_new=False
):
    return Vault(
        root,
        server,
        storage_factory=WindowsFiles,
        initial_state=initial_state,
        require_existing=require_existing,
        require_new=require_new,
        max_encoded_bytes=MAX_ENCODED,
        codec_factory=lambda **options: DPAPICipher(root, server, **options),
    )
