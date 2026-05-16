# exchange-api-qa

**Production-grade QA automation suite for crypto exchange APIs.**

Built to validate real-time market data pipelines across REST endpoints, async feeds, and streaming data sources
 the same patterns used in live exchange environments.

---

## What This Tests

| Module | Coverage |
|---|---|
| `test_market_data.py` | Price endpoints, OHLCV candles, coin detail responses |
| `test_async_feeds.py` | Concurrent fetches, timeout/retry logic, funding rate feeds, data freshness |
| `test_schema_validation.py` | Field types, null checks, cross-field consistency, regression guards |

**APIs covered:** CoinGecko, CoinGlass (funding rates), OHLCV feeds

---

## Project Structure

```
exchange-api-qa/
├── .github/workflows/ci.yml     ← CI pipeline (runs on push + every 6h)
├── tests/
│   ├── conftest.py              ← Shared fixtures, mock factories, helpers
│   ├── test_market_data.py      ← Price & OHLCV endpoint tests
│   ├── test_async_feeds.py      ← Async, concurrent, and real-time feed tests
│   └── test_schema_validation.py← Response structure & data integrity tests
├── requirements.txt
└── README.md
```

---

## Running Tests

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Run full suite:**
```bash
pytest tests/ -v --asyncio-mode=auto
```

**Run with coverage report:**
```bash
pytest tests/ --cov=tests --cov-report=term-missing --asyncio-mode=auto
```

**Run a specific module:**
```bash
pytest tests/test_schema_validation.py -v
pytest tests/test_async_feeds.py -v --asyncio-mode=auto
```

**Run against live APIs** (requires API keys):
```bash
LIVE_TESTS=1 COINGECKO_API_KEY=your_key pytest tests/ -v --asyncio-mode=auto
```

---

## CI/CD Pipeline

Tests run automatically on:
- Every push to `main` or `develop`
- Every pull request targeting `main`
- Scheduled every 6 hours against live API endpoints

The pipeline tests against Python 3.10 and 3.11 in parallel. Test reports are uploaded as artifacts on every run.

---

## Design Decisions

**Mock-first, live-optional:** All tests run fully offline using `httpx` mocks. Set `LIVE_TESTS=1` to hit real endpoints. This keeps CI fast while allowing real validation on demand.

**Concurrency testing:** `test_async_feeds.py` explicitly tests semaphore limiting, race conditions, and data bleed across parallel requests — common failure modes in high-throughput crypto pipelines.

**Regression guards:** Schema tests include named regression cases for known past failures (string-encoded prices, uppercase symbols, stale timestamps).

**Cross-field consistency:** Market cap is validated against `price × circulating_supply` with a 10% tolerance, catching cases where different fields are sourced from different API snapshots.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `LIVE_TESTS` | Set to `1` to run against real APIs | `0` |
| `COINGECKO_API_KEY` | CoinGecko API key for authenticated requests | *(empty)* |
| `COINGLASS_API_KEY` | CoinGlass API key | *(empty)* |

---

## Background

This suite reflects testing patterns applied during production work on crypto exchange infrastructure validating multi-API async pipelines, catching silent data failures, and ensuring output correctness before release.