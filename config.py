"""
config.py — Centralized environment-based configuration for the QA framework.

All API endpoints, keys, timeouts, and behavior flags live here.
Test files import from this module — never read os.getenv directly in tests.
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class APIConfig:
    base_url: str
    api_key: str = ""
    timeout: float = 15.0
    max_retries: int = 3
    backoff_factor: float = 0.5


@dataclass(frozen=True)
class FrameworkConfig:
    # API configs
    coingecko: APIConfig = field(default_factory=lambda: APIConfig(
        base_url="https://api.coingecko.com/api/v3",
        api_key=os.getenv("COINGECKO_API_KEY", ""),
        timeout=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
        backoff_factor=float(os.getenv("BACKOFF_FACTOR", "0.5")),
    ))

    coinglass: APIConfig = field(default_factory=lambda: APIConfig(
        base_url="https://open-api.coinglass.com/public/v2",
        api_key=os.getenv("COINGLASS_API_KEY", ""),
        timeout=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
        backoff_factor=float(os.getenv("BACKOFF_FACTOR", "0.5")),
    ))

    # Test behavior
    live_tests: bool = field(default_factory=lambda: os.getenv("LIVE_TESTS", "0") == "1")
    chaos_tests: bool = field(default_factory=lambda: os.getenv("CHAOS_TESTS", "0") == "1")

    # Data validation thresholds
    market_cap_price_tolerance: float = 0.10        # 10% tolerance for market cap cross-check
    max_funding_rate: float = 0.03                  # 3% absolute max realistic funding rate
    btc_price_min: float = 100.0
    btc_price_max: float = 10_000_000.0
    pipeline_latency_threshold_ms: float = 1000.0

    # Supported assets
    supported_coins: tuple = ("bitcoin", "ethereum", "solana", "binancecoin")
    supported_vs_currencies: tuple = ("usd", "btc", "eth")


# Singleton — import this everywhere
settings = FrameworkConfig()


def get_coingecko_headers() -> dict:
    headers = {
        "Accept": "application/json",
        "User-Agent": "crypto-market-data-qa/1.0",
    }
    if settings.coingecko.api_key:
        headers["x-cg-demo-api-key"] = settings.coingecko.api_key
    return headers


def get_coinglass_headers() -> dict:
    headers = {
        "Accept": "application/json",
        "User-Agent": "crypto-market-data-qa/1.0",
    }
    if settings.coinglass.api_key:
        headers["glassnodeapi-client"] = settings.coinglass.api_key
    return headers