from __future__ import annotations

import asyncio
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from finmcp.db.models import Base, SyncRun
from finmcp.sync.lock import EXIT_BUSY
from finmcp.web import app as webapp
from finmcp.web import auth, syncjob

LAN = ("192.168.1.20", 50000)
TODAY = date(2026, 10, 22)
PIN = "2580"


@pytest.fixture
def db(tmp_path):
    """BD en fichero (el proceso hijo falso escribe en ella, como haría `finmcp sync`)."""
    path = tmp_path / "finmcp.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    return path, sessionmaker(bind=engine, future=True)


def fake_sync(path, *rows, sleep=0.0, code=0, stderr=""):
    """Comando que imita a `finmcp sync`: apunta una fila por banco en `sync_runs` y sale."""
    script = f"""
import datetime, sqlite3, sys, time
time.sleep({sleep})
con = sqlite3.connect({str(path)!r})
for bank, status, new, detail in {list(rows)!r}:
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    con.execute("insert into sync_runs (bank_id, started_at, finished_at, status, accounts_synced,"
                " tx_added, detail) values (?,?,?,?,?,?,?)", (bank, now, now, status, 1, new, detail))
con.commit()
sys.stderr.write({stderr!r})
sys.exit({code})
"""
    seen: list[str] = []

    def command(from_date: str) -> list[str]:
        seen.append(from_date)
        return [sys.executable, "-c", script]

    command.seen = seen
    return command


def add_run(factory, *, finished_ago: float, now: datetime, status="ok", bank="ING"):
    """Una sincronización anterior (cron, CLI…) terminada hace `finished_ago` segundos."""
    end = (now - timedelta(seconds=finished_ago)).replace(tzinfo=None)
    with factory() as s:
        s.add(SyncRun(bank_id=bank, started_at=end - timedelta(seconds=8), finished_at=end,
                      status=status, accounts_synced=1, tx_added=0))
        s.commit()


def make_job(factory, command, **kw):
    kw.setdefault("cooldown", 0)
    kw.setdefault("timeout", 20)
    return syncjob.SyncJob(factory, command=command, today=lambda: TODAY, **kw)


async def run_to_end(job: syncjob.SyncJob):
    snap, started = await job.start()
    if started:
        await job._task
    return snap, started, await job.snapshot()


# --- SyncJob ---------------------------------------------------------------------------


def test_sync_ok_resume_los_bancos_y_las_nuevas(db):
    path, factory = db
    cmd = fake_sync(path, ("BBVA", "ok", 2, ""), ("ING", "ok", 1, ""))
    job = make_job(factory, cmd)

    first, started, done = asyncio.run(run_to_end(job))

    assert started and first["state"] == "running"
    assert done["state"] == "finished" and done["outcome"] == "ok" and done["error"] is None
    assert done["new_transactions"] == 3
    assert [(b["bank"], b["new"]) for b in done["banks"]] == [("BBVA", 2), ("ING", 1)]
    assert done["last_sync_at"] and done["finished_at"]


def test_sync_pide_los_ultimos_30_dias(db):
    path, factory = db
    cmd = fake_sync(path, ("ING", "ok", 0, ""))

    asyncio.run(run_to_end(make_job(factory, cmd)))

    assert cmd.seen == ["2026-09-22"]  # hoy − 30 días: el rango explícito del cron diario


def test_sync_parcial_si_un_banco_falla(db):
    path, factory = db
    cmd = fake_sync(path, ("BBVA", "ok", 1, ""),
                    ("Trade Republic", "error", 0, "Client error '429 Too Many Requests'"), code=1)

    _, _, done = asyncio.run(run_to_end(make_job(factory, cmd)))

    assert done["outcome"] == "partial"
    assert done["error"] == "el banco ha limitado los accesos (429)"
    assert [b["status"] for b in done["banks"]] == ["ok", "error"]
    assert done["new_transactions"] == 1


def test_sync_error_si_fallan_todos(db):
    path, factory = db
    cmd = fake_sync(path, ("BBVA", "error", 0, "Errno -5 No address associated with hostname"), code=1)

    _, _, done = asyncio.run(run_to_end(make_job(factory, cmd)))

    assert done["outcome"] == "error" and done["error"] == "fallo de red (DNS)"


def test_sync_error_si_el_proceso_cae_sin_apuntar_nada(db):
    path, factory = db
    cmd = fake_sync(path, code=1, stderr="Traceback...\nValueError: sin credenciales\n")

    _, _, done = asyncio.run(run_to_end(make_job(factory, cmd)))

    assert done["outcome"] == "error" and done["error"] == "ValueError: sin credenciales"
    assert done["banks"] == []


def test_sync_ocupada_si_otra_tiene_el_bloqueo(db):
    path, factory = db
    cmd = fake_sync(path, code=EXIT_BUSY, stderr="Ya hay otra sincronización en curso.\n")

    _, _, done = asyncio.run(run_to_end(make_job(factory, cmd)))

    assert done["outcome"] == "busy" and "en curso" in done["error"]


def test_sync_se_cancela_si_tarda_demasiado(db):
    path, factory = db
    job = make_job(factory, fake_sync(path, ("ING", "ok", 0, ""), sleep=30), timeout=0.5)
    t0 = time.monotonic()

    _, _, done = asyncio.run(run_to_end(job))

    assert done["outcome"] == "timeout" and "demasiado" in done["error"]
    assert time.monotonic() - t0 < 10  # el hijo se mata: no se espera a que acabe su `sleep`


