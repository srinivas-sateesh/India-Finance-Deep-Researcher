"""SEBI insider trading connector — uses NSE's PIT disclosure API.

NSE publishes Prohibition of Insider Trading (PIT) disclosures via a public API.
SAST and pledge disclosures require the SEBI portal (sebi.gov.in) which may be
unavailable from some network environments; graceful messages are returned in that case.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from app.connectors.bse import _KNOWN as _BSE_KNOWN, _search_yahoo

logger = logging.getLogger(__name__)

_TICKER_CACHE: dict[str, str] = {}

_NSE_BASE = os.environ.get("NSE_BASE_URL", "https://www.nseindia.com")
_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": f"{_NSE_BASE}/companies-listing/corporate-filings-insider-trading",
}


async def fetch(company: str, disclosure_type: str = "insider", lookback_days: int = 180) -> str:
    if disclosure_type == "insider":
        return await _fetch_pit(company, lookback_days)
    elif disclosure_type in ("sast", "pledge"):
        return (
            f"SAST and pledge disclosures for '{company}' are only available via the SEBI portal "
            f"(sebi.gov.in), which is not reachable from the current network. "
            f"As an alternative, use search_bse_filings('{company}', 'shareholding') to check "
            f"promoter holding trends, which indirectly signals pledge activity."
        )
    else:
        return f"Unknown disclosure_type '{disclosure_type}'. Use: insider | sast | pledge"


async def _fetch_pit(company: str, lookback_days: int) -> str:
    symbol = await _resolve_nse_symbol(company)
    if not symbol:
        return (
            f"Could not resolve '{company}' to an NSE symbol. "
            "Try the exact NSE ticker (e.g. 'HDFCBANK', 'INFY', 'ADANIENT')."
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    from_date = cutoff.strftime("%d-%m-%Y")
    to_date = datetime.now(timezone.utc).strftime("%d-%m-%Y")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        try:
            # Warm up session
            await c.get(f"{_NSE_BASE}/", headers=_NSE_HEADERS)
        except Exception:
            pass

        try:
            resp = await c.get(
                f"{_NSE_BASE}/api/corporates-pit",
                params={"index": "equities", "symbol": symbol,
                        "from_date": from_date, "to_date": to_date},
                headers=_NSE_HEADERS,
            )
        except Exception as exc:
            logger.error(f"NSE PIT fetch failed for {symbol}: {exc}")
            return f"NSE insider trading data unavailable for '{company}': {type(exc).__name__}: {exc}"

    if resp.status_code != 200:
        return f"NSE PIT API returned {resp.status_code} for '{symbol}'. Check the company symbol."

    try:
        data = resp.json()
    except Exception:
        return f"NSE PIT API returned non-JSON response for '{symbol}'."

    rows = data.get("data", [])
    if not rows:
        return (
            f"No PIT insider trading disclosures found for '{company}' ({symbol}) "
            f"in the last {lookback_days} days. "
            "This may mean insiders did not trade during this period, or the symbol is incorrect."
        )

    return _format_pit(company, symbol, rows, lookback_days)


async def _resolve_nse_symbol(company: str) -> str | None:
    """Resolve company name to NSE symbol (uppercase, no suffix)."""
    key = company.strip().lower()
    if key in _TICKER_CACHE:
        return _TICKER_CACHE[key]

    # Use the BSE known map — strip .NS suffix
    if key in _BSE_KNOWN:
        sym = _BSE_KNOWN[key].replace(".NS", "").replace(".BO", "")
        _TICKER_CACHE[key] = sym
        return sym

    # If it already looks like a ticker
    upper = company.strip().upper()
    if " " not in upper and len(upper) <= 15:
        _TICKER_CACHE[key] = upper
        return upper

    # Yahoo Finance search → strip .NS
    yf_ticker = await _search_yahoo(company)
    if yf_ticker:
        sym = yf_ticker.replace(".NS", "").replace(".BO", "")
        _TICKER_CACHE[key] = sym
        return sym

    # Name normalisation
    normalised = (
        upper
        .replace(" LIMITED", "").replace(" LTD", "").replace(" INDUSTRIES", "")
        .replace(" BANK", "BANK").replace(" FINANCE", "FIN")
        .replace(" TECHNOLOGIES", "TECH").replace(" INDIA", "")
        .replace(" ", "")[:12]
    )
    _TICKER_CACHE[key] = normalised
    return normalised


def _format_pit(company: str, symbol: str, rows: list[dict], lookback_days: int) -> str:
    lines = [
        f"NSE Insider Trading (PIT) Disclosures — {company} ({symbol})",
        f"Period: last {lookback_days} days  |  Total disclosures: {len(rows)}",
        f"Source: NSE India / Prohibition of Insider Trading Regulations",
        "",
    ]

    # Summary by mode
    from collections import Counter
    mode_counts = Counter(r.get("acqMode", "Unknown") for r in rows)
    mode_summary = ", ".join(f"{mode}: {cnt}" for mode, cnt in mode_counts.most_common(5))
    lines.append(f"Transaction types: {mode_summary}")
    lines.append("")

    # Table header
    lines.append(f"{'Date':<12} | {'Insider':<30} | {'Category':<15} | {'Mode':<15} | {'Buy Qty':>10} | {'Sell Qty':>10} | {'After %':>8}")
    lines.append("-" * 110)

    for row in rows[:30]:  # cap at 30 rows
        date = row.get("acqfromDt", "")[:11]
        name = (row.get("acqName") or "")[:29]
        cat = (row.get("personCategory") or "")[:14]
        mode = (row.get("acqMode") or "")[:14]
        buy_qty = row.get("buyQuantity", "0") or "0"
        sell_qty = row.get("sellquantity", "0") or "0"
        after_pct = row.get("afterAcqSharesPer", "0") or "0"
        lines.append(
            f"{date:<12} | {name:<30} | {cat:<15} | {mode:<15} | "
            f"{str(buy_qty):>10} | {str(sell_qty):>10} | {str(after_pct):>8}"
        )

    if len(rows) > 30:
        lines.append(f"\n[... {len(rows) - 30} more rows truncated ...]")

    return "\n".join(lines)[:8000]
