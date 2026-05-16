"""
conftest.py — Shared fixtures, clients, and test configuration.

Covers:
- Async HTTP client setup (httpx)
- Mock API response factories
- Schema loader
- Environment-based config
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio


# Config


BASE_URLS = {
    "coingecko": "https://api.coingecko.com/api/v3",
    "coinglass": "https://open-api.coinglass.com/public/v2",
    "mock": "http://testserver",
}

SUPPORTED_COINS = ["bitcoin", "ethereum", "solana", "binancecoin"]
SUPPORTED_VS_CURRENCIES = ["usd", "btc", "eth"]

COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY", "")



# HTTP Client Fixtures


@pytest.fixture(scope="session")
def base_headers():
    """Common headers for API requests."""
    return {
        "Accept": "application/json",
        "User-Agent": "exchange-api-qa/1.0",
    }


@pytest.fixture(scope="session")
def coingecko_headers(base_headers):
    headers = base_headers.copy()
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    return headers


@pytest_asyncio.fixture(scope="session")
async def async_client():
    """Shared async HTTP client for the full test session."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        yield client


@pytest_asyncio.fixture
async def mock_async_client():
    """Mock async client for unit-level tests (no real HTTP calls)."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client



# Mock Response Factories


@pytest.fixture
def make_mock_response():
    """
    Factory that builds a mock httpx.Response with given status + JSON body.

    Usage:
        response = make_mock_response(200, {"id": "bitcoin", "symbol": "btc"})
    """
    def _factory(status_code: int, json_body: dict) -> MagicMock:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = status_code
        mock_resp.json.return_value = json_body
        mock_resp.text = json.dumps(json_body)
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.raise_for_status = MagicMock()
        if status_code >= 400:
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                message=f"HTTP {status_code}",
                request=MagicMock(),
                response=mock_resp,
            )
        return mock_resp
    return _factory



# Sample Payload Fixtures


@pytest.fixture
def sample_price_payload():
    """Minimal valid CoinGecko /simple/price response."""
    return {
        "bitcoin": {"usd": 65000.0, "usd_24h_change": 2.5},
        "ethereum": {"usd": 3200.0, "usd_24h_change": -1.1},
    }


@pytest.fixture
def sample_coin_detail_payload():
    """Minimal valid CoinGecko /coins/{id} response."""
    return {
        "id": "bitcoin",
        "symbol": "btc",
        "name": "Bitcoin",
        "market_data": {
            "current_price": {"usd": 65000.0},
            "market_cap": {"usd": 1_200_000_000_000},
            "total_volume": {"usd": 30_000_000_000},
            "price_change_percentage_24h": 2.5,
            "circulating_supply": 19_500_000.0,
        },
        "last_updated": "2024-01-15T12:00:00.000Z",
    }


@pytest.fixture
def sample_funding_rate_payload():
    """Minimal valid funding rate response."""
    return {
        "code": "0",
        "msg": "success",
        "data": [
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.0001",
                "fundingTime": 1705320000000,
                "nextFundingTime": 1705348800000,
            }
        ],
    }


@pytest.fixture
def sample_ohlcv_payload():
    """OHLCV candle data — list of [timestamp, open, high, low, close, volume]."""
    return [
        [1705276800000, 64800.0, 65500.0, 64200.0, 65000.0, 12500.0],
        [1705280400000, 65000.0, 65800.0, 64900.0, 65400.0, 11800.0],
        [1705284000000, 65400.0, 66000.0, 65100.0, 65750.0, 13200.0],
    ]



# Schema Fixtures


PRICE_RESPONSE_SCHEMA = {
    "type": "object",
    "required_coin_keys": ["usd"],
    "optional_coin_keys": ["usd_24h_change", "usd_market_cap", "usd_24h_vol"],
    "value_types": {"usd": (int, float)},
}

COIN_DETAIL_SCHEMA = {
    "required_top_level": ["id", "symbol", "name", "market_data", "last_updated"],
    "required_market_data": [
        "current_price",
        "market_cap",
        "total_volume",
        "price_change_percentage_24h",
    ],
}

FUNDING_RATE_SCHEMA = {
    "required_top_level": ["code", "data"],
    "required_data_item": ["symbol", "fundingRate", "fundingTime"],
}


@pytest.fixture
def price_schema():
    return PRICE_RESPONSE_SCHEMA


@pytest.fixture
def coin_detail_schema():
    return COIN_DETAIL_SCHEMA


@pytest.fixture
def funding_rate_schema():
    return FUNDING_RATE_SCHEMA


# Helpers


def assert_valid_price(value, field_name="price"):
    """Assert a value is a positive number — reusable across tests."""
    assert isinstance(value, (int, float)), f"{field_name} must be numeric, got {type(value)}"
    assert value > 0, f"{field_name} must be positive, got {value}"


def assert_response_keys(data: dict, required_keys: list, context: str = ""):
    """Assert all required keys exist in a dict."""
    missing = [k for k in required_keys if k not in data]
    assert not missing, f"Missing keys {missing} in {context or 'response'}"


# Expose helpers so test files can import from conftest
pytest.assert_valid_price = assert_valid_price
pytest.assert_response_keys = assert_response_keys