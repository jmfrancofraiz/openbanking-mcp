"""Pestaña «Diario» del panel web: el día a día, sin agregaciones (solo lectura).

A diferencia de `dashboard.py`, aquí no se agrupa ni se excluye nada: se listan TODOS
los movimientos (también los no computables) y el último saldo conocido de cada cuenta.

- `accounts()`: una tarjeta por cuenta con su último saldo y la frescura de su sincronización.
- `movements()`: lista plana de la fecha más reciente a la más antigua, con filtros y
  paginación por cursor. Orden estable: fecha descendente y, dentro del día, id descendente
  (el banco solo da la fecha, sin hora: el orden dentro de un día no es el real).
- `closing_balances()`: saldo al cierre de cada día de una cuenta, **calculado** hacia atrás
  desde la última foto de saldo del banco, y su contraste con todas las fotos reales.
- `movement()`: detalle de un movimiento con los datos originales del banco (`raw_json`).
"""

from __future__ import annotations

import bisect
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, selectinload

from finmcp.analytics.categories import category_of
from finmcp.db import models
from finmcp.db.queries import latest_balances, latest_sync_runs
from finmcp.util import normalize_tag

# Las fechas contables de los bancos son locales (España); las fotos de saldo, UTC.
LOCAL_TZ = ZoneInfo("Europe/Madrid")
# Una cuenta sin foto de saldo en este plazo se marca como desactualizada.
STALE_AFTER = timedelta(hours=36)
MARKS = ("uncategorized", "bill", "neutral", "manual")
# Tope de combinaciones al contrastar una foto de saldo con los movimientos de su día.
MAX_SUBSET_SUMS = 1 << 16

STATUS_LABELS = {"BOOK": "contabilizado", "PDNG": "pendiente", "INFO": "informativo"}


# --- utilidades --------------------------------------------------------------


