# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Read-only personal-finance MCP server over Open Banking (PSD2). Pulls accounts, balances and
transactions from a bank via an aggregator (Enable Banking by default), stores them in a local
SQLite database, and exposes analytics (spend by category, bills/recurring payments, unusual charges,
monthly summaries) as MCP tools, plus a mobile-first web dashboard (`finmcp web`) that reads the DB read-only and can only trigger a sync.
Three surfaces share the same SQLite: CLI, MCP server (same operations as the CLI except `auth`)
and the web dashboard (separate service/port). Code comments, docstrings, CLI output and README are in Spanish;
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
finmcp web-pin                          # set the dashboard PIN (stores only a PBKDF2 hash in .env)
finmcp web --host 192.168.1.10          # web dashboard on :8790 (read-only DB, LAN-only, PIN; pull-down triggers a sync)
node --test tests/js/lib.test.js        # pure JS helpers of the dashboard UI (Node >= 18)
```

No linter or formatter is configured. CI (`.github/workflows/ci.yml`) runs `python -m pytest -q`
on Python 3.11–3.13 and the dashboard's JS tests with Node. `requires-python >= 3.11`; the code uses `X | None` unions and
`from __future__ import annotations` everywhere.

## Architecture

Data flows strictly one direction and each layer only talks to its neighbour:

```
provider client (HTTP)  ->  sync/service.run_sync  ->  SQLite (db/)  ->  analytics/  ->  mcp/server.py
```

- **Only `sync` and `list_institutions` MCP tools call the network**; the rest read/write SQLite
  (or local files for `import_csv`). `auth` is CLI-only by design (browser consent flow).
  Scheduled fetching happens via `finmcp sync` (manually or the launchd template in `deploy/`).
  The dashboard can also trigger one (pull-down gesture, see «Web dashboard»).
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
  by calling `apply_rules` once, after all banks, to recategorize. The whole run holds an
  exclusive `flock` on `data/sync.lock` (`sync/lock.py`): a second concurrent `run_sync` (CLI, MCP,
  cron or dashboard) raises `SyncInProgress` before touching any bank, and `finmcp sync` exits with
  code 75 (`EXIT_BUSY`). The OS releases the lock if the process dies. `tests/conftest.py` redirects
  the lock path to a temp dir for every test.
- **Categorization**: `Transaction.my_category` (user rules, `CategoryRule` table) always wins
  over `provider_category`; `analytics/categories.category_of` is the single place that encodes
  this precedence. Rules are case-insensitive substring matches on merchant/description/any,
  optionally scoped to one account (`CategoryRule.account_id`, nullable — `None` matches any
  account, the default/backward-compatible behavior), lowest `priority` wins;
  `finmcp rules add`/`update`/`remove` (tools
  `add_category_rule`/`update_category_rule`/`remove_category_rules`) manage them and every
  rule mutation reapplies `apply_rules` to the existing data. `--account`/`--bank` (CLI) resolve
  to `account_id`; `--bank` only resolves when that bank has exactly one local account.
  `repo.upsert_transaction` deliberately does not touch `my_category` so
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
- **Web dashboard** (`web/app.py`, `analytics/dashboard.py`, `analytics/ledger.py`, `web/static/`): Starlette app served by
  `finmcp web` on its own port (8790). Opens the DB through a separate read-only engine
  (`sqlite:///file:...?mode=ro&uri=true`) and exposes GET JSON endpoints (`/api/meta`,
  `/api/monthly[?day=D]`, `/api/breakdown`, `/api/series`, `/api/transactions`, and for the «Diario»
  tab `/api/accounts`, `/api/tags`, `/api/ledger`, `/api/movement`; `POST/GET /api/sync` is the only non-read action, below) plus a static UI.
  `analytics/ledger.py` («Diario», the landing tab) aggregates nothing and hides nothing.
  - It returns the latest balance per account (`current`, else `available`) with a freshness `state`: `warn` if the bank's last sync failed, `stale` if the snapshot is older than 36 h.
  - `ledger.accounts_total(accounts)` sums those balances (in cents, server-side like every figure; `/api/accounts` returns it as `total`). Accounts without a balance don't add (`missing`), stale/failed ones do but are listed in `outdated`, `disponible` names the banks whose figure is the available (not booked) balance, `state` is the worst of all accounts and `oldest` the oldest snapshot (compared as dates, not strings). `None` if no account has a balance or the currencies differ. The UI (`saldosBlock` → `totalRow`, `balanceCell`, `L.totalNote`) renders **one single card** (`.saldos`): the total on top (only with 2+ accounts; not a filter) and the accounts below in a two-column grid (`.bgrid`, one `.bcell` button per account). **The total carries no visible label** (the Señor asked to drop the word «Total»; the `role=group` keeps `aria-label="Total de todas las cuentas"`): a large (2 rem) left-aligned figure and, underneath, a small note line with the freshness light in front (`.btotal .bt`, light aligned to the first line). **There are no inner lines** (no gap/background trick between cells, no divider between the total and the grid; only the card's own border) so cells are told apart by spacing and the selected one by its own background + 2 px inset ring; because nothing paints a gap any more, an odd last cell is no longer stretched. **The section is deliberately compact** (the Señor asked for less vertical space): each cell is 3 lines (bank / figure with «saldo disponible» inline in `.bvrow` / `.bt` = IBAN tail-or-name · freshness), no `min-height`, and the total's note is short (`N cuentas · más antiguo: <when>` plus only the caveats that apply). ~230 px at 412 px width in the normal state (was 388 px as loose cards); warn/stale cells wrap their text and grow. The total's note wraps to two lines on any usual phone while the Revolut caveat applies (~350 px of text at .7 rem). Measure with `getBoundingClientRect` at 412/360/320 px, light and dark, and force warn/stale on a **copy** of the DB (a failed `sync_runs` row, an old `balances.snapshot_at`); measure a text's natural width with an out-of-flow clone, not `nowrap` on the flex item itself.
  - **Rolling figures** (`app.js::odometer`, `L.figureChars/odoTape/rollDelay`): each balance figure is rendered as an odometer. Each digit is a tape of 11 cells `0…9,d` inside an `overflow:hidden` column; the tape's *resting* transform (`translateY(-90.9091%)`) already shows the final digit, so a card that doesn't roll (or a browser without animations) always shows the right number. The animation (`.bgrid.roll .odo-tape`, keyframes `odo-roll`, 1.1 s) is switched on by `startRoll()` **once per page load**, the first time a `.bgrid` is painted by `render()` (not on filters, account selection, return from the detail or the post-sync reload; a first load on another tab rolls when Diario first appears); per-digit/per-card staggering goes through CSSOM (`style.animationDelay`, CSP-safe). `prefers-reduced-motion` disables it in CSS and in `startRoll`. Each figure is `role="img"` with `aria-label` = the formatted text (the tapes are `aria-hidden`), so `textContent` of `.bv` is not the figure: read the `aria-label`.
  - It lists every movement, neutral ones included, ordered by `booked_at` desc, then `id` desc, with keyset pagination (`cursor=<booked_at iso>|<id>`). Text search folds case and accents, so it runs in Python.
  - With `account`, it reconstructs each day's closing balance backwards from the latest snapshot. It checks every snapshot: the value must equal the previous day's close plus some subset of that day's movements.
  - It exposes the raw bank data (`raw_json`) in `/api/movement`.
  - **The only action: pull-down to reload or sync (Diario list view only).** The gesture has two zones, decided by where the finger is on release (`L.pullZone(pull)` over the damped distance `L.pullDistance(dy)`; thresholds in `L.PULL`): **medium** (`reload` ≤ pull < `sync`, ~200–370 px of finger, deliberately long so it never fires by accident; `damping` 0.28) → `location.reload()` (full native reload; revalidates JS/CSS, keeps the hash route; no bank traffic); **long** (≥ `sync`, ~370 px+) → the sync flow below; short → nothing. The indicator (`.ptr.pull|reload|sync|busy`) shows which one a release would do. `POST /api/sync` (JSON only, behind the PIN) starts `web/syncjob.py::SyncJob`, which spawns `python -m finmcp.cli sync --from <today-30d>` in a **separate process** (cwd = repo root for the `.env`; `PYTHONPATH`/`PYTHONHOME`/`VIRTUAL_ENV` stripped, like the `~/.local/bin/finmcp` wrapper). The web process keeps its read-only DB: the child writes. The job reads the child's `sync_runs` rows (id > the last one before launching) to build the summary (`outcome` ok/partial/error/busy/timeout, per-bank status, `new_transactions`); `GET /api/sync` polls it. One at a time (a second POST joins the running one). **Cooldown** `FINMCP_WEB_SYNC_COOLDOWN` (default 300 s) counts from the last finished run of *any* origin (max `sync_runs.finished_at`), because PSD2 caps bank accesses (429s are common); inside it the POST returns `started=false` + `retry_after` and the UI only reloads data. 300 s timeout kills the child. `/api/meta` carries `sync` (bool): when false the UI does not capture the gesture and `POST /api/sync` is a 404. Client: `app.js::setupPull` (touch events, non-passive `touchmove`; active only while the Diario *list* is on screen (`L.viewOf(parseHash) === "diario"`, which also covers empty/unknown/incomplete routes that render the list; NOT Mes, Evolución, Tendencias, `rango` lists or the `diario/mov/<id>` detail) — `render()` dispatches on `L.viewOf` and records what it actually painted with `setPullView(kind)` (empty while loading and on error/PIN screens; leaving the list switches it off immediately); ignored when `scrollY>0`, with a `.backdrop` sheet open, on `input`/`textarea`, with one touch only, if the move is horizontal or upward, or while syncing) + pure helpers in `lib.js` (`PULL`, `pullDistance`, `pullZone`, `waitLabel`, `syncResult`); `html.ptr-on { overscroll-behavior-y: contain }` is toggled by that same `setPullView` and set only when `sync` is true *and* the Diario list is showing, so the browser keeps its own pull-to-refresh everywhere else. On finish it runs `resetData(); boot({refreshing: true})`, which re-renders the current route; a 401 shows the PIN screen. Tests: `tests/test_web_sync.py` (real child process against a file DB), `tests/test_sync_lock.py`, JS in `tests/js/lib.test.js`.
  `analytics/dashboard.py` encodes the dashboard semantics: classification by category, not sign
  (income = categories starting with «Ingresos» plus «Otros ingresos»/«Intereses»), refunds
  subtracted from their category, `neutral` excluded, `net` equal to `monthly_summary`; panel
  «categoría» = prefix before « - ». `LanOnlyMiddleware` rejects non-private client IPs (Tailscale
  100.64/10 only when bound to it), `validate_hosts` refuses 0.0.0.0/public binds, uvicorn runs with
  `proxy_headers=False`. Optional PIN (`web/auth.py`, `FINMCP_WEB_PIN_HASH`): `PinMiddleware` requires
  a signed session cookie on `/api/*` (login/logout excepted); `POST /api/login` (JSON only,
  serialized, global exponential lockout after 5 failures) sets it; the session-signing secret lives
  in `data/web_session.key` (0600). The static UI is public (no data). Strict CSP (no inline scripts/styles: SVG styling via classes; element
  styles only through CSSOM). The UI is dependency-free vanilla JS: `lib.js` (pure, tested with
  `node --test`) and `app.js` (DOM, hash routes such as `#/mes/AAAA-MM/gastos`, `#/diario/<filters>`, `#/diario/mov/<id>`).
  - **Dark mode** (23-oct-2026): automatic via `prefers-color-scheme`, no UI needed. A ☀/☾ toggle in the
    header of Diario, Evolución and Tendencias (`topbar()`) force-cycles auto → dark → light → auto,
    storing the override in `localStorage['finmcp.theme']` and setting `<html data-theme>` (no server
    state; Mes and the movement detail have no header icon slot, so they follow whichever of the other
    three tabs was set last). `--accent`/`--accent2` stay the fixed brand teal (solid buttons, the hero
    gradient, white text on top); every other place accent appears as text/border on `--card`/`--bg`
    uses `--accent-text`/`--accent2-text`, which brighten in dark mode for contrast. `--good`/`--bad`/
    `--warn` are brightened too; `--track`/`--tick`/`--axis`/`--led-warn`/`--hero-shadow` replace small
    fixed hex values that used to ignore the theme; `--ink` is a theme-independent dark value kept only
    for the toast's neutral background (reusing `--fg` broke once it turns light in dark mode). Hand-drawn
    SVG chart colours (`barsChart`, `miniBars`, `spark`) are set via `setAttribute("fill"/"stroke", …)`,
    which doesn't track CSS `var()` reliably across browsers, so `app.js` reads the resolved colour with
    a `cssVar()` (`getComputedStyle`) helper at paint time; toggling the theme calls `render({keep:true})`
    to repaint them. The category colour palette (`lib.js::PALETTE`) is intentionally unchanged across
    themes. Pure cycle/resolution helpers (`L.nextTheme`, `L.isDarkTheme`) are covered in `lib.test.js`.
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
