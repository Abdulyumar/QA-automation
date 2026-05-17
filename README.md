# crypto-market-data-qa

QA automation framework for validating crypto market data pipelines across REST endpoints, async feeds, and streaming exchange APIs.

Built to ensure accuracy, consistency, and reliability of real-time financial data in production-like crypto environments — with a focus on catching the silent failures that unit tests miss.



## Why This Exists

Crypto data pipelines fail in ways that don't raise HTTP errors:

- A price field returns `"65000.00"` (string) instead of `65000.0` (float) — downstream math silently breaks
- A timestamp is 4 hours stale — trades execute on bad data
- A coin symbol arrives as `BTC` instead of `btc` — pair routing fails with a KeyError
- A concurrent batch of 10 requests bleeds data between responses under load
- A funding rate feed returns `{"code": "0", "data": []}` — success status, zero data, no error

This framework was built to catch all of these before they reach production.



## What This Tests

| Module | Coverage |
|---|---|
| `test_market_data.py` | Price endpoints, OHLCV candles, coin detail responses |
| `test_async_feeds.py` | Concurrent fetches, timeout/retry logic, funding rate feeds, data freshness |
| `test_schema_validation.py` | Field types, null checks, cross-field consistency, regression guards |
| `test_chaos.py` | Malformed responses, network faults, retry exhaustion, concurrent chaos |

APIs covered: CoinGecko, CoinGlass (funding rates), OHLCV feeds



## Framework Structure

```
crypto-market-data-qa/
├── .github/workflows/ci.yml     ← CI pipeline (mock + chaos + live stages)
├── tests/
│   ├── conftest.py              ← Fixtures, mock factories, chaos injectors
│   ├── test_market_data.py      ← Price & OHLCV endpoint tests
│   ├── test_async_feeds.py      ← Async, concurrent, and real-time feed tests
│   ├── test_schema_validation.py← Response structure & data integrity tests
│   └── test_chaos.py            ← Negative scenarios & fault injection
├── config.py                    ← Centralized env-based config (no scattered os.getenv)
├── client.py                    ← Reusable async API client with retry/backoff
├── logger.py                    ← Structured logging (console + JSON file output)
├── reports/                     ← Auto-generated HTML + Allure reports
├── logs/                        ← Structured JSON logs per test session
├── requirements.txt
├── pytest.ini                   ← Reporting config, markers, log capture
└── README.md
```



## Running Tests

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Run full suite (mock, offline):**
```bash
pytest tests/ -v --asyncio-mode=auto
```

**Run with coverage report:**
```bash
pytest tests/ --cov=tests --cov-report=term-missing --asyncio-mode=auto
```

**Run chaos/negative tests only:**
```bash
pytest tests/test_chaos.py -v
```

**Run with HTML report:**
```bash
pytest tests/ --html=reports/test_report.html --self-contained-html
```

