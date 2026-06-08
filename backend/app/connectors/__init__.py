import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "application/json, text/html, */*",
}

_BSE_HEADERS = {
    **_DEFAULT_HEADERS,
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}

_CONNECTOR_TIMEOUT = float(os.environ.get("CONNECTOR_TIMEOUT", 30.0))
_CONNECTOR_RETRIES = int(os.environ.get("CONNECTOR_RETRIES", 5))

_CLIENT = httpx.AsyncClient(
    headers=_DEFAULT_HEADERS,
    timeout=_CONNECTOR_TIMEOUT,
    follow_redirects=True,
)


async def _fetch(
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    data: dict | None = None,
    extra_headers: dict | None = None,
    retries: int | None = None,
    backoff: float = 2.0,
    initial_delay: float = 2.0,
) -> httpx.Response:
    """Fetch a URL with exponential backoff on 429 and 5xx responses.

    Defaults: 5 retries, 2× backoff, starting at 2s delay.
    Total max wait before giving up: 2 + 4 + 8 + 16 = ~30s across 5 attempts.
    Callers can override retries/backoff/initial_delay per-connector.
    """
    effective_retries = retries if retries is not None else _CONNECTOR_RETRIES
    headers = {**_DEFAULT_HEADERS, **(extra_headers or {})}
    delay = initial_delay
    last_exc: Exception | None = None

    for attempt in range(effective_retries):
        try:
            resp = await _CLIENT.request(
                method, url, params=params, data=data, headers=headers
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                logger.warning(
                    f"_fetch: {resp.status_code} on attempt {attempt + 1}/{effective_retries}, "
                    f"retrying in {delay:.1f}s — {url}"
                )
                await asyncio.sleep(delay)
                delay *= backoff
                continue
            resp.raise_for_status()
            return resp
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning(
                f"_fetch: network error on attempt {attempt + 1}/{effective_retries} "
                f"({type(exc).__name__}: {exc or 'connection refused'}) — {url}"
            )
            last_exc = exc
            await asyncio.sleep(delay)
            delay *= backoff

    raise last_exc or httpx.RequestError(f"All {effective_retries} retries failed for {url}")
