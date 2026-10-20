"""PIN del panel web: huella del PIN, sesiones firmadas y freno a los intentos fallidos.

- El PIN no se guarda nunca: solo su huella PBKDF2-SHA256 con sal, en `.env`
  (`FINMCP_WEB_PIN_HASH`), que se fija con `finmcp web-pin`.
- Tras acertar, el navegador recibe una cookie de sesión firmada (HMAC) que
  caduca a los 30 días. Cambiar el PIN invalida todas las sesiones.
- Un PIN corto se adivina probando: tras 5 fallos seguidos, cada nuevo fallo
  bloquea el acceso un tiempo que se duplica (30 s, 1 min, 2 min... hasta 1 h).
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path

PIN_ENV = "FINMCP_WEB_PIN_HASH"
SESSION_COOKIE = "finmcp_web"
SESSION_TTL = 30 * 24 * 3600
ALGO = "pbkdf2_sha256"
ITERATIONS = 200_000
_PIN_RE = re.compile(r"^\d{4,12}$")


# --- huella del PIN ------------------------------------------------------------


def valid_pin(pin: object) -> bool:
    return isinstance(pin, str) and bool(_PIN_RE.match(pin))


def hash_pin(pin: str, *, iterations: int = ITERATIONS, salt: bytes | None = None) -> str:
    """Huella `pbkdf2_sha256:<iteraciones>:<sal>:<hash>` (sin «$», que .env interpretaría)."""
    if not valid_pin(pin):
        raise ValueError("El PIN debe tener entre 4 y 12 cifras.")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
    return f"{ALGO}:{iterations}:{salt.hex()}:{digest.hex()}"


def _parse(stored: str) -> tuple[int, bytes, bytes] | None:
    try:
        algo, iterations, salt, digest = stored.split(":")
        if algo != ALGO:
            return None
        parsed = int(iterations), bytes.fromhex(salt), bytes.fromhex(digest)
    except (AttributeError, ValueError):
        return None
    return parsed if parsed[0] > 0 and parsed[1] and parsed[2] else None


def valid_hash(stored: str) -> bool:
    return _parse(stored) is not None


def verify_pin(pin: str, stored: str) -> bool:
    parsed = _parse(stored)
    if parsed is None or not valid_pin(pin):
        return False
    iterations, salt, digest = parsed
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations), digest)


# --- sesiones ------------------------------------------------------------------


class Sessions:
    """Tokens `<caducidad>.<firma>` firmados con una clave derivada del secreto y de la huella del PIN."""

    def __init__(self, secret: bytes, pin_hash: str, *, ttl: int = SESSION_TTL,
                 clock: Callable[[], float] = time.time) -> None:
        self._key = hmac.new(secret, pin_hash.encode(), hashlib.sha256).digest()
        self.ttl = ttl
        self.clock = clock

    def _sign(self, expires: int) -> str:
        return hmac.new(self._key, str(expires).encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        expires = int(self.clock()) + self.ttl
        return f"{expires}.{self._sign(expires)}"

    def valid(self, token: str | None) -> bool:
        if not isinstance(token, str):
            return False
        try:
            expires_s, signature = token.split(".", 1)
            expires = int(expires_s)
        except (AttributeError, ValueError):
            return False
        return expires > self.clock() and hmac.compare_digest(signature, self._sign(expires))


def load_secret(path: Path) -> bytes:
    """Secreto de firma de sesiones, persistente (las sesiones sobreviven a un reinicio)."""
    path = Path(path)
    if path.exists():
        data = path.read_bytes()
        if len(data) >= 32:
            return data
    data = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)
    return data


# --- freno a los intentos fallidos ---------------------------------------------


class Throttle:
    """Freno global: tras `free` fallos seguidos, cada fallo bloquea `base`·2^k s (máximo `cap`)."""

    def __init__(self, *, free: int = 5, base: int = 30, cap: int = 3600,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.free, self.base, self.cap, self.clock = free, base, cap, clock
        self.failures = 0
        self.until = 0.0

    def retry_after(self) -> int:
        """Segundos que faltan para poder intentarlo de nuevo (0 si no hay bloqueo)."""
        return max(0, math.ceil(self.until - self.clock()))

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.free:
            self.until = self.clock() + min(self.cap, self.base * 2 ** (self.failures - self.free))

    def success(self) -> None:
        self.failures = 0
        self.until = 0.0


# --- .env ----------------------------------------------------------------------


def write_env_value(path: Path, key: str, value: str | None) -> None:
    """Fija (o, con None, elimina) `KEY=valor` en un .env, conservando el resto de líneas."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=")
    out = [line for line in lines if not pattern.match(line)]
    if value is not None:
        out.append(f"{key}={value}")
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    path.write_text("\n".join(out) + "\n" if out else "", encoding="utf-8")
