"""Stockage des clés API.

Windows : chiffrement DPAPI (lié au compte Windows de l'utilisateur).
Autres systèmes : fichier réservé à l'utilisateur (chmod 600), non chiffré.
Les clés ne sont jamais renvoyées à l'interface.
"""
import base64
import json
import os
import sys
import threading
from pathlib import Path

ENV_NAMES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama": "OLLAMA_API_KEY",
}

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _dpapi(data: bytes, protect: bool) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        dst = _Blob()
        crypt32 = ctypes.windll.crypt32
        if protect:
            ok = crypt32.CryptProtectData(ctypes.byref(src), "Jarvis", None, None, None, 0, ctypes.byref(dst))
        else:
            ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst))
        if not ok:
            raise OSError("DPAPI a refusé l'opération")
        try:
            return ctypes.string_at(dst.pbData, dst.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(dst.pbData)

    def _enc(b: bytes) -> bytes:
        return _dpapi(b, True)

    def _dec(b: bytes) -> bytes:
        return _dpapi(b, False)

    SCHEME = "dpapi"
else:
    def _enc(b: bytes) -> bytes:
        return b

    def _dec(b: bytes) -> bytes:
        return b

    SCHEME = "fichier-prive"


class SecretStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8")).get("keys", {})
            except (ValueError, OSError):
                self._data = {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"scheme": SCHEME, "keys": self._data}), encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def set(self, name: str, value: str):
        with self._lock:
            value = (value or "").strip()
            if value:
                self._data[name] = base64.b64encode(_enc(value.encode("utf-8"))).decode("ascii")
            else:
                self._data.pop(name, None)
            self._save()

    def get(self, name: str) -> str:
        with self._lock:
            raw = self._data.get(name)
        if raw:
            try:
                return _dec(base64.b64decode(raw)).decode("utf-8")
            except (OSError, ValueError):
                return ""
        return os.environ.get(ENV_NAMES.get(name, ""), "")

    def has(self, name: str) -> bool:
        return bool(self.get(name))

    def reload(self):
        with self._lock:
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8")).get("keys", {})
            except (ValueError, OSError):
                self._data = {}
