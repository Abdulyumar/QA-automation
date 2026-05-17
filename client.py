"""
client.py — Reusable async API client wrapper.

Centralizes:
- Retry + exponential backoff logic
- 429 rate limit handling
- Timeout management
- Structured error logging

All test files should use CryptoAPIClient instead of raw httpx calls
where possible — this is what separates a test suite from a framework.
"""

import asyncio
import logging
from typing import Any

import httpx

from config import APIConfig, settings

logger = logging.getLogger("qa.client")


class APIError(Exception):
    """Raised when an API call fails after all retries."""
    def __init__(self, message: str, status_code: int = None, url: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class RateLimitError(APIError):
    """Raised when rate limit is hit and retries are exhausted."""
    pass


class CryptoAPIClient:
    """
    Async HTTP client wrapper with built-in retry, backoff, and logging.

    Usage:
        async with CryptoAPIClient(settings.coingecko) as client:
            data = await client.get("/simple/price", params={"ids": "bitcoin", "vs_currencies": "usd"})
    """

    def __init__(self, api_config: APIConfig, extra_headers: dict = None):
        self.config = api_config
        self.extra_headers = extra_headers or {}
        self._client: httpx.AsyncClient = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "crypto-market-data-qa/1.0",
                **self.extra_headers,
            },
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def get(self, path: str, params: dict = None) -> dict[str, Any]:
        """
        GET request with automatic retry and backoff.

        Returns parsed JSON on success.
        Raises APIError, RateLimitError, or httpx exceptions on failure.
        """
        url = path
        attempt = 0

        while attempt <= self.config.max_retries:
            try:
                logger.debug("GET %s (attempt %d/%d)", url, attempt + 1, self.config.max_retries + 1)
                resp = await self._client.get(url, params=params)

                if resp.status_code == 429:
                    wait = self.config.backoff_factor * (2 ** attempt)
                    logger.warning("Rate limited on %s — waiting %.2fs before retry", url, wait)
                    if attempt == self.config.max_retries:
                        raise RateLimitError(
                            f"Rate limit exhausted after {attempt + 1} attempts",
                            status_code=429,
                            url=url,
                        )
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue

                resp.raise_for_status()
                logger.debug("GET %s -> %d", url, resp.status_code)
                return resp.json()

            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                wait = self.config.backoff_factor * (2 ** attempt)
                logger.warning("Network error on %s (%s) — retry in %.2fs", url, exc, wait)
                if attempt == self.config.max_retries:
                    raise
                await asyncio.sleep(wait)
                attempt += 1

            except httpx.HTTPStatusError as exc:
                logger.error("HTTP %d on %s", exc.response.status_code, url)
                raise APIError(
                    str(exc),
                    status_code=exc.response.status_code,
                    url=url,
                ) from exc

        raise APIError(f"All retries exhausted for {url}")


# ── Standalone retry helpers (used directly in test files) ────────────────────


async def fetch_with_retry(client, url, params=None, retries=3):
    """
    Retry on ConnectError or TimeoutException with linear backoff.
    Raises on final attempt failure.
    """
    for attempt in range(retries):
        try:
            return await client.get(url, params=params)
        except (httpx.ConnectError, httpx.TimeoutException):
            if attempt == retries - 1:
                raise
            await asyncio.sleep(0.01 * (attempt + 1))


async def fetch_with_backoff(client, url, params=None, retries=5):
    """
    Retry on HTTP 429 with exponential backoff.
    Returns last response (may be 429) if all retries are exhausted.
    """
    resp = None
    for attempt in range(retries):
        resp = await client.get(url, params=params)
        if resp.status_code == 429:
            await asyncio.sleep(0.01 * (attempt + 1))
            continue
        return resp
    return resp