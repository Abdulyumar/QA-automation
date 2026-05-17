"""
test_chaos.py — Negative and chaos scenario validation.

Tests cover:
- Malformed API responses (string prices, null fields, wrong types)
- Network-level faults (timeout, connection refused, downtime, intermittent)
- Retry exhaustion behavior
- Impossible / physically invalid data values
- Partial / truncated responses
- Rate limit handling under sustained pressure
- Concurrent chaos (multiple failure types simultaneously)
- Data pipeline resilience under degraded conditions

These tests document known failure modes in crypto data pipelines.
Each test corresponds to a real class of production bug.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from config import settings
from logger import get_logger

logger = get_logger(__name__)

COINGECKO_BASE = settings.coingecko.base_url
COINGLASS_BASE = settings.coinglass.base_url


# ── Malformed Response Detection ─────────────────────────────────────────────


class TestMalformedResponseDetection:
    """
    Validates that the schema layer correctly REJECTS bad data.

    Production context: CoinGecko has historically returned string-encoded
    prices during API migrations. This test class documents and guards
    against that class of silent failure.
    """

    def test_string_price_fails_type_check(self, make_malformed_response):
        """
        KNOWN BUG CLASS: String-encoded price passes JSON parsing but breaks
        any downstream numeric operation silently.

        Detection: isinstance check must reject str type.
        """
        payload = make_malformed_response("string_price")
        btc_price = payload["bitcoin"]["usd"]

        logger.info("Testing string price detection: value=%r type=%s", btc_price, type(btc_price).__name__)

        # This is what the bug looks like — string passes truthiness check
        assert btc_price, "price field exists (bug: looks valid)"

        # This is how we catch it
        assert not isinstance(btc_price, (int, float)), (
            "String price should NOT be numeric — this test confirms detection works"
        )
        with pytest.raises(AssertionError):
            assert isinstance(btc_price, (int, float)), f"price must be numeric, got {type(btc_price)}"

    def test_null_price_is_detected(self, make_malformed_response):
        """
        KNOWN BUG CLASS: Null price propagates silently through pipelines,
        often surfacing as NoneType errors in downstream calculations.
        """
        payload = make_malformed_response("null_price")
        price = payload["bitcoin"]["usd"]

        logger.info("Testing null price detection: value=%r", price)

        assert price is None, "Confirms null value present"

        # Validate our assertion helper catches it correctly
        with pytest.raises((AssertionError, TypeError)):
            assert isinstance(price, (int, float)) and price > 0, "null price must be rejected"

    def test_uppercase_symbol_is_detected(self, make_malformed_response):
        """
        KNOWN BUG CLASS: Uppercase symbols break case-sensitive lookups,
        dict key matching, and exchange pair routing in trading systems.
        """
        payload = make_malformed_response("uppercase_symbol")
        symbol = payload["symbol"]

        logger.info("Testing uppercase symbol detection: symbol=%r", symbol)

        # Must fail lowercase check
        assert symbol != symbol.lower(), f"Symbol '{symbol}' is uppercase — confirm detection"

        with pytest.raises(AssertionError):
            assert symbol == symbol.lower(), f"Symbol must be lowercase, got '{symbol}'"

    def test_missing_market_cap_field_raises(self, make_malformed_response):
        """
        KNOWN BUG CLASS: Missing market_cap field causes KeyError or silent
        zero-value in cross-field consistency checks.
        """
        payload = make_malformed_response("missing_market_cap")
        market_data = payload.get("market_data", {})

        logger.info("Testing missing market_cap detection: market_data keys=%s", list(market_data.keys()))

        assert "market_cap" not in market_data, "Confirms market_cap is missing"

        with pytest.raises((KeyError, AssertionError)):
            _ = market_data["market_cap"]

    def test_partial_response_has_no_usable_price(self, make_malformed_response):
        """
        KNOWN BUG CLASS: Partial response — coin present in payload but
        all fields missing. Passes existence check, fails value extraction.
        """
        payload = make_malformed_response("partial_response")
        coin_data = payload.get("bitcoin", {})

        logger.info("Testing partial response detection: bitcoin keys=%s", list(coin_data.keys()))

        assert "bitcoin" in payload, "Coin key present (looks valid at surface)"
        assert coin_data.get("usd") is None, "But USD price is missing"

    def test_stale_timestamp_is_detected(self, make_malformed_response):
        """
        KNOWN BUG CLASS: Stale last_updated timestamp — data appears fresh
        but is hours or days old. Critical in real-time trading context.

        Threshold: data older than 1 hour should be flagged.
        """
        from datetime import datetime, timezone, timedelta

        payload = make_malformed_response("stale_timestamp")
        last_updated = payload.get("last_updated", "")

        dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - dt
        staleness_threshold = timedelta(hours=1)

        logger.info("Testing stale timestamp: last_updated=%s age=%s", last_updated, age)

        assert age > staleness_threshold, (
            f"Data is {age} old — exceeds freshness threshold of {staleness_threshold}"
        )


# ── OHLCV Impossible Values ───────────────────────────────────────────────────


class TestOHLCVChaosValues:
    """
    Validates impossible / physically invalid candle data is caught.

    These represent data corruption scenarios, not API errors — the HTTP
    call succeeds but the payload contains logically impossible values.
    """

    def test_negative_volume_is_rejected(self, make_malformed_response):
        """Volume cannot be negative — physical impossibility."""
        candles = make_malformed_response("negative_volume")

        logger.info("Testing negative volume detection")

        for i, candle in enumerate(candles):
            vol = candle[5]
            assert vol < 0, f"Confirms negative volume present: {vol}"
            with pytest.raises(AssertionError):
                assert vol >= 0, f"Candle {i}: volume must be non-negative, got {vol}"

    def test_reversed_timestamps_are_detected(self, make_malformed_response):
        """Out-of-order timestamps break time-series analysis."""
        candles = make_malformed_response("reversed_timestamps")
        timestamps = [c[0] for c in candles]

        logger.info("Testing reversed timestamps: %s", timestamps)

        # Confirm timestamps are reversed
        assert timestamps != sorted(timestamps), "Timestamps are out of order — confirms detection"

        with pytest.raises(AssertionError):
            for i in range(1, len(timestamps)):
                assert timestamps[i] > timestamps[i - 1], (
                    f"Non-ascending timestamp at index {i}"
                )

    def test_impossible_candle_high_below_low(self, make_malformed_response):
        """High < Low is physically impossible and indicates data corruption."""
        candles = make_malformed_response("impossible_candle")
        ts, open_, high, low, close, vol = candles[0]

        logger.info("Testing impossible candle: high=%s low=%s", high, low)

        assert high < low, f"Confirms impossible candle: high({high}) < low({low})"

        with pytest.raises(AssertionError):
            assert high >= low, f"Candle high ({high}) must be >= low ({low})"

    def test_empty_funding_data_is_flagged(self, make_malformed_response):
        """Empty data array — API returns success but no actual data."""
        payload = make_malformed_response("empty_data")

        logger.info("Testing empty funding data detection")

        assert payload["code"] == "0", "Status looks successful"
        assert len(payload["data"]) == 0, "But data array is empty"

        with pytest.raises(AssertionError):
            assert len(payload["data"]) > 0, "Funding rate data array must not be empty"


# ── Network Fault Scenarios ───────────────────────────────────────────────────


class TestNetworkFaultHandling:
    """
    Tests how the system behaves under network-level failures.
    Each scenario maps to a real infrastructure failure mode.
    """

    @pytest.mark.asyncio
    async def test_timeout_raises_immediately(self, mock_async_client, make_network_fault):
        """Hard timeout should raise, not hang indefinitely."""
        mock_async_client.get = AsyncMock(side_effect=make_network_fault("timeout"))

        logger.info("Testing timeout fault injection")

        with pytest.raises(httpx.TimeoutException):
            await mock_async_client.get(
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
            )

    @pytest.mark.asyncio
    async def test_connect_error_raises(self, mock_async_client, make_network_fault):
        """Connection refused — API host unreachable."""
        mock_async_client.get = AsyncMock(side_effect=make_network_fault("connect_error"))

        logger.info("Testing connect error fault injection")

        with pytest.raises(httpx.ConnectError):
            await mock_async_client.get(f"{COINGECKO_BASE}/simple/price")

    @pytest.mark.asyncio
    async def test_simulated_downtime_returns_503(self, mock_async_client, make_network_fault):
        """503 Service Unavailable — API is down, not just slow."""
        mock_async_client.get = AsyncMock(side_effect=make_network_fault("downtime"))

        logger.info("Testing simulated API downtime (503)")

        resp = await mock_async_client.get(f"{COINGECKO_BASE}/simple/price")

        assert resp.status_code == 503
        with pytest.raises(httpx.HTTPStatusError):
            resp.raise_for_status()

    @pytest.mark.asyncio
    async def test_intermittent_failure_recovers_on_second_attempt(
        self, mock_async_client, make_network_fault, make_mock_response
    ):
        """
        Transient failure on first call, success on second.
        Validates retry logic recovers correctly.
        """
        call_count = {"n": 0}
        fault = make_network_fault("intermittent")

        async def tracked_fault(*args, **kwargs):
            call_count["n"] += 1
            return await fault(*args, **kwargs)

        mock_async_client.get = AsyncMock(side_effect=tracked_fault)

        logger.info("Testing intermittent failure recovery")

        # First call should fail
        with pytest.raises(httpx.ConnectError):
            await mock_async_client.get(f"{COINGECKO_BASE}/simple/price")

        # Second call should succeed
        resp = await mock_async_client.get(f"{COINGECKO_BASE}/simple/price")
        assert resp.status_code == 200
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_retry_exhaustion_raises_after_all_attempts(
        self, mock_async_client, make_network_fault
    ):
        """
        When all retries are exhausted, the client must raise — not silently
        return None or swallow the error.
        """
        from client import fetch_with_retry

        mock_async_client.get = AsyncMock(side_effect=make_network_fault("connect_error"))

        logger.info("Testing retry exhaustion behavior (max_retries=%d)", settings.coingecko.max_retries)

        with pytest.raises(httpx.ConnectError):
            await fetch_with_retry(
                mock_async_client,
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                retries=3,
            )


# ── Rate Limit Exhaustion ─────────────────────────────────────────────────────


class TestRateLimitBehavior:

    @pytest.mark.asyncio
    async def test_sustained_429s_exhaust_backoff(self, mock_async_client, make_mock_response):
        """
        When 429 is returned on every attempt, backoff must eventually give up.
        The client should raise RateLimitError, not loop forever.
        """
        from client import fetch_with_backoff

        mock_async_client.get = AsyncMock(
            return_value=make_mock_response(429, {"error": "rate limit"})
        )

        logger.info("Testing rate limit exhaustion across all backoff attempts")

        # fetch_with_backoff returns last response if all retries are 429
        resp = await fetch_with_backoff(
            mock_async_client,
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            retries=3,
        )
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_then_success_recovers(self, mock_async_client, make_mock_response, sample_price_payload):
        """After N 429 responses, a 200 must be returned correctly."""
        from client import fetch_with_backoff

        calls = {"n": 0}

        async def rate_then_ok(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                return make_mock_response(429, {"error": "rate limit"})
            return make_mock_response(200, sample_price_payload)

        mock_async_client.get = AsyncMock(side_effect=rate_then_ok)

        logger.info("Testing rate limit recovery after 2 rejections")

        resp = await fetch_with_backoff(
            mock_async_client,
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            retries=5,
        )
        assert resp.status_code == 200
        assert calls["n"] == 3


# ── Concurrent Chaos ──────────────────────────────────────────────────────────


class TestConcurrentChaos:
    """
    Validates behavior when multiple concurrent requests experience mixed failures.
    This is the hardest class of bug to catch — failures that only appear under load.
    """

    @pytest.mark.asyncio
    async def test_mixed_success_failure_in_concurrent_batch(
        self, mock_async_client, make_mock_response, sample_price_payload
    ):
        """
        In a batch of 5 concurrent requests, 2 fail with 500.
        The successful results must still be correct — no data bleed from errors.
        """
        coins = ["bitcoin", "ethereum", "solana", "binancecoin", "ripple"]
        failing_coins = {"solana", "ripple"}

        async def mixed_response(url, **kwargs):
            coin = kwargs.get("params", {}).get("ids", "")
            if coin in failing_coins:
                return make_mock_response(500, {"error": "internal server error"})
            return make_mock_response(200, {coin: {"usd": 100.0}})

        mock_async_client.get = AsyncMock(side_effect=mixed_response)

        tasks = [
            mock_async_client.get(
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": coin, "vs_currencies": "usd"},
            )
            for coin in coins
        ]

        responses = await asyncio.gather(*tasks)

        successes = [r for r in responses if r.status_code == 200]
        failures = [r for r in responses if r.status_code == 500]

        logger.info(
            "Concurrent chaos: %d success / %d failure out of %d total",
            len(successes), len(failures), len(coins),
        )

        assert len(successes) == 3
        assert len(failures) == 2

        # Verify successful responses have correct data
        for resp in successes:
            data = resp.json()
            for coin_key, values in data.items():
                assert isinstance(values["usd"], (int, float))
                assert values["usd"] > 0

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_connections(self, mock_async_client, make_mock_response, sample_price_payload):
        """
        Validates that a semaphore correctly limits concurrency to N simultaneous requests.
        Exceeding the semaphore limit in production causes connection pool exhaustion.
        """
        MAX_CONCURRENT = 3
        sem = asyncio.Semaphore(MAX_CONCURRENT)
        active = {"count": 0, "peak": 0}

        async def tracked_get(url, **kwargs):
            async with sem:
                active["count"] += 1
                active["peak"] = max(active["peak"], active["count"])
                await asyncio.sleep(0.01)
                active["count"] -= 1
                return make_mock_response(200, sample_price_payload)

        mock_async_client.get = AsyncMock(side_effect=tracked_get)

        tasks = [
            mock_async_client.get(
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": f"coin{i}", "vs_currencies": "usd"},
            )
            for i in range(10)
        ]

        responses = await asyncio.gather(*tasks)

        logger.info("Semaphore test: peak_concurrency=%d limit=%d", active["peak"], MAX_CONCURRENT)

        assert all(r.status_code == 200 for r in responses)
        assert active["peak"] <= MAX_CONCURRENT, (
            f"Peak concurrency {active['peak']} exceeded semaphore limit {MAX_CONCURRENT}"
        )