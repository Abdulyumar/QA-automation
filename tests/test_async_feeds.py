"""
test_async_feeds.py — Async real-time data pipeline validation.
"""

import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGLASS_BASE = "https://open-api.coinglass.com/public/v2"


async def fetch_with_retry(client, url, params, retries=3):
    for attempt in range(retries):
        try:
            return await client.get(url, params=params)
        except (httpx.ConnectError, httpx.TimeoutException):
            if attempt == retries - 1:
                raise
            await asyncio.sleep(0.01 * (attempt + 1))


async def fetch_with_backoff(client, url, params, retries=5):
    for attempt in range(retries):
        resp = await client.get(url, params=params)
        if resp.status_code == 429:
            await asyncio.sleep(0.01 * (attempt + 1))
            continue
        return resp
    return resp


class TestConcurrentFetches:

    @pytest.mark.asyncio
    async def test_concurrent_price_fetches_return_all_results(
        self, mock_async_client, make_mock_response
    ):
        coins = ["bitcoin", "ethereum", "solana", "binancecoin"]

        payloads = {
            coin: {coin: {"usd": 100.0 * (i + 1)}}
            for i, coin in enumerate(coins)
        }

        async def fake_get(url, **kwargs):
            coin_id = kwargs.get("params", {}).get("ids", "bitcoin")
            return make_mock_response(200, payloads.get(coin_id, {}))

        mock_async_client.get = AsyncMock(side_effect=fake_get)

        tasks = [
            mock_async_client.get(
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": coin, "vs_currencies": "usd"}
            )
            for coin in coins
        ]

        responses = await asyncio.gather(*tasks)

        assert len(responses) == len(coins)

        for resp in responses:
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_concurrent_fetches_no_data_bleed(
        self, mock_async_client, make_mock_response
    ):
        coin_prices = {
            "bitcoin": 65000.0,
            "ethereum": 3200.0,
            "solana": 150.0
        }

        async def fake_get(url, **kwargs):
            coin = kwargs.get("params", {}).get("ids", "")
            return make_mock_response(200, {coin: {"usd": coin_prices.get(coin, 0)}})

        mock_async_client.get = AsyncMock(side_effect=fake_get)

        tasks = [
            mock_async_client.get(
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": coin, "vs_currencies": "usd"}
            )
            for coin in coin_prices
        ]

        responses = await asyncio.gather(*tasks)

        for coin, resp in zip(coin_prices.keys(), responses):
            data = resp.json()
            assert coin in data
            assert data[coin]["usd"] == coin_prices[coin]


class TestTimeoutAndRetry:

    @pytest.mark.asyncio
    async def test_request_raises_on_timeout(self, mock_async_client):
        mock_async_client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        with pytest.raises(httpx.TimeoutException):
            await mock_async_client.get(
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"}
            )

    @pytest.mark.asyncio
    async def test_retry_logic_succeeds_after_transient_failure(
        self, mock_async_client, make_mock_response, sample_price_payload
    ):
        calls = 0

        async def flaky(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("fail")
            return make_mock_response(200, sample_price_payload)

        mock_async_client.get = AsyncMock(side_effect=flaky)

        resp = await fetch_with_retry(
            mock_async_client,
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"}
        )

        assert resp.status_code == 200
        assert calls == 2

    @pytest.mark.asyncio
    async def test_429_rate_limit_triggers_backoff(
        self, mock_async_client, make_mock_response, sample_price_payload
    ):
        calls = 0

        async def rate_limited(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                return make_mock_response(429, {"error": "rate limit"})
            return make_mock_response(200, sample_price_payload)

        mock_async_client.get = AsyncMock(side_effect=rate_limited)

        resp = await fetch_with_backoff(
            mock_async_client,
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"}
        )

        assert resp.status_code == 200
        assert calls == 3


class TestFundingRateFeed:

    @pytest.mark.asyncio
    async def test_async_funding_rate_fetch(
        self, mock_async_client, make_mock_response, sample_funding_rate_payload
    ):
        mock_async_client.get = AsyncMock(
            return_value=make_mock_response(200, sample_funding_rate_payload)
        )

        response = await mock_async_client.get(
            f"{COINGLASS_BASE}/funding_rate",
            params={"symbol": "BTC"}
        )

        assert response.status_code == 200
        assert response.json()["code"] == "0"


class TestDataFreshness:

    @pytest.mark.asyncio
    async def test_pipeline_response_time_under_threshold(
        self, mock_async_client, make_mock_response, sample_price_payload
    ):
        async def delayed(*args, **kwargs):
            await asyncio.sleep(0.05)
            return make_mock_response(200, sample_price_payload)

        mock_async_client.get = AsyncMock(side_effect=delayed)

        start = time.monotonic()

        response = await mock_async_client.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"}
        )

        elapsed_ms = (time.monotonic() - start) * 1000

        assert response.status_code == 200
        assert elapsed_ms < 500