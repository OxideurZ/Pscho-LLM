from __future__ import annotations

import ctypes
import os
import secrets
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


class SecretStoreError(RuntimeError):
    code = "SECRET_STORE_UNAVAILABLE"


class SecretMissingError(SecretStoreError):
    code = "SECRET_MISSING"


class SecretCorruptError(SecretStoreError):
    code = "SECRET_CORRUPT"


class SecretStore(Protocol):
    def get(self, name: str) -> bytes: ...

    def create(self, name: str, value: bytes | None = None) -> bytes: ...

    def delete(self, name: str) -> None: ...

    def health(self) -> dict[str, object]: ...


class WindowsDpapiSecretStore:
    """Small user-bound secret store backed by Windows DPAPI.

    The files contain only DPAPI protected blobs. Raw key material is generated in memory and is
    never serialized to configuration, instance metadata, command arguments or logs.
    """

    _prefix = "psych-local-"
    _suffix = ".protected"

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        if sys.platform != "win32":
            raise SecretStoreError("Windows DPAPI is unavailable on this platform")
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_api()

    def get(self, name: str) -> bytes:
        path = self._path(name)
        if not path.is_file():
            raise SecretMissingError(f"Protected secret is missing: {name}")
        try:
            return self._unprotect(path.read_bytes())
        except SecretStoreError:
            raise
        except (OSError, ValueError, ctypes.ArgumentError) as error:
            raise SecretCorruptError(f"Protected secret cannot be opened: {name}") from error

    def create(self, name: str, value: bytes | None = None) -> bytes:
        path = self._path(name)
        if path.exists():
            return self.get(name)
        raw = value if value is not None else secrets.token_bytes(32)
        if not raw:
            raise ValueError("Secret value cannot be empty")
        protected = self._protect(raw)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(protected)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise SecretStoreError(f"Protected secret cannot be stored: {name}") from error
        return raw

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink(missing_ok=True)
        except OSError as error:
            raise SecretStoreError(f"Protected secret cannot be deleted: {name}") from error

    def health(self) -> dict[str, object]:
        return {
            "status": "ready",
            "backend": "windows-dpapi-user-scope",
            "directory": str(self.directory),
        }

    def _path(self, name: str) -> Path:
        if not name or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in name
        ):
            raise ValueError("Secret name contains invalid characters")
        return self.directory / f"{self._prefix}{name}{self._suffix}"

    def _configure_api(self) -> None:
        blob_type = ctypes.POINTER(_WinDataBlob)
        self._crypt32.CryptProtectData.argtypes = [
            blob_type,
            wintypes.LPCWSTR,
            blob_type,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            blob_type,
        ]
        self._crypt32.CryptProtectData.restype = ctypes.c_int
        self._crypt32.CryptUnprotectData.argtypes = [
            blob_type,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_type,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            blob_type,
        ]
        self._crypt32.CryptUnprotectData.restype = ctypes.c_int
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def _protect(self, value: bytes) -> bytes:
        source, _source_buffer = _make_blob(value)
        destination = _WinDataBlob()
        if not self._crypt32.CryptProtectData(
            ctypes.byref(source),
            "psych-local secret",
            None,
            None,
            None,
            0,
            ctypes.byref(destination),
        ):
            raise SecretStoreError("Windows DPAPI could not protect the secret")
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            self._kernel32.LocalFree(destination.pbData)

    def _unprotect(self, value: bytes) -> bytes:
        source, _source_buffer = _make_blob(value)
        destination = _WinDataBlob()
        description = ctypes.c_wchar_p()
        if not self._crypt32.CryptUnprotectData(
            ctypes.byref(source),
            ctypes.byref(description),
            None,
            None,
            None,
            0,
            ctypes.byref(destination),
        ):
            raise SecretCorruptError("Windows DPAPI rejected the protected secret")
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            self._kernel32.LocalFree(destination.pbData)


class _WinDataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _make_blob(value: bytes) -> tuple[_WinDataBlob, object]:
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return _WinDataBlob(len(value), buffer), buffer
