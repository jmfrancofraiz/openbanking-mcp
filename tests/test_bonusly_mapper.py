"""Proveedor Bonusly: traducción de reconocimientos, canjes y saldo (datos ficticios)."""

from __future__ import annotations

from datetime import datetime

import pytest

from finmcp.providers.bonusly import mapper

BAL = {"id": "u1", "name": "Ana Prueba", "points_name": "NetCoins", "currency": "CHF",
       "exchange_rate": 1.0, "earned_balance": 1643, "giving_balance": 155,
       "lifetime_earnings": 12636, "lifetime_redeemed": 1154000}
KW = {"eur_per_point": 1.08, "tz": "Europe/Madrid"}


def _red(name="€100 Amazon.es Gift Card", price=93, state="fulfilled", type_="gift_card"):
    return {"id": "r1", "catalog_item_id": "c1", "created_at": "2026-05-22T14:20:46Z",
            "state": state, "certificate_url": "https://x", "claim_url": None,
            "reward_details": {"id": "c1", "name": name, "price": price, "type": type_,
                               "display_price": f"{price} NetCoins",
                               "categories": [{"name": "General", "key": "general"}],
                               "description": "texto largo", "disclaimer_html": "<sup>*</sup>",
                               "image_url": "https://img"}}


def test_cuenta_en_euros_y_nunca_manual():
    acc = mapper.to_account(BAL, bank_id="Bonusly", eur_per_point=1.08)
    assert (acc.provider_account_id, acc.bank_id, acc.name, acc.type, acc.iban, acc.currency) == (
        "bonusly-u1", "Bonusly", "Bonusly", "OTHR", None, "EUR")
    assert acc.raw["source"] == "bonusly" and acc.raw["eur_per_point"] == 1.08


def test_saldo_en_euros():
    bal = mapper.to_balance(BAL, eur_per_point=1.08)
    assert bal.current == bal.available == 1774.44  # 1643 × 1,08
    assert bal.currency == "EUR"


def test_valor_del_punto_invalido():
    with pytest.raises(ValueError):
        mapper.points_to_eur(10, 0)


def test_fecha_local_de_madrid():
    assert mapper.booked_date("2026-04-15T23:30:00Z") == datetime(2026, 4, 16)  # 01:30 en Madrid
    assert mapper.booked_date("2026-01-15T22:30:00Z") == datetime(2026, 1, 15)  # 23:30 en invierno


def test_reconocimiento_es_ingreso():
    r = {"id": "b1", "giver": {"name": "Luis Prueba", "email": "l@x"}, "amount": 10,
         "reason": "+10 @ana gracias\n #teamwork", "created_at": "2026-04-15T10:00:00Z",
         "recipient_count": 1, "parent_bonus_id": None, "top_level": True}
    tx = mapper.recognition_to_tx(r, **KW)
    assert (tx.provider_id, tx.type, tx.amount, tx.currency) == ("bonusly-rec-b1", "credit", 10.8, "EUR")
    assert tx.booked_at == datetime(2026, 4, 15)
    assert tx.merchant_name == "Luis Prueba"
    # el mensaje no va en la descripción (haría casar reglas ajenas), pero se conserva
    assert tx.description == "Bonusly · reconocimiento de Luis Prueba"
    assert tx.raw["reason"] == "+10 @ana gracias\n #teamwork"
    assert tx.raw["points"] == 10 and tx.raw["eur_per_point"] == 1.08


def test_reconocimiento_del_feed_no_guarda_la_lista_de_receptores():
    r = {"id": "b9", "giver": {"name": "Luis"}, "amount": 1, "reason": "", "top_level": True,
         "created_at": "2026-09-30T21:15:53Z", "receivers": [{"name": f"p{i}"} for i in range(38)]}
    tx = mapper.recognition_to_tx(r, **KW)
    assert tx.description == "Bonusly · reconocimiento de Luis"
    assert "receivers" not in tx.raw and tx.raw["receiver_count"] == 38


def test_reconocimiento_sin_puntos_se_ignora():
    r = {"id": "b2", "giver": {"name": "X"}, "amount": 0, "reason": "", "created_at": "2026-04-15T10:00:00Z"}
    assert mapper.recognition_to_tx(r, **KW) is None


@pytest.mark.parametrize("name, merchant, face", [
    ("€100 Amazon.es Gift Card", "Amazon.es", 100.0),
    ("€10 El Corte Inglés ES Gift Card", "El Corte Inglés", 10.0),
    ("€15 El Corte Inglés Gift Card", "El Corte Inglés", 15.0),
    ("€100 Media Markt Spain Gift Card", "Media Markt", 100.0),
    ("€10 Glovo  Gift Card", "Glovo", 10.0),
    ("€5 Casa del Libro ES Gift Card", "Casa del Libro", 5.0),
    ("5 Net Coins Points Boost", "5 Net Coins Points Boost", None),
])
def test_comercio_de_la_recompensa(name, merchant, face):
    assert mapper.reward_merchant(name) == (merchant, face)


def test_canje_es_gasto_en_el_comercio():
    txs = mapper.redemption_to_txs(_red(), **KW)
    assert [(t.provider_id, t.type, t.amount) for t in txs] == [("bonusly-red-r1", "debit", 100.44)]
    tx = txs[0]
    assert tx.merchant_name == "Amazon.es"
    assert tx.description == "Bonusly · canje: €100 Amazon.es Gift Card"
    assert tx.provider_category == "General"
    assert tx.raw["points"] == 93 and tx.raw["face_value_eur"] == 100.0
    assert "description" not in tx.raw["reward_details"]  # sin el texto del catálogo


def test_canje_rechazado_lleva_devolucion():
    txs = mapper.redemption_to_txs(_red(name="€10 Zalando Spain Gift Card", price=10, state="declined"), **KW)
    assert [(t.provider_id, t.type, t.amount) for t in txs] == [
        ("bonusly-red-r1", "debit", 10.8), ("bonusly-red-r1-refund", "credit", 10.8)]
    assert txs[1].description == "Bonusly · devolución de canje: €10 Zalando Spain Gift Card"
    assert txs[1].merchant_name == "Zalando"


def test_canje_sin_precio_se_ignora():
    assert mapper.redemption_to_txs(_red(price=0), **KW) == []
