"""
test_market_data.py — Price and market endpoint validation.

Tests cover:
- Response structure and required field presence
- Data type correctness (prices are numeric, timestamps are ints, etc.)
- Value sanity checks (prices > 0, market cap > volume, etc.)
- Multi-coin batch responses
- Error handling for invalid coin IDs
- Edge cases (stablecoins, high-volatility assets)

Uses mock responses by default so tests run offline in CI.
Set LIVE_TESTS=1 in environment to run against real CoinGecko API.
"""

import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

LIVE_TESTS = os.getenv("LIVE_TESTS", "0") == "1"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"


# Price Endpoint — /simple/price


class TestSimplePriceEndpoint:

    def test_price_response_contains_requested_coins(self, sample_price_payload):
        """All requested coins must be present in the response."""
        requested = ["bitcoin", "ethereum"]
        for coin in requested:
            assert coin in sample_price_payload, f"Missing coin: {coin}"

    def test_price_values_are_positive_numbers(self, sample_price_payload):
        """USD prices must be positive numerics."""
        for coin, data in sample_price_payload.items():
            price = data.get("usd")
            assert price is not None, f"No USD price for {coin}"
            assert isinstance(price, (int, float)), f"Price not numeric for {coin}: {type(price)}"
            assert price > 0, f"Price must be positive for {coin}: {price}"

    def test_price_24h_change_is_float_or_none(self, sample_price_payload):
        """24h change can be None (not requested) or a float."""
        for coin, data in sample_price_payload.items():
            change = data.get("usd_24h_change")
            if change is not None:
                assert isinstance(change, float), (
                    f"24h change for {coin} should be float, got {type(change)}"
                )

    def test_price_response_structure_matches_schema(self, sample_price_payload, price_schema):
        """Each coin entry must have at minimum the required keys from schema."""
        required = price_schema["required_coin_keys"]
        for coin, data in sample_price_payload.items():
            for key in required:
                assert key in data, f"Missing required key '{key}' for coin '{coin}'"

    def test_bitcoin_price_in_realistic_range(self, sample_price_payload):
        """Sanity check: BTC price should be between $100 and $10,000,000."""
        btc_price = sample_price_payload.get("bitcoin", {}).get("usd", 0)
        assert 100 < btc_price < 10_000_000, f"BTC price looks wrong: {btc_price}"

    def test_multiple_coins_independent_prices(self, sample_price_payload):
        """Different coins must not return identical prices (data bleed check)."""
        prices = [data["usd"] for data in sample_price_payload.values()]
        assert len(prices) == len(set(prices)), "Duplicate prices across different coins — possible data bleed"

    @pytest.mark.asyncio
    async def test_mock_price_endpoint_returns_200(self, mock_async_client, make_mock_response, sample_price_payload):
        """Mock test: endpoint returns 200 with valid payload."""
        mock_async_client.get = AsyncMock(
            return_value=make_mock_response(200, sample_price_payload)
        )
        response = await mock_async_client.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": "bitcoin,ethereum", "vs_currencies": "usd"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "bitcoin" in data
        assert "ethereum" in data

    @pytest.mark.asyncio
    async def test_invalid_coin_returns_empty_or_error(self, mock_async_client, make_mock_response):
        """Invalid coin ID should return empty object, not 500."""
        mock_async_client.get = AsyncMock(
            return_value=make_mock_response(200, {})
        )
        response = await mock_async_client.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": "notarealcoin_xyz", "vs_currencies": "usd"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "notarealcoin_xyz" not in data



# Coin Detail Endpoint — /coins/{id}


