"""Traduce las respuestas de Bonusly a los tipos normalizados de finmcp.

Bonusly lleva el saldo en puntos (en esta empresa, «NetCoins»). finmcp lo trata como una
cuenta en euros: cada punto vale `eur_per_point` € (ajuste `BONUSLY_EUR_PER_POINT`).
Ingresos = reconocimientos recibidos; gastos = canjes de recompensas.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from finmcp.providers.types import Account, Balance, Transaction

DEFAULT_TZ = "Europe/Madrid"
# Estados de un canje que devuelven los puntos. En oct-2026 todos los canjes eran
# «fulfilled»; si alguno llega así, se añade su devolución para que el saldo cuadre.
REVERSED_STATES = {"declined", "refunded", "canceled", "cancelled"}
# «€100 Amazon.es Gift Card» -> valor facial 100, marca «Amazon.es».
_GIFT_CARD = re.compile(r"^€\s*(\d+(?:[.,]\d+)?)\s+(.+?)\s+gift\s*card$", re.IGNORECASE)
_COUNTRY_SUFFIX = re.compile(r"\s+(ES|Spain)$", re.IGNORECASE)
# Campos voluminosos del catálogo que no aportan nada al movimiento.
_REWARD_NOISE = ("description", "disclaimer_html", "image_url")


def points_to_eur(points: float, eur_per_point: float) -> float:
    """Puntos -> euros, redondeado a céntimos."""
    if not eur_per_point or eur_per_point <= 0:
        raise ValueError(f"Valor del punto de Bonusly no válido: {eur_per_point!r}")
    return round(points * eur_per_point, 2)


def booked_date(created_at: str, tz: str = DEFAULT_TZ) -> datetime:
    """Día local del instante UTC de Bonusly, a medianoche y sin zona (como los bancos)."""
    instant = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    local = instant.astimezone(ZoneInfo(tz))
    return datetime(local.year, local.month, local.day)


def reward_merchant(name: str | None) -> tuple[str, float | None]:
    """Comercio y valor facial (€) de una recompensa.

    «€100 Amazon.es Gift Card» -> («Amazon.es», 100.0); «€10 El Corte Inglés ES Gift Card» ->
    («El Corte Inglés», 10.0). Lo que no es una tarjeta regalo conserva su nombre, sin valor.
    """
    clean = " ".join((name or "").split())
    m = _GIFT_CARD.match(clean)
    if not m:
        return clean or "Recompensa", None
    brand = _COUNTRY_SUFFIX.sub("", m.group(2)).strip()
    return brand, float(m.group(1).replace(",", "."))


def to_account(balance: dict, bank_id: str, eur_per_point: float) -> Account:
    return Account(
        provider_account_id=f"bonusly-{balance['id']}",
        name="Bonusly",
        type="OTHR",
        currency="EUR",
        iban=None,
        bank_id=bank_id,
        raw={
            "source": "bonusly",
            "user_id": balance["id"],
            "points_name": balance.get("points_name"),
            "eur_per_point": eur_per_point,
        },
    )


def to_balance(balance: dict, eur_per_point: float, now: datetime | None = None) -> Balance:
    value = points_to_eur(balance["earned_balance"], eur_per_point)
    return Balance(
        available=value,
        current=value,
        currency="EUR",
        snapshot_at=now or datetime.now(timezone.utc),
        raw=balance,
    )


def recognition_to_tx(
    r: dict, *, eur_per_point: float, tz: str = DEFAULT_TZ
) -> Transaction | None:
    """Reconocimiento recibido -> ingreso. Sin puntos no mueve el saldo: None.

    Sirve para las filas de `getRecognitionReceived` y de `getRecognitionFeed` (en ambas,
    `amount` son los puntos que recibe cada receptor). El mensaje queda solo en `raw_json`:
    en la descripción haría casar reglas ajenas (y sus etiquetas, que se suman aunque
    gane otra regla) con cualquier palabra del texto.
    """
    points = r.get("amount") or 0
    if points <= 0:
        return None
    giver = (r.get("giver") or {}).get("name") or "Bonusly"
    raw = {k: v for k, v in r.items() if k != "receivers"}
    if "receivers" in r:
        raw["receiver_count"] = len(r.get("receivers") or [])
    raw.update(points=points, eur_per_point=eur_per_point)
    return Transaction(
        provider_id=f"bonusly-rec-{r['id']}",
        amount=points_to_eur(points, eur_per_point),
        currency="EUR",
        booked_at=booked_date(r["created_at"], tz),
        description=f"Bonusly · reconocimiento de {giver}",
        type="credit",
        merchant_name=giver,
        provider_category="bonusly_recognition",
        raw=raw,
    )


def redemption_to_txs(
    d: dict, *, eur_per_point: float, tz: str = DEFAULT_TZ
) -> list[Transaction]:
    """Canje -> gasto en el comercio de la recompensa (+ su devolución si se rechazó).

    El importe son los puntos que cuesta la recompensa (`reward_details.price`) en euros.
    Ojo: Bonusly da el precio de catálogo ACTUAL, no el que se pagó en su día.
    """
    details = d.get("reward_details") or {}
    points = details.get("price") or 0
    if points <= 0:
        return []
    name = " ".join((details.get("name") or "").split()) or "Recompensa"
    merchant, face_value = reward_merchant(name)
    categories = [c.get("name") for c in details.get("categories") or [] if c.get("name")]
    raw = {k: v for k, v in d.items() if k != "reward_details"}
    raw["reward_details"] = {k: v for k, v in details.items() if k not in _REWARD_NOISE}
    raw.update(points=points, eur_per_point=eur_per_point, face_value_eur=face_value)
    amount = points_to_eur(points, eur_per_point)
    booked = booked_date(d["created_at"], tz)

    def make(suffix: str, typ: str, description: str, category: str) -> Transaction:
        return Transaction(
            provider_id=f"bonusly-red-{d['id']}{suffix}",
            amount=amount,
            currency="EUR",
            booked_at=booked,
            description=description,
            type=typ,
            merchant_name=merchant,
            provider_category=category,
            raw=raw,
        )

    txs = [make("", "debit", f"Bonusly · canje: {name}",
                ", ".join(categories) or "bonusly_redemption")]
    if (d.get("state") or "").lower() in REVERSED_STATES:
        txs.append(make("-refund", "credit", f"Bonusly · devolución de canje: {name}",
                        "bonusly_refund"))
    return txs