**Run with Allure report** (requires [allure CLI](https://allurereport.org/docs/install/)):
```bash
pytest tests/ --alluredir=reports/allure-results
allure serve reports/allure-results
```

**Run against live APIs** (requires API keys):
```bash
LIVE_TESTS=1 COINGECKO_API_KEY=your_key pytest tests/ -v --asyncio-mode=auto
```



## CI/CD Pipeline

Tests run automatically on:

- Every push to `main` or `develop`
- Every pull request targeting `main`
- Scheduled every 6 hours against live API endpoints

The pipeline has three stages:

1. **Mock test suite** — runs fully offline, fast, covers all modules
2. **Chaos test stage** — dedicated fault injection run logged separately
3. **Live API validation** — scheduled only, requires secrets

Test reports (HTML), Allure results, coverage XML, and structured logs are uploaded as CI artifacts on every run.



## Testing Approach & Design Decisions

**Mock-first, live-optional:** All tests run fully offline using httpx mocks. Set `LIVE_TESTS=1` to run real API validation. This keeps CI fast while allowing real validation when needed.

**Framework layer, not just a test folder:** `config.py` centralizes all env vars and thresholds. `client.py` provides a reusable async client with built-in retry/backoff. `logger.py` writes structured JSON logs that CI ingests as artifacts. Test files consume these — they don't duplicate config or logic.

**Concurrency testing:** `test_async_feeds.py` validates semaphore limits, race conditions, and data bleed across parallel requests — common failure modes in high-throughput crypto data pipelines.

**Chaos + negative scenarios:** `test_chaos.py` covers string-encoded prices, null fields, uppercase symbols, impossible candles, network faults, rate limit exhaustion, and concurrent mixed failures. Each test maps to a documented real-world bug class.

**Regression guards:** Schema tests include known failure patterns — string prices, uppercase symbols, stale timestamps — as named regression cases that will catch recurrence.

**Cross-field consistency:** Market cap is validated against `price × circulating_supply` with a 10% tolerance to detect inconsistencies between data sources.



## Bug Classes This Framework Detects

These are documented failure patterns caught during development and during production pipeline work. Each maps to a test case.

### 1. String-encoded price (`test_chaos.py::TestMalformedResponseDetection::test_string_price_fails_type_check`)

**What happens:** API returns `{"bitcoin": {"usd": "65000.00"}}`. Passes JSON parsing and truthiness checks. Fails silently when used in arithmetic.

**Detection:** `isinstance(price, (int, float))` check on every price field.

**Example failure output:**
```
FAILED tests/test_chaos.py::TestMalformedResponseDetection::test_string_price_fails_type_check
AssertionError: price must be numeric, got <class 'str'>
```



### 2. Stale timestamp (`test_chaos.py::TestMalformedResponseDetection::test_stale_timestamp_is_detected`)

**What happens:** `last_updated` is hours or days old. The API returns 200 with valid-looking data that reflects an old market state. In a live trading context, this causes decisions on stale prices.

**Detection:** Parse `last_updated` as ISO 8601, compare against `datetime.now(UTC)` with a 1-hour threshold.

**Example failure output:**
```
FAILED tests/test_chaos.py::TestMalformedResponseDetection::test_stale_timestamp_is_detected
AssertionError: Data is 4 years, 15 days old — exceeds freshness threshold of 1:00:00
```



### 3. Uppercase symbol (`test_chaos.py::TestMalformedResponseDetection::test_uppercase_symbol_is_detected`)

**What happens:** Symbol field returns `"BTC"` instead of `"btc"`. Breaks case-sensitive dict lookups, exchange pair routing, and downstream symbol matching.

**Detection:** `assert symbol == symbol.lower()`

**Example failure output:**
```
FAILED tests/test_chaos.py::TestMalformedResponseDetection::test_uppercase_symbol_is_detected
AssertionError: Symbol must be lowercase, got 'BTC'
```



### 4. Impossible OHLCV candle (`test_chaos.py::TestOHLCVChaosValues::test_impossible_candle_high_below_low`)

**What happens:** Data pipeline returns a candle where `high < low` — physically impossible. Indicates upstream data corruption or a join/merge error between data sources.

**Detection:** `assert high >= low` on every candle.

**Example failure output:**
```
FAILED tests/test_chaos.py::TestOHLCVChaosValues::test_impossible_candle_high_below_low
AssertionError: Candle high (64000.0) must be >= low (65500.0)
```



### 5. Empty funding rate feed (`test_chaos.py::TestOHLCVChaosValues::test_empty_funding_data_is_flagged`)

**What happens:** API returns `{"code": "0", "data": []}` — a success response with no actual data. Silent in any pipeline that only checks status codes.

**Detection:** `assert len(data["data"]) > 0`

**Example failure output:**
```
FAILED tests/test_chaos.py::TestOHLCVChaosValues::test_empty_funding_data_is_flagged
AssertionError: Funding rate data array must not be empty
```



### 6. Semaphore violation under load (`test_chaos.py::TestConcurrentChaos::test_semaphore_limits_concurrent_connections`)

**What happens:** Under 10 concurrent requests, the connection pool allows more simultaneous connections than the semaphore limit. Causes pool exhaustion in production.

**Detection:** Track peak concurrency across `asyncio.gather` tasks, assert `peak <= MAX_CONCURRENT`.

**Example failure output:**
```
FAILED tests/test_chaos.py::TestConcurrentChaos::test_semaphore_limits_concurrent_connections
AssertionError: Peak concurrency 7 exceeded semaphore limit 3
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `LIVE_TESTS` | Set to `1` to run against real APIs | `0` |
| `COINGECKO_API_KEY` | CoinGecko API key | (empty) |
| `COINGLASS_API_KEY` | CoinGlass API key | (empty) |
| `LOG_LEVEL` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`) | `INFO` |
| `REQUEST_TIMEOUT_SECONDS` | HTTP client timeout | `15` |
| `MAX_RETRIES` | Retry attempts on transient failure | `3` |
| `BACKOFF_FACTOR` | Backoff multiplier for retry delays | `0.5` |
| `CHAOS_TESTS` | Set to `1` to enable additional randomized chaos | `0` |



## Background

This suite reflects testing patterns applied during production work on crypto exchange infrastructure — validating multi-API async pipelines, catching silent data failures, and ensuring output correctness before release.

The chaos scenarios in `test_chaos.py` document real failure modes observed when building and maintaining crypto market data systems using CoinGecko, CoinGlass, and exchange-native OHLCV feeds.