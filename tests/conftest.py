"""
conftest.py — Shared fixtures, clients, and test configuration.

Now backed by:
- config.py  → centralized settings (no scattered os.getenv in test files)
- logger.py  → structured logging to console + JSON file
- client.py  → reusable API client with retry/backoff

Covers:
- Async HTTP client setup (httpx)
- Mock API response factories
- Chaos / fault injection fixtures
- Schema definitions
- Environment-based config via FrameworkConfig
"""

import asyncio
import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import random
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

from config import settings, get_coingecko_headers, get_coinglass_headers
from logger import get_logger

logger = get_logger(__name__)


#HTTP Client Fixtures 


@pytest.fixture(scope="session")
def coingecko_headers():
    return get_coingecko_headers()


@pytest.fixture(scope="session")
def coinglass_headers():
    return get_coinglass_headers()


@pytest_asyncio.fixture(scope="session")
async def async_client():
    """Shared async HTTP client for the full test session."""
    logger.info("Initialising shared async HTTP client (timeout=%.1fs)", settings.coingecko.timeout)
    async with httpx.AsyncClient(
        timeout=settings.coingecko.timeout,
        headers=get_coingecko_headers(),
    ) as client:
        yield client


@pytest_asyncio.fixture
async def mock_async_client():
    """Mock async client — no real HTTP calls. Default for all CI runs."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


# Mock Response Factories 


@pytest.fixture
def make_mock_response():
    """
    Factory: builds a mock httpx.Response with given status + JSON body.

    Usage:
        resp = make_mock_response(200, {"id": "bitcoin"})
        resp = make_mock_response(429, {"error": "rate limited"})
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
        logger.debug("Built mock response: status=%d body_keys=%s", status_code, list(json_body.keys()) if isinstance(json_body, dict) else "[]")
        return mock_resp
    return _factory


# Chaos / Fault Injection Fixture


@pytest.fixture
def make_malformed_response():
    """
    Factory: builds a mock response with malformed/partial JSON body.

    Simulates real-world API failures where:
    - Fields are missing
    - Types are wrong (string price instead of float)
    - Values are null where they shouldn't be
    - Symbols are uppercase instead of lowercase

    Usage:
        resp = make_malformed_response("string_price")
        resp = make_malformed_response("null_market_cap")
    """
    scenarios = {
        # Price is a string — common silent failure in data pipelines
        "string_price": {
            "bitcoin": {"usd": "65000.00", "usd_24h_change": 2.5}
        },
        # Market cap field missing entirely
        "missing_market_cap": {
            "id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
            "market_data": {
                "current_price": {"usd": 65000.0},
                "total_volume": {"usd": 30_000_000_000},
                "price_change_percentage_24h": 2.5,
                "circulating_supply": 19_500_000.0,
            },
            "last_updated": "2024-01-15T12:00:00.000Z",
        },
        # Symbol uppercased — breaks case-sensitive downstream consumers
        "uppercase_symbol": {
            "id": "bitcoin", "symbol": "BTC", "name": "Bitcoin",
            "market_data": {
                "current_price": {"usd": 65000.0},
                "market_cap": {"usd": 1_200_000_000_000},
                "total_volume": {"usd": 30_000_000_000},
                "price_change_percentage_24h": 2.5,
                "circulating_supply": 19_500_000.0,
            },
            "last_updated": "2024-01-15T12:00:00.000Z",
        },
        # Stale timestamp — data freshness failure
        "stale_timestamp": {
            "id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
            "market_data": {
                "current_price": {"usd": 65000.0},
                "market_cap": {"usd": 1_200_000_000_000},
                "total_volume": {"usd": 30_000_000_000},
                "price_change_percentage_24h": 2.5,
                "circulating_supply": 19_500_000.0,
            },
            "last_updated": "2020-01-01T00:00:00.000Z",  # 4 years old
        },
        # Null price — silent null propagation failure
        "null_price": {
            "bitcoin": {"usd": None, "usd_24h_change": 2.5}
        },
        # Empty data array — pipeline returns success but no data
        "empty_data": {
            "code": "0", "msg": "success", "data": []
        },
        # Negative volume — impossible value that should be caught
        "negative_volume": [
            [1705276800000, 64800.0, 65500.0, 64200.0, 65000.0, -500.0],
        ],
        # Out-of-order timestamps — breaks time-series consumers
        "reversed_timestamps": [
            [1705284000000, 65400.0, 66000.0, 65100.0, 65750.0, 13200.0],
            [1705280400000, 65000.0, 65800.0, 64900.0, 65400.0, 11800.0],
            [1705276800000, 64800.0, 65500.0, 64200.0, 65000.0, 12500.0],
        ],
        # High < Low — physically impossible candle
        "impossible_candle": [
            [1705276800000, 64800.0, 64000.0, 65500.0, 65000.0, 12500.0],
        ],
        # Partial response — truncated mid-stream
        "partial_response": {
            "bitcoin": {}  # coin present but all fields missing
        },
    }

    def _factory(scenario: str):
        if scenario not in scenarios:
            raise ValueError(f"Unknown chaos scenario: '{scenario}'. Available: {list(scenarios.keys())}")
        payload = scenarios[scenario]
        logger.debug("Injecting chaos scenario: %s", scenario)
        return payload

    return _factory