def _aware(ts: datetime) -> datetime:
    """Las marcas de tiempo de la BD son UTC sin zona: se les pone la zona."""
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def iso_utc(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return _aware(ts).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def local_date(ts: datetime) -> date:
    return _aware(ts).astimezone(LOCAL_TZ).date()


def balance_value(b: models.Balance | None) -> tuple[float | None, str | None]:
    """Saldo de una foto: el contable («actual») o, si el banco no lo da, el disponible."""
    if b is None:
        return None, None
    if b.current is not None:
        return b.current, "actual"
    if b.available is not None:
        return b.available, "disponible"
    return None, None


def signed(t: models.Transaction) -> float:
    return t.amount if t.type == "credit" else -t.amount


def _cents(value: float) -> int:
    return round(value * 100)


def iban_tail(iban: str | None) -> str | None:
    return iban[-4:] if iban else None


def fold(text: str) -> str:
    """Minúsculas y sin acentos, para buscar sin distinguir «Tomiño» de «TOMINO»."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def short_error(detail: str | None) -> str | None:
    """Motivo legible del último fallo de sincronización de un banco."""
    if not detail:
        return None
    text = detail.strip()
    if "429" in text:
        return "el banco ha limitado los accesos (429)"
    if "Errno -5" in text or "address associated" in text or "Name or service not known" in text:
        return "fallo de red (DNS)"
    line = text.splitlines()[0]
    return line[:160] + ("…" if len(line) > 160 else "")


def _get(data, *path):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


# --- cuentas -------------------------------------------------------------------


def accounts(session: Session, now: datetime) -> list[dict]:
    """Cada cuenta con su último saldo conocido y el estado de su sincronización.

    `state`: «stale» si no hay saldo o la foto tiene más de 36 h; «warn» si el último
    intento de sincronizar ese banco falló (el saldo mostrado es el de la foto anterior);
    «ok» en otro caso.
    """
    now = _aware(now)
    T = models.Transaction
    balances = {b.account_id: b for b in latest_balances(session)}
    runs = {r.bank_id: r for r in latest_sync_runs(session)}
    last_ok = dict(
        session.query(models.SyncRun.bank_id, func.max(models.SyncRun.finished_at))
        .filter(models.SyncRun.status == "ok")
        .group_by(models.SyncRun.bank_id)
        .all()
    )
    stats = {
        acc: (n, first, last)
        for acc, n, first, last in session.query(
            T.account_id, func.count(T.id), func.min(T.booked_at), func.max(T.booked_at)
        ).group_by(T.account_id)
    }
    out = []
    for a in sorted(session.query(models.Account).all(),
                    key=lambda a: (a.bank_id.lower(), a.name or "", a.id)):
        b = balances.get(a.id)
        value, kind = balance_value(b)
        run = runs.get(a.bank_id)
        n, first, last = stats.get(a.id, (0, None, None))
        if value is None or now - _aware(b.snapshot_at) > STALE_AFTER:
            state = "stale"
        elif run is not None and run.status == "error":
            state = "warn"
        else:
            state = "ok"
        out.append({
            "id": a.id,
            "bank": a.bank_id,
            "name": a.name,
            "iban_tail": iban_tail(a.iban),
            "currency": a.currency,
            "balance": None if value is None else {
                "value": round(value, 2), "kind": kind, "at": iso_utc(b.snapshot_at),
            },
            "sync": None if run is None else {
                "status": run.status,
                "at": iso_utc(run.finished_at or run.started_at),
                "last_ok": iso_utc(last_ok.get(a.bank_id)),
                "error": short_error(run.detail) if run.status == "error" else None,
            },
            "state": state,
            "count": n,
            "first": first.date().isoformat() if first else None,
            "last": last.date().isoformat() if last else None,
        })
    return out


def tags_in_use(session: Session) -> list[dict]:
    """Etiquetas con algún movimiento (sin contar las exclusiones), por nombre."""
    TT = models.TransactionTag
    rows = (
        session.query(models.Tag.name, func.count(TT.transaction_id))
        .join(TT, TT.tag_id == models.Tag.id)
        .filter(TT.source != models.TAG_EXCLUDED)
        .group_by(models.Tag.name)
        .order_by(models.Tag.name)
        .all()
    )
    return [{"name": name, "count": n} for name, n in rows]


# --- saldo al cierre de cada día (calculado) ---------------------------------------


def _subset_sum(amounts: list[int], target: int) -> bool | None:
    """¿Suma `target` (±1 céntimo) algún subconjunto de `amounts`? None si hay demasiados."""
    sums = {0}
    for a in amounts:
        sums |= {x + a for x in sums}
        if len(sums) > MAX_SUBSET_SUMS:
            return None
    return any(target + d in sums for d in (-1, 0, 1))


def closing_balances(session: Session, account_id: str) -> dict | None:
    """Saldo al cierre de cada día de una cuenta, reconstruido desde la última foto del banco.

    cierre(D) = foto más reciente − Σ movimientos de los días posteriores a D. Es un cálculo,
    no un dato del banco. Se contrasta con TODAS las fotos reales: la foto tomada el día d
    (hora local) debe valer cierre(d − 1) más algún subconjunto de los movimientos del día d,
    porque la de primera hora ya recoge parte de ese día. Si una foto no cuadra, falta o
    sobra algún movimiento y los saldos calculados hasta esa fecha pueden estar desviados.
    Devuelve None si la cuenta no tiene ninguna foto con saldo.
    """
    T = models.Transaction
    snaps = []
    for s in (session.query(models.Balance)
              .filter(models.Balance.account_id == account_id)
              .order_by(models.Balance.snapshot_at)):
        value, kind = balance_value(s)
        if value is not None:
            snaps.append((s, value, kind))
    if not snaps:
        return None
    anchor, anchor_value, anchor_kind = snaps[-1]

    per_day: dict[date, list[int]] = {}
    for booked, typ, amount in session.query(T.booked_at, T.type, T.amount).filter(
            T.account_id == account_id):
        per_day.setdefault(booked.date(), []).append(_cents(amount if typ == "credit" else -amount))
    days = sorted(per_day)  # ascendente
    closing: dict[date, int] = {}
    before_day: list[int] = [0] * len(days)  # cierre del día anterior a cada día con movimientos
    running = _cents(anchor_value)
    for i in range(len(days) - 1, -1, -1):
        closing[days[i]] = running
        running -= sum(per_day[days[i]])
        before_day[i] = running

    def before(d: date) -> int:
        """Saldo calculado al cierre del día anterior a `d`."""
        i = bisect.bisect_left(days, d)
        return before_day[i] if i < len(days) else _cents(anchor_value)

    checks = []
    for s, value, _ in snaps:
        d = local_date(s.snapshot_at)
        checks.append((s, value, d, _subset_sum(per_day.get(d, []), _cents(value) - before(d))))
    bad = [c for c in checks if c[3] is False]
    last_bad = None
    if bad:
        s, value, d, _ = bad[-1]
        last_bad = {
            "at": iso_utc(s.snapshot_at),
            "date": d.isoformat(),
            "bank_value": round(value, 2),
            "calc_prev": before(d) / 100,
            "calc_day": before(d + timedelta(days=1)) / 100,
        }
    return {
        "anchor": {"value": round(anchor_value, 2), "kind": anchor_kind, "at": iso_utc(anchor.snapshot_at)},
        "closing": {d.isoformat(): c / 100 for d, c in closing.items()},
        "check": {
            "snapshots": len(checks),
            "ok": sum(1 for c in checks if c[3] is True),
            "unknown": sum(1 for c in checks if c[3] is None),
            "bad": len(bad),
            "since": checks[0][2].isoformat(),
            "last_bad": last_bad,
            # Los saldos calculados de los días hasta esta fecha pueden estar desviados.
            "suspect_through": last_bad["date"] if last_bad else None,
        },
    }


# --- movimientos ------------------------------------------------------------------


def _row(t: models.Transaction, banks: dict[str, str]) -> dict:
    return {
        "id": t.id,
        "date": t.booked_at.date().isoformat(),
        "amount": round(signed(t), 2) + 0.0,
        "currency": t.currency,
        "merchant": t.merchant_name,
        "description": t.description,
        "category": category_of(t),
        "uncategorized": not t.my_category,
        "bank": banks.get(t.account_id, ""),
        "account": t.account_id,
        "tags": t.tag_names,
        "is_bill": bool(t.is_bill),
        "neutral": bool(t.neutral),
        "manual": bool(t.skip_category_rules),
    }


def _haystack(t: models.Transaction, bank: str) -> str:
    amount = f"{t.amount:.2f}"
    parts = [t.merchant_name or "", t.description or "", category_of(t), bank,
             *t.tag_names, amount, amount.replace(".", ",")]
    return fold(" ".join(parts))


def encode_cursor(t: models.Transaction) -> str:
    return f"{t.booked_at.isoformat()}|{t.id}"


def movements(
    session: Session,
    *,
    account: str | None = None,
    kind: str | None = None,
    mark: str | None = None,
    tag: str | None = None,
    start: date | None = None,
    end: date | None = None,
    text: str | None = None,
    cursor: tuple[datetime, str] | None = None,
    limit: int = 100,
) -> dict:
    """Todos los movimientos (también los no computables), del más reciente al más antiguo.

    Filtros opcionales: cuenta, tipo (`debit`/`credit`), una marca (`MARKS`), etiqueta,
    fechas (ambas incluidas) y texto (todas las palabras, sin distinguir mayúsculas ni
    acentos, en comercio, concepto, categoría, banco, etiquetas o importe). `next` es el
    cursor de la página siguiente, o None si no hay más. Con `account`, `closing` trae el
    saldo calculado al cierre de cada día de la página (ver `closing_balances`).
    """
    T = models.Transaction
    banks = dict(session.query(models.Account.id, models.Account.bank_id).all())
    q = session.query(T).options(selectinload(T.tag_links).selectinload(models.TransactionTag.tag))
    if account:
        q = q.filter(T.account_id == account)
    if kind:
        q = q.filter(T.type == kind)
    if mark == "uncategorized":
        q = q.filter(or_(T.my_category.is_(None), T.my_category == ""))
    elif mark == "bill":
        q = q.filter(T.is_bill.is_(True))
    elif mark == "neutral":
        q = q.filter(T.neutral.is_(True))
    elif mark == "manual":
        q = q.filter(T.skip_category_rules.is_(True))
    if tag:
        q = q.filter(T.tag_links.any(and_(
            models.TransactionTag.source != models.TAG_EXCLUDED,
            models.TransactionTag.tag.has(models.Tag.name == normalize_tag(tag)),
        )))
    if start:
        q = q.filter(T.booked_at >= datetime.combine(start, time.min))
    if end:
        q = q.filter(T.booked_at < datetime.combine(end + timedelta(days=1), time.min))
    if cursor:
        at, tx_id = cursor
        q = q.filter(or_(T.booked_at < at, and_(T.booked_at == at, T.id < tx_id)))
    q = q.order_by(T.booked_at.desc(), T.id.desc())

    words = fold(text).split() if text else []
    if not words:
        found = q.limit(limit + 1).all()
    else:
        found = []
        for t in q:
            hay = _haystack(t, banks.get(t.account_id, ""))
            if all(w in hay for w in words):
                found.append(t)
                if len(found) > limit:
                    break
    more = len(found) > limit
    found = found[:limit]
    rows = [_row(t, banks) for t in found]
    out = {"transactions": rows, "next": encode_cursor(found[-1]) if more and found else None,
           "closing": None, "balance": None}
    if account:
        cb = closing_balances(session, account)
        if cb:
            page_days = {r["date"] for r in rows}
            out["closing"] = {d: v for d, v in cb["closing"].items() if d in page_days}
            out["balance"] = {"anchor": cb["anchor"], "check": cb["check"]}
    return out


# --- detalle de un movimiento --------------------------------------------------------


def bank_fields(raw: dict) -> list[dict]:
    """Campos legibles del dato original del banco (formato PSD2 de Enable Banking)."""
    def iban(acc):
        return _get(acc, "iban") or _get(acc, "other", "identification")

    concept = raw.get("remittance_information")
    if isinstance(concept, list):
        concept = " · ".join(str(x) for x in concept if x)
    code = raw.get("bank_transaction_code")
    code = " · ".join(str(code.get(k)) for k in ("description", "code", "sub_code") if code.get(k)) \
        if isinstance(code, dict) else code
    cards = [
        f"{x.get('issuer') or 'tarjeta'} ··{str(x.get('identification'))[-4:]}"
        for x in (raw.get("debtor_account_additional_identification") or [])
        if isinstance(x, dict) and x.get("scheme_name") == "CPAN" and x.get("identification")
    ]
    rate = raw.get("exchange_rate")
    rate_text = None
    if isinstance(rate, dict) and rate.get("exchange_rate"):
        rate_text = f"{rate.get('exchange_rate')} ({rate.get('unit_currency') or '?'})"
        if _get(rate, "instructed_amount", "amount"):
            rate_text += (f" · importe original {_get(rate, 'instructed_amount', 'amount')} "
                          f"{_get(rate, 'instructed_amount', 'currency') or ''}").rstrip()
    status = raw.get("status")
    balance_after = _get(raw, "balance_after_transaction", "balance_amount", "amount") \
        or _get(raw, "balance_after_transaction", "amount")

    def es_date(value):
        """Fechas del banco (AAAA-MM-DD) en formato español; cualquier otra cosa, tal cual."""
        try:
            return date.fromisoformat(value).strftime("%d/%m/%Y") if isinstance(value, str) else value
        except ValueError:
            return value

    fields = [
        ("Beneficiario", _get(raw, "creditor", "name")),
        ("Cuenta del beneficiario", iban(raw.get("creditor_account"))),
        ("Ordenante", _get(raw, "debtor", "name")),
        ("Cuenta del ordenante", iban(raw.get("debtor_account"))),
        ("Concepto del banco", concept),
        ("Tipo de operación", code),
        ("Tarjeta", ", ".join(cards) if cards else None),
        ("Tipo de cambio", rate_text),
        ("Fecha de la operación", es_date(raw.get("transaction_date"))),
        ("Fecha contable", es_date(raw.get("booking_date"))),
        ("Fecha valor", es_date(raw.get("value_date"))),
        ("Estado", STATUS_LABELS.get(status, status)),
        ("Saldo tras el movimiento", balance_after),
        ("Nota", raw.get("note")),
        ("Referencia", raw.get("entry_reference")),
    ]
    return [{"label": k, "value": str(v)} for k, v in fields if v not in (None, "", [], {})]


def movement(session: Session, tx_id: str) -> dict | None:
    """Un movimiento con su cuenta y los datos originales del banco; None si no existe."""
    t = session.get(models.Transaction, tx_id)
    if t is None:
        return None
    acc = session.get(models.Account, t.account_id)
    bank = acc.bank_id if acc else ""
    raw = t.raw_json if isinstance(t.raw_json, dict) else {}
    source = "csv" if raw.get("source") == "csv" else ("feed" if raw else "none")
    return {
        **_row(t, {t.account_id: bank}),
        "account_info": {
            "id": t.account_id,
            "bank": bank,
            "name": acc.name if acc else None,
            "iban_tail": iban_tail(acc.iban) if acc else None,
        },
        "source": source,
        "bank_data": bank_fields(raw) if source == "feed" else [],
        "raw": raw if source == "feed" else None,
    }
