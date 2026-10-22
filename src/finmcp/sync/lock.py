"""Exclusión mutua entre sincronizaciones (CLI, MCP, cron y panel web).

Dos `sync` a la vez se pisarían en la BD y gastarían accesos del banco (PSD2 limita
los de cada consentimiento), así que `run_sync` toma un bloqueo de fichero
(`data/sync.lock`, `flock`) y falla de inmediato si otro proceso ya lo tiene. El
sistema operativo lo libera solo, aunque el proceso muera.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from finmcp.config import settings

try:  # sin `fcntl` (Windows) no hay bloqueo: se conserva el comportamiento anterior
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

# Código de salida de `finmcp sync` cuando ya hay otra sincronización en curso (EX_TEMPFAIL).
EXIT_BUSY = 75


class SyncInProgress(RuntimeError):
    """Ya hay otra sincronización en curso."""

    def __init__(self) -> None:
        super().__init__("Ya hay otra sincronización en curso; inténtelo en unos minutos.")


def lock_path() -> Path:
    return settings.data_dir / "sync.lock"


@contextmanager
def sync_lock() -> Iterator[None]:
    """Reserva en exclusiva la sincronización; lanza `SyncInProgress` si está ocupada."""
    if fcntl is None:  # pragma: no cover
        yield
        return
    fd = os.open(lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncInProgress() from None
        yield
    finally:
        os.close(fd)  # cerrar el descriptor libera el bloqueo
