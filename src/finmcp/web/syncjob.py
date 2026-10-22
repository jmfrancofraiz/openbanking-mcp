"""Sincronización lanzada desde el panel (gesto de tirar hacia abajo).

El panel sigue abriendo la BD en solo lectura: la sincronización se ejecuta en un
proceso aparte (`python -m finmcp.cli sync --from <hoy − 30 días>`, el mismo rango
que usa el cron diario), que es quien escribe. Aquí solo se lanza, se vigila y se
resume su resultado leyendo la tabla `sync_runs`.

- Una sola a la vez: mientras haya una en marcha, otra petición se une a ella.
- Freno: si la última sincronización (de cualquier origen: cron, CLI o panel)
  terminó hace menos de `cooldown` segundos no se lanza otra, para no gastar los
  accesos que PSD2 permite a cada banco (el 429 es habitual). El panel recarga
  entonces los datos tal cual están.
- Si el cron u otro proceso tiene ya el bloqueo de `sync/lock.py`, el proceso hijo
  sale con `EXIT_BUSY` y el resultado es «busy»: no se pisa nada.
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func
from starlette.concurrency import run_in_threadpool

from finmcp.analytics.ledger import iso_utc, short_error
from finmcp.db import models
from finmcp.sync.lock import EXIT_BUSY

SYNC_DAYS = 30  # ventana explícita: la del banco por defecto es corta y pierde movimientos tardíos
DEFAULT_COOLDOWN = 300  # s entre sincronizaciones
DEFAULT_TIMEOUT = 300  # s máximos de una sincronización (la de 4 bancos dura ~30 s)
_STRIP_ENV = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")  # como el wrapper `~/.local/bin/finmcp`


def default_command(from_date: str) -> list[str]:
    """Comando del proceso hijo: el CLI del propio entorno Python del panel."""
    return [sys.executable, "-m", "finmcp.cli", "sync", "--from", from_date]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: datetime) -> datetime:
    """Las marcas de la BD son UTC sin zona."""
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _last_line(text: bytes | None) -> str | None:
    lines = [ln.strip() for ln in (text or b"").decode("utf-8", "replace").splitlines() if ln.strip()]
    if not lines:
        return None
    return lines[-1][:160] + ("…" if len(lines[-1]) > 160 else "")


class SyncJob:
    """Estado y ejecución de la sincronización lanzada desde el panel (una por proceso)."""

    def __init__(
        self,
        session_factory: Callable,
        *,
        command: Callable[[str], list[str]] = default_command,
        cwd: Path | None = None,
        cooldown: int = DEFAULT_COOLDOWN,
        timeout: float = DEFAULT_TIMEOUT,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._command = command
        self._cwd = cwd
        self.cooldown = max(0, int(cooldown))
        self.timeout = timeout
        self._today = today
        self._now = now
        self._start_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.state = "idle"  # idle | running | finished
        self.outcome: str | None = None  # ok | partial | error | busy | timeout
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.banks: list[dict] = []
        self.error: str | None = None

    # --- lectura de la BD (de solo lectura; se llama desde un hilo) ---------------

    def _last_run_id(self) -> int:
        with self._session_factory() as session:
            return session.query(func.max(models.SyncRun.id)).scalar() or 0

    def _last_finished(self) -> datetime | None:
        with self._session_factory() as session:
            ts = session.query(func.max(models.SyncRun.finished_at)).scalar()
        return _aware(ts) if ts else None

    def _runs_after(self, run_id: int) -> list[dict]:
        with self._session_factory() as session:
            runs = (session.query(models.SyncRun).filter(models.SyncRun.id > run_id)
                    .order_by(models.SyncRun.id).all())
            return [{
                "bank": r.bank_id,
                "status": r.status,
                "new": r.tx_added or 0,
                "error": short_error(r.detail) if r.status == "error" else None,
            } for r in runs]

    # --- freno --------------------------------------------------------------------

    async def _cooldown(self) -> tuple[int, datetime | None]:
        """(segundos que faltan para poder sincronizar, última sincronización terminada)."""
        last = await run_in_threadpool(self._last_finished)
        if self.finished_at and (last is None or self.finished_at > last):
            last = self.finished_at
        if last is None or not self.cooldown:
            return 0, last
        left = self.cooldown - (self._now() - last).total_seconds()
        return max(0, math.ceil(left)), last

    # --- API ----------------------------------------------------------------------

    async def snapshot(self) -> dict:
        retry_after, last = await self._cooldown()
        return {
            "state": self.state,
            "outcome": self.outcome,
            "started_at": iso_utc(self.started_at),
            "finished_at": iso_utc(self.finished_at),
            "banks": list(self.banks),
            "new_transactions": sum(b["new"] for b in self.banks),
            "error": self.error,
            "last_sync_at": iso_utc(last),
            "retry_after": 0 if self.state == "running" else retry_after,
        }

    async def start(self) -> tuple[dict, bool]:
        """Lanza una sincronización si procede. Devuelve (estado, ¿se ha lanzado?)."""
        async with self._start_lock:
            if self.state == "running":
                return await self.snapshot(), False
            retry_after, _ = await self._cooldown()
            if retry_after:
                return await self.snapshot(), False
            self.state, self.outcome, self.error, self.banks = "running", None, None, []
            self.started_at, self.finished_at = self._now(), None
            self._task = asyncio.create_task(self._run())  # referencia: que no la recoja el GC
        return await self.snapshot(), True

    # --- ejecución ----------------------------------------------------------------

    async def _execute(self) -> tuple[int | None, bytes | None, bool]:
        """Lanza el hijo y espera. Devuelve (código de salida, stderr, ¿agotó el tiempo?)."""
        command = self._command((self._today() - timedelta(days=SYNC_DAYS)).isoformat())
        env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
        proc = await asyncio.create_subprocess_exec(
            *command, cwd=self._cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), self.timeout)
            return proc.returncode, err, False
        except asyncio.TimeoutError:
            return None, None, True
        finally:
            if proc.returncode is None:  # tiempo agotado o panel parándose: no dejar el hijo suelto
                proc.kill()
                await proc.wait()

    async def _run(self) -> None:
        outcome, error, banks = "error", None, []
        try:
            before = await run_in_threadpool(self._last_run_id)
            code, stderr, timed_out = await self._execute()
            banks = await run_in_threadpool(self._runs_after, before)
            if timed_out:
                outcome, error = "timeout", "La sincronización ha tardado demasiado y se ha cancelado."
            elif code == EXIT_BUSY:
                outcome, error = "busy", "Ya hay otra sincronización en curso."
            elif code != 0 and not any(b["status"] == "error" for b in banks):
                outcome = "error"
                error = _last_line(stderr) or f"La sincronización terminó con código {code}."
            else:
                bad = [b for b in banks if b["status"] != "ok"]
                outcome = "ok" if not bad else "error" if len(bad) == len(banks) else "partial"
                error = next((b["error"] for b in bad if b["error"]), None)
        except Exception as exc:  # noqa: BLE001 -- el estado nunca debe quedar en «running»
            outcome, error = "error", f"No se pudo lanzar la sincronización: {exc}"[:200]
        finally:
            self.banks, self.outcome, self.error = banks, outcome, error
            self.finished_at = self._now()
            self.state = "finished"