@pytest.fixture
def make_network_fault():
    """
    Factory: returns an AsyncMock side_effect simulating network-level failures.

    Usage:
        mock_client.get = AsyncMock(side_effect=make_network_fault("timeout"))
        mock_client.get = AsyncMock(side_effect=make_network_fault("intermittent"))
    """
    def _factory(fault_type: str, failure_rate: float = 0.5):
        call_count = {"n": 0}

        async def _side_effect(*args, **kwargs):
            call_count["n"] += 1
            n = call_count["n"]

            if fault_type == "timeout":
                raise httpx.TimeoutException("Simulated timeout")

            elif fault_type == "connect_error":
                raise httpx.ConnectError("Simulated connection refused")

            elif fault_type == "intermittent":
                # Fails on first call, succeeds after
                if n == 1:
                    raise httpx.ConnectError("Simulated transient failure")
                mock = MagicMock(spec=httpx.Response)
                mock.status_code = 200
                mock.json.return_value = {}
                return mock

            elif fault_type == "random":
                # Randomly fails at the given rate — stress-tests retry logic
                if random.random() < failure_rate:
                    raise httpx.ConnectError("Simulated random failure")
                mock = MagicMock(spec=httpx.Response)
                mock.status_code = 200
                mock.json.return_value = {}
                return mock

            elif fault_type == "downtime":
                # Always returns 503
                mock = MagicMock(spec=httpx.Response)
                mock.status_code = 503
                mock.json.return_value = {"error": "service unavailable"}
                mock.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "503", request=MagicMock(), response=mock
                )
                return mock

            else:
                raise ValueError(f"Unknown fault type: {fault_type}")

        return _side_effect

    return _factory


# Sample Payload Fixtures 


@pytest.fixture
def sample_price_payload():
    return {
        "bitcoin": {"usd": 65000.0, "usd_24h_change": 2.5},
        "ethereum": {"usd": 3200.0, "usd_24h_change": -1.1},
    }


@pytest.fixture
def sample_coin_detail_payload():
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
    return [
        [1705276800000, 64800.0, 65500.0, 64200.0, 65000.0, 12500.0],
        [1705280400000, 65000.0, 65800.0, 64900.0, 65400.0, 11800.0],
        [1705284000000, 65400.0, 66000.0, 65100.0, 65750.0, 13200.0],
    ]


# Schema Fixtures 


@pytest.fixture
def price_schema():
    return {
        "type": "object",
        "required_coin_keys": ["usd"],
        "optional_coin_keys": ["usd_24h_change", "usd_market_cap", "usd_24h_vol"],
        "value_types": {"usd": (int, float)},
    }


@pytest.fixture
def coin_detail_schema():
    return {
        "required_top_level": ["id", "symbol", "name", "market_data", "last_updated"],
        "required_market_data": [
            "current_price",
            "market_cap",
            "total_volume",
            "price_change_percentage_24h",
        ],
    }


@pytest.fixture
def funding_rate_schema():
    return {
        "required_top_level": ["code", "data"],
        "required_data_item": ["symbol", "fundingRate", "fundingTime"],
    }


# Shared Assertion Helpers 


def assert_valid_price(value, field_name="price"):
    assert isinstance(value, (int, float)), f"{field_name} must be numeric, got {type(value)}"
    assert value > 0, f"{field_name} must be positive, got {value}"


def assert_response_keys(data: dict, required_keys: list, context: str = ""):
    missing = [k for k in required_keys if k not in data]
    assert not missing, f"Missing keys {missing} in {context or 'response'}"


# Expose helpers for import from test files
pytest.assert_valid_price = assert_valid_price
pytest.assert_response_keys = assert_response_keys