def test_no_lanza_otra_mientras_hay_una_en_marcha(db):
    path, factory = db
    job = make_job(factory, fake_sync(path, ("ING", "ok", 1, ""), sleep=0.6))

    async def scenario():
        _, started1 = await job.start()
        snap2, started2 = await job.start()
        await job._task
        return started1, started2, snap2

    started1, started2, snap2 = asyncio.run(scenario())

    assert started1 and not started2
    assert snap2["state"] == "running" and snap2["retry_after"] == 0
    with factory() as s:  # una sola corrida real
        assert s.query(SyncRun).count() == 1


def test_freno_tras_una_sincronizacion_reciente_de_cualquier_origen(db):
    path, factory = db
    now = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
    add_run(factory, finished_ago=60, now=now)  # p. ej. el cron o un `finmcp sync` a mano
    cmd = fake_sync(path, ("ING", "ok", 0, ""))
    job = make_job(factory, cmd, cooldown=300, now=lambda: now)

    snap, started = asyncio.run(job.start())

    assert not started and snap["state"] == "idle"
    assert snap["retry_after"] == 240  # 300 − 60
    assert cmd.seen == []  # no se tocó el banco


def test_pasado_el_freno_si_lanza(db):
    path, factory = db
    now = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
    add_run(factory, finished_ago=400, now=now)
    job = make_job(factory, fake_sync(path, ("ING", "ok", 0, "")), cooldown=300, now=lambda: now)

    _, started, _ = asyncio.run(run_to_end(job))

    assert started


def test_freno_tras_la_propia_sincronizacion(db):
    path, factory = db
    job = make_job(factory, fake_sync(path, ("ING", "ok", 0, "")), cooldown=300)

    async def scenario():
        await run_to_end(job)
        return await job.start()

    snap, started = asyncio.run(scenario())

    assert not started and snap["state"] == "finished" and 0 < snap["retry_after"] <= 300


def test_sin_freno_con_cooldown_cero(db):
    path, factory = db
    job = make_job(factory, fake_sync(path, ("ING", "ok", 0, "")), cooldown=0)

    async def scenario():
        await run_to_end(job)
        return await run_to_end(job)

    _, started, done = asyncio.run(scenario())

    assert started and done["outcome"] == "ok"


def test_el_estado_nunca_se_queda_en_running_si_no_arranca_el_hijo(db):
    _, factory = db
    job = make_job(factory, lambda from_date: ["/no/existe/finmcp"])

    _, _, done = asyncio.run(run_to_end(job))

    assert done["state"] == "finished" and done["outcome"] == "error"
    assert "No se pudo lanzar" in done["error"]


# --- API ---------------------------------------------------------------------------------


def client_for(factory, job, **kw):
    app = webapp.create_app(factory, today=lambda: TODAY, sync_job=job, **kw)
    return TestClient(app, client=LAN)


def wait_finished(client, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        body = client.get("/api/sync").json()
        if body["state"] == "finished":
            return body
        time.sleep(0.05)
    raise AssertionError("la sincronización no terminó")


def test_api_sync_lanza_y_se_consulta(db):
    path, factory = db
    job = make_job(factory, fake_sync(path, ("BBVA", "ok", 2, ""), sleep=0.3), cooldown=300)

    with client_for(factory, job) as client:  # `with`: el bucle sigue vivo entre peticiones
        r = client.post("/api/sync", json={})
        assert r.status_code == 202
        assert r.json()["started"] is True and r.json()["state"] == "running"

        again = client.post("/api/sync", json={})  # una segunda mientras corre: se une a la primera
        assert again.status_code == 200 and again.json()["started"] is False

        done = wait_finished(client)
        assert done["outcome"] == "ok" and done["new_transactions"] == 2

        brake = client.post("/api/sync", json={})  # recién terminada: freno
        assert brake.status_code == 200 and brake.json()["started"] is False
        assert 0 < brake.json()["retry_after"] <= 300


def test_api_sync_exige_json(db):
    path, factory = db
    job = make_job(factory, fake_sync(path, ("ING", "ok", 0, "")))

    with client_for(factory, job) as client:
        assert client.post("/api/sync", content="x=1",
                           headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 415
        assert client.post("/api/sync", content="hola",
                           headers={"content-type": "text/plain"}).status_code == 415
        assert client.get("/api/sync").json()["state"] == "idle"  # nada se lanzó


def test_api_sync_no_disponible_sin_gestor(db):
    _, factory = db

    with client_for(factory, None) as client:
        assert client.post("/api/sync", json={}).status_code == 404
        assert client.get("/api/sync").status_code == 404
        assert client.get("/api/meta").json()["sync"] is False


def test_meta_anuncia_la_sincronizacion(db):
    path, factory = db

    with client_for(factory, make_job(factory, fake_sync(path))) as client:
        assert client.get("/api/meta").json()["sync"] is True


def test_api_sync_pide_pin_y_solo_red_local(db):
    path, factory = db
    pin_hash = auth.hash_pin(PIN, iterations=1000)
    job = make_job(factory, fake_sync(path, ("ING", "ok", 0, "")))
    app = webapp.create_app(factory, today=lambda: TODAY, sync_job=job, pin_hash=pin_hash,
                            session_secret=b"k" * 32)

    with TestClient(app, client=LAN) as client:
        assert client.post("/api/sync", json={}).status_code == 401
        assert client.get("/api/sync").status_code == 401
        assert client.post("/api/login", json={"pin": PIN}).status_code == 204
        assert client.post("/api/sync", json={}).status_code == 202
        wait_finished(client)

    with TestClient(app, client=("8.8.8.8", 50000)) as outside:
        assert outside.post("/api/sync", json={}).status_code == 403
