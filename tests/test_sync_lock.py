from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from finmcp import cli
from finmcp.config import BankConfig
from finmcp.sync import lock, service


def test_el_bloqueo_se_libera_al_salir():
    with lock.sync_lock():
        pass
    with lock.sync_lock():  # si no se hubiera liberado, fallaría
        pass


def test_segundo_bloqueo_falla_mientras_el_primero_esta_tomado():
    with lock.sync_lock():
        with pytest.raises(lock.SyncInProgress, match="en curso"):
            with lock.sync_lock():
                pytest.fail("no debería entrar")


def test_se_libera_aunque_el_cuerpo_falle():
    with pytest.raises(RuntimeError):
        with lock.sync_lock():
            raise RuntimeError("boom")
    with lock.sync_lock():
        pass


def _preparar_run_sync(monkeypatch, Session, llamadas: list):
    monkeypatch.setattr(service, "init_db", lambda: None)
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(
        service, "settings",
        SimpleNamespace(banks=[BankConfig(id="default", aspsp_name="CaixaBank", country="ES")]),
    )

    class Cliente:
        def get_accounts(self):
            llamadas.append("get_accounts")
            return []

    monkeypatch.setattr(service, "get_provider", lambda bank: Cliente())


def test_run_sync_no_toca_el_banco_si_hay_otra_sincronizacion(monkeypatch, Session):
    llamadas: list = []
    _preparar_run_sync(monkeypatch, Session, llamadas)

    with lock.sync_lock():
        with pytest.raises(lock.SyncInProgress):
            service.run_sync()

    assert llamadas == []  # ni siquiera se abrió el cliente del banco
    assert service.run_sync()[0].status == "ok"  # y libre otra vez, funciona


def test_cli_sync_ocupado_sale_con_codigo_75(monkeypatch, Session):
    _preparar_run_sync(monkeypatch, Session, [])

    with lock.sync_lock():
        result = CliRunner().invoke(cli.app, ["sync"])

    assert result.exit_code == lock.EXIT_BUSY == 75
    assert "en curso" in result.output


def test_el_bloqueo_funciona_entre_procesos_y_lo_libera_el_sistema():
    """El caso real: el cron (otro proceso) tiene la sincronización y el panel debe esperar."""
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, os, sys\n"
         f"fd = os.open({str(lock.lock_path())!r}, os.O_RDWR | os.O_CREAT, 0o600)\n"
         "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
         "print('LOCKED', flush=True)\n"
         "sys.stdin.readline()\n"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "LOCKED"
        with pytest.raises(lock.SyncInProgress):
            with lock.sync_lock():
                pytest.fail("no debería entrar")
    finally:
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)
    with lock.sync_lock():  # al acabar el otro proceso, el sistema libera el bloqueo
        pass
