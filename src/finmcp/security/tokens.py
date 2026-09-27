from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet

from finmcp.config import settings


def _key_path() -> Path:
    return settings.data_dir / "fernet.key"


def _load_or_create_key() -> bytes:
    """Clave Fernet: de la env si está, si no se genera y persiste con chmod 600."""
    if settings.finmcp_encryption_key:
        return settings.finmcp_encryption_key.encode()
    kp = _key_path()
    if kp.exists():
        return kp.read_bytes()
    key = Fernet.generate_key()
    kp.write_bytes(key)
    os.chmod(kp, 0o600)
    return key


def _fernet() -> Fernet:
    return Fernet(_load_or_create_key())


# --- Almacén cifrado genérico (vínculos por banco) ---------------------------

def _named_path(name: str) -> Path:
    return settings.data_dir / f"{name}.enc"


def save_secret(name: str, data: dict) -> None:
    """Guarda un dict cifrado en data/<name>.enc (chmod 600)."""
    blob = _fernet().encrypt(json.dumps(data).encode())
    p = _named_path(name)
    p.write_bytes(blob)
    os.chmod(p, 0o600)


def load_secret(name: str) -> dict | None:
    p = _named_path(name)
    if not p.exists():
        return None
    return json.loads(_fernet().decrypt(p.read_bytes()))
