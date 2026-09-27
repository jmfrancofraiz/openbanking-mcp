from __future__ import annotations

from finmcp.config import Settings


def _clear_bank_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("FINMCP_BANK_") or key.startswith("ENABLEBANKING_"):
            monkeypatch.delenv(key, raising=False)


def test_enablebanking_aspsp_name_reads_env(monkeypatch):
    monkeypatch.setenv("ENABLEBANKING_ASPSP_NAME", "CaixaBank")
    s = Settings(_env_file=None)
    assert s.enablebanking_aspsp_name == "CaixaBank"


def test_enablebanking_country_defaults_to_es(monkeypatch):
    monkeypatch.delenv("ENABLEBANKING_COUNTRY", raising=False)
    s = Settings(_env_file=None)
    assert s.enablebanking_country == "ES"


def test_banks_discovers_indexed_env_vars(monkeypatch):
    _clear_bank_env(monkeypatch)
    monkeypatch.setenv("FINMCP_BANK_1_ID", "caixa")
    monkeypatch.setenv("FINMCP_BANK_1_ASPSP_NAME", "CaixaBank")
    monkeypatch.setenv("FINMCP_BANK_1_COUNTRY", "ES")
    monkeypatch.setenv("FINMCP_BANK_2_ID", "bbva")
    monkeypatch.setenv("FINMCP_BANK_2_ASPSP_NAME", "BBVA")

    s = Settings(_env_file=None)
    banks = s.banks

    assert [b.id for b in banks] == ["caixa", "bbva"]
    assert banks[0].aspsp_name == "CaixaBank"
    assert banks[0].country == "ES"
    assert banks[1].aspsp_name == "BBVA"
    assert banks[1].country == "ES"  # default


def test_banks_falls_back_to_legacy_single_bank(monkeypatch):
    _clear_bank_env(monkeypatch)
    monkeypatch.setenv("ENABLEBANKING_ASPSP_NAME", "CaixaBank")
    monkeypatch.setenv("ENABLEBANKING_COUNTRY", "ES")

    s = Settings(_env_file=None)
    banks = s.banks

    assert len(banks) == 1
    assert banks[0].id == "default"
    assert banks[0].aspsp_name == "CaixaBank"


def test_banks_empty_when_nothing_configured(monkeypatch):
    _clear_bank_env(monkeypatch)

    s = Settings(_env_file=None)
    assert s.banks == []