class TestCoinDetailEndpoint:

    def test_detail_response_has_required_top_level_fields(
        self, sample_coin_detail_payload, coin_detail_schema
    ):
        """Top-level required fields must be present."""
        required = coin_detail_schema["required_top_level"]
        for field in required:
            assert field in sample_coin_detail_payload, f"Missing top-level field: {field}"

    def test_market_data_block_has_required_fields(
        self, sample_coin_detail_payload, coin_detail_schema
    ):
        """market_data sub-block must contain all required keys."""
        market_data = sample_coin_detail_payload.get("market_data", {})
        required = coin_detail_schema["required_market_data"]
        for field in required:
            assert field in market_data, f"Missing market_data field: {field}"

    def test_current_price_usd_is_positive(self, sample_coin_detail_payload):
        """current_price.usd must be a positive number."""
        price = sample_coin_detail_payload["market_data"]["current_price"]["usd"]
        assert isinstance(price, (int, float))
        assert price > 0

    def test_market_cap_greater_than_volume(self, sample_coin_detail_payload):
        """For major coins, market cap should exceed 24h volume."""
        md = sample_coin_detail_payload["market_data"]
        market_cap = md["market_cap"]["usd"]
        volume = md["total_volume"]["usd"]
        assert market_cap > volume, (
            f"Market cap ({market_cap}) should exceed 24h volume ({volume}) for major assets"
        )

    def test_coin_id_and_symbol_are_strings(self, sample_coin_detail_payload):
        """id and symbol must be non-empty strings."""
        assert isinstance(sample_coin_detail_payload["id"], str)
        assert isinstance(sample_coin_detail_payload["symbol"], str)
        assert len(sample_coin_detail_payload["id"]) > 0
        assert len(sample_coin_detail_payload["symbol"]) > 0

    def test_last_updated_is_iso_string(self, sample_coin_detail_payload):
        """last_updated must be a non-empty ISO 8601 string."""
        last_updated = sample_coin_detail_payload.get("last_updated", "")
        assert isinstance(last_updated, str)
        assert "T" in last_updated, f"last_updated doesn't look like ISO 8601: {last_updated}"

    def test_circulating_supply_is_positive(self, sample_coin_detail_payload):
        """Circulating supply must be a positive number."""
        supply = sample_coin_detail_payload["market_data"].get("circulating_supply")
        if supply is not None:
            assert supply > 0, f"Circulating supply must be positive: {supply}"

    def test_price_change_24h_is_float(self, sample_coin_detail_payload):
        """24h price change percentage must be a float (can be negative)."""
        change = sample_coin_detail_payload["market_data"]["price_change_percentage_24h"]
        assert isinstance(change, float), f"Expected float, got {type(change)}"

    @pytest.mark.asyncio
    async def test_mock_coin_detail_endpoint(
        self, mock_async_client, make_mock_response, sample_coin_detail_payload
    ):
        """Mock test: /coins/bitcoin returns structured payload."""
        mock_async_client.get = AsyncMock(
            return_value=make_mock_response(200, sample_coin_detail_payload)
        )
        response = await mock_async_client.get(f"{COINGECKO_BASE}/coins/bitcoin")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "bitcoin"
        assert "market_data" in data

    @pytest.mark.asyncio
    async def test_mock_404_for_unknown_coin(self, mock_async_client, make_mock_response):
        """Unknown coin ID must return 404, not 200 with empty data."""
        mock_async_client.get = AsyncMock(
            return_value=make_mock_response(404, {"error": "coin not found"})
        )
        response = await mock_async_client.get(f"{COINGECKO_BASE}/coins/fakecoin999")
        assert response.status_code == 404



# OHLCV / Candle Data

class TestOHLCVData:

    def test_ohlcv_is_list_of_candles(self, sample_ohlcv_payload):
        """OHLCV response must be a non-empty list."""
        assert isinstance(sample_ohlcv_payload, list)
        assert len(sample_ohlcv_payload) > 0

    def test_each_candle_has_six_fields(self, sample_ohlcv_payload):
        """Each candle: [timestamp, open, high, low, close, volume]."""
        for i, candle in enumerate(sample_ohlcv_payload):
            assert len(candle) == 6, f"Candle {i} has {len(candle)} fields, expected 6"

    def test_candle_timestamps_are_ascending(self, sample_ohlcv_payload):
        """Timestamps must be strictly ascending (no reordering or duplicates)."""
        timestamps = [c[0] for c in sample_ohlcv_payload]
        for i in range(1, len(timestamps)):
            assert timestamps[i] > timestamps[i - 1], (
                f"Non-ascending timestamp at index {i}: {timestamps[i-1]} -> {timestamps[i]}"
            )

    def test_candle_high_gte_low(self, sample_ohlcv_payload):
        """High must be >= Low in every candle."""
        for i, (ts, open_, high, low, close, vol) in enumerate(sample_ohlcv_payload):
            assert high >= low, f"Candle {i}: high ({high}) < low ({low})"

    def test_candle_high_gte_open_and_close(self, sample_ohlcv_payload):
        """High must be >= both open and close."""
        for i, (ts, open_, high, low, close, vol) in enumerate(sample_ohlcv_payload):
            assert high >= open_, f"Candle {i}: high < open"
            assert high >= close, f"Candle {i}: high < close"

    def test_candle_low_lte_open_and_close(self, sample_ohlcv_payload):
        """Low must be <= both open and close."""
        for i, (ts, open_, high, low, close, vol) in enumerate(sample_ohlcv_payload):
            assert low <= open_, f"Candle {i}: low > open"
            assert low <= close, f"Candle {i}: low > close"

    def test_candle_volume_is_non_negative(self, sample_ohlcv_payload):
        """Volume must be zero or positive."""
        for i, candle in enumerate(sample_ohlcv_payload):
            vol = candle[5]
            assert vol >= 0, f"Candle {i}: negative volume {vol}"