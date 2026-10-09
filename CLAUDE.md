# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Read-only personal-finance MCP server over Open Banking (PSD2). Pulls accounts, balances and
transactions from a bank via an aggregator (Enable Banking by default), stores them in a local
SQLite database, and exposes analytics (spend by category, bills/recurring payments, unusual charges,
monthly summaries) as MCP tools. Code comments, docstrings, CLI output and README are in Spanish;
keep new user-facing strings and comments in Spanish for consistency.

The `data/` directory (SQLite db, encrypted tokens, Fernet key, private key `.pem`) is
gitignored and must never be committed.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                                   # full suite (pythonpath=src, testpaths=tests via pyproject)
pytest tests/test_anomalies.py           # one file
pytest tests/test_sync.py::test_run_sync_ok_path   # one test
pytest -k csv                            # by keyword

finmcp --help                            # CLI entry point (typer), defined in src/finmcp/cli.py
finmcp serve                             # MCP over stdio (Claude Desktop / Cursor)
finmcp serve --http --port 8000          # Streamable HTTP on /mcp; set FINMCP_HTTP_TOKEN for bearer auth
```

No linter or formatter is configured. CI (`.github/workflows/ci.yml`) only runs `python -m pytest -q`
on Python 3.11–3.13. `requires-python >= 3.11`; the code uses `X | None` unions and
`from __future__ import annotations` everywhere.

## Architecture

Data flows strictly one direction and each layer only talks to its neighbour:

```
provider client (HTTP)  ->  sync/service.run_sync  ->  SQLite (db/)  ->  analytics/  ->  mcp/server.py
```

- **Only `sync` and `list_institutions` MCP tools call the network**; the rest read/write SQLite
  (or local files for `import_csv`). `auth` is CLI-only by design (browser consent flow).
  Scheduled fetching happens via `finmcp sync` (manually or the launchd template in `deploy/`).
- **Multi-bank** (`config.py`): any number of banks can be linked at once. `Settings.banks`
  discovers `FINMCP_BANK_<n>_ID/ASPSP_NAME/COUNTRY` env vars (grouped by index `n`) and falls
  back to a single legacy bank (`id="default"`) built from `ENABLEBANKING_ASPSP_NAME` if no
  indexed vars are set. `sync/service.run_sync` and `cli.py` iterate `settings.banks`; most
  MCP tools and `db/models.Account`/`SyncRun` carry a `bank_id` to disambiguate.
- **Providers** (`src/finmcp/providers/`): Enable Banking (`enablebanking/`) is the only
  implemented provider, though `providers/base.BankDataProvider` (a `Protocol`) exists so another
  could be added later. Each subpackage has `auth.py` (consent/link flow + credentials),
  `client.py` (HTTP, implements `BankDataProvider`) and `mapper.py` (provider JSON -> normalized
  dataclasses in `types.py`). `factory.get_provider(bank)` always returns an `EnableBankingClient`
  for the given `BankConfig`. Sync and analytics never import a concrete provider.
- **Normalized types** (`providers/types.py`): `Transaction.amount` is always positive; the sign
  lives in `type` (`"debit" | "credit"`). Every mapper and the CSV importer must respect this,
  and all analytics rely on it.
- **Sync** (`sync/service.py`): `run_sync` loops over `settings.banks`, calling `_sync_one_bank`
  per bank — an idempotent upsert keyed by provider transaction id. Writes a `SyncRun` row
  (with `bank_id`) *before* calling the bank so a failure is recorded as `status="error"` instead
  of hanging in `running`; one bank's failure doesn't abort the others. Balance failures are
  swallowed per account; anything else rolls back that bank's run and marks it as error. Finishes
  by calling `apply_rules` once, after all banks, to recategorize.
- **Categorization**: `Transaction.my_category` (user rules, `CategoryRule` table) always wins
  over `provider_category`; `analytics/categories.category_of` is the single place that encodes
  this precedence. Rules are case-insensitive substring matches on merchant/description/any,
  lowest `priority` wins; `finmcp rules add`/`update`/`remove` (tools
  `add_category_rule`/`update_category_rule`/`remove_category_rules`) manage them and every
  rule mutation reapplies `apply_rules` to the existing data. `repo.upsert_transaction` deliberately does not touch `my_category` so
  re-syncs preserve manual categorization.
- **Neutral transactions**: `Transaction.neutral` marks transfers/card settlements that are
  neither income nor expense. `apply_rules` copies `neutral` from the winning rule (like the
  category); expense analytics query with `neutral=False`, and `monthly_summary` lists them apart.
- **Manual override**: `Transaction.skip_category_rules` (set by `finmcp recategorize` /
  `set_transactions_skip_rules`) freezes a hand-picked category; `apply_rules` skips those
  transactions entirely — category, neutral, is_bill and rule tags untouched.
- **Bills / recurring payments**: `Transaction.is_bill` and `CategoryRule.is_bill` mark a
  transaction/rule as a recibo (direct debit, subscription...). `apply_rules` copies `is_bill`
  from the winning rule, exactly like `neutral`. There is no heuristic detector any more
  (the old `analytics/subscriptions.py` amount-stability/cadence guesswork was removed);
  `analytics/bills.list_bills` / `finmcp bills` / MCP tool `list_bills` simply query
  `is_bill=True` and group by merchant for display. Managed via `finmcp rules add --is-bill`,
  `finmcp rules update --is-bill/--no-is-bill`, `finmcp rules bill RULE_ID [--off]`.
- **Tags** (`analytics/tagging.py`): canonical `tags` table, N:M with transactions via
  `transaction_tags` (`source` = `manual` | `rule` | `excluded`) and with `CategoryRule` via
  `category_rule_tags`. `apply_rules` gives each tx the union of tags of *all* matching rules,
  only touching `rule` links; `excluded` is a tombstone left by manual untag so rules don't
  re-add it. `delete_tag` refuses to delete tags in use (no cascades). Names are normalized by
  `util.normalize_tag`.
- **Analytics** (`analytics/`): pure functions taking a SQLAlchemy `Session`, all reading through
  `db/queries.query_transactions`. Unusual charges use median + MAD (not mean/stddev) so a single
  spike does not contaminate its own baseline; when MAD is 0 it falls back to a 50%-over-median
  rule. Both `anomalies.py` and `bills.py` group by `util.merchant_key` (merchant, else
  description, lowercased).
- **CSV/Excel importer** (`importers/csv_import.py`): for history older than the ~90 days PSD2
  allows. Header detection is heuristic (Spanish/English key lists at the top of the file),each
  bank's link (session id + account list) is stored via `save_secret(name, ...)` where `name` is
  `enablebanking_link` for the legacy `"default"` bank id or `enablebanking_link_<bank_id>`
  otherwise (see `auth._link_store`).
- **Config** (`config.py`): a single module-level `settings = Settings()` (pydantic-settings,
  reads `.env`). Fields map by name (e.g. `enablebanking_app_id` <- `ENABLEBANKING_APP_ID`).
  `db/session.py` **Secrets** (`security/tokens.py`): everything at rest in `data/*.enc` is Fernet-encrypted with
  a key from `FINMCP_ENCRYPTION_KEY` or an auto-generated `data/fernet.key` (chmod 600). Enable
  Banking auth is a per-request RS256 JWT signed with the app private key (`kid` = app id); the
  bank link (session id + account list) is stored via `save_secret("enablebanking_link", ...)`.
- **Config** (`config.py`): a single module-level `settings = Settings()` (pydantic-settings,
  reads `.env`). Note the provider field uses `validation_alias="FINMCP_PROVIDER"` while other
  fields map by name (e.g. `enablebanking_app_id` <- `ENABLEBANKING_APP_ID`). `db/session.py`
  builds the engine at import time from `settings.db_url`.

## Testing conventions

`tests/conftest.py` provides an in-memory SQLite `Session` factory (StaticPool so multiple
sessions share one DB), a `session` fixture pre-seeded with account `acc1`, and a `make_tx`
factory. Sync tests monkeypatch `service.SessionLocal`, `service.init_db` and
`service.get_provider` with fakes; no test touches the network or `data/`. Mapper tests feed raw
provider JSON fixtures directly into `mapper.to_*`.
