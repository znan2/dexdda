# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal, local-only dashboard comparing "buy on DEX (OKX Onchain OS aggregator) → sell on Upbit/Bithumb" opportunities; milestones M0–M7 are implemented.
Docs, comments, and UI strings are in Korean; code identifiers are English.

## 필요한 문서

- 요청 흐름·레이어 구조를 바꾸거나 새 모듈을 추가하면 docs/architecture.md를 먼저 읽는다.
- 동작을 바꾸기 전에 DECISIONS.md의 관련 날짜 섹션을 읽고, 설계 결정을 하면 거기에 추가한다.
- 마일스톤별 검증 절차는 docs/ 아래 해당 문서를 읽는다.

## Commands

```sh
uv sync --locked                      # Python >=3.12, uv
uv run dexdda                         # server at http://127.0.0.1:8000 (Ctrl+C to stop)
uv run scripts/smoke_test.py [--json] [--skip-rpc]   # M0 read-only connection check; exit 0/1/2/130
uv run scripts/build_registry.py      # regenerate data/registry.json + registry_report.md (live APIs or --sources/--metadata snapshots)
uv run scripts/check_prices.py --seconds 35          # short live M2 check; don't run alongside the server
uv run scripts/sync_deposit_addresses.py             # prepare exchange deposit addresses (never moves funds)
uv run scripts/build_static.py        # rebuild dist/ (backend-free demo, synthetic data, never signs)
node scripts/check_static_ui.cjs [--shots DIR]       # serve dist/ with _headers; 30s zero-external-request check

uv run pytest -q                      # all tests (asyncio_mode=auto)
uv run pytest tests/test_swap.py -q   # one file
uv run pytest tests/test_swap.py -k "name" -q        # one test
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts         # line-length 100
```

Browser checks (`node scripts/check_*_ui.cjs`) need Node, Playwright, and installed Chrome. Without `--live` they run against the offline fixture apps in `tests/*_ui_server.py` (no credentials, no network); `--live` uses `app.main:app` and real APIs.

## Test rules

Tests must never read `.env` or hit real APIs: use `tests/fixtures/responses.json` (synthetic, schema-shaped) and `httpx.MockTransport`. `tests/conftest.py` provides `responses` and `credentials` (sentinel values) fixtures.

## Invariants to preserve

- Read-only by default: `DRY_RUN=true` blocks every signature/broadcast including `approve`. Only the user flips it, and only in `.env`.
- Secrets (keys, JWTs, signatures, full RPC URLs, raw responses/exceptions) must not appear in logs, diagnostics, API JSON, tests, or docs. Sanitize by whitelisting fields, not by regex-redacting.
- Exchange trading fees are excluded from gap math; bridge candidates are display-only.
- Execution is limited to chains whose cost model is verified (Ethereum, Polygon, Avalanche C); do not assume zero cost on other listed chains.
- Money values travel as `Decimal`/decimal strings (`decimal_text`), never floats.
- Never overwrite an existing `.env`; `.env.example` is the template.
- API reads never make external requests; they return the latest in-memory snapshot only.
- Swap order is user confirmation → DRY_RUN guard → allowance reset → approve → swap; `/api/swap/confirm` accepts only a server-created intent ID (client cannot alter calldata/amount/nonce).
- tx hash/nonce is persisted atomically **before** broadcast and there is no automatic replay.
