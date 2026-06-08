"""RBI/Macro connector — World Bank Development Indicators API for Indian macroeconomic data.

All data is annual. World Bank IDs are well-documented stable identifiers.
"""

import logging
import os

from app.connectors import _fetch

logger = logging.getLogger(__name__)

# World Bank indicator codes → description
_WB_INDICATORS: dict[str, tuple[str, str]] = {
    "repo_rate"          : ("FR.INR.LEND",        "Lending Interest Rate (%) — proxy for policy rate trend"),
    "cpi"                : ("FP.CPI.TOTL.ZG",     "CPI Inflation (annual %)"),
    "wpi"                : ("NY.GDP.DEFL.KD.ZG",  "GDP Deflator / Wholesale Price Proxy (annual %)"),
    "bank_credit_growth" : ("FS.AST.PRVT.GD.ZS",  "Private Credit by Banks (% of GDP)"),
    "bank_deposits"      : ("FS.AST.PRVT.GD.ZS",  "Domestic Credit to Private Sector (% of GDP)"),
    "forex_reserves"     : ("FI.RES.TOTL.CD",     "Total Forex Reserves (USD)"),
    "npa_ratio"          : ("FB.AST.NPER.ZS",     "Bank Non-Performing Loans (% of total loans)"),
    "gdp_growth"         : ("NY.GDP.MKTP.KD.ZG",  "GDP Growth Rate (annual %)"),
}

_VALID_INDICATORS = set(_WB_INDICATORS)
_WB_BASE = os.environ.get(
    "WORLDBANK_API_BASE", "https://api.worldbank.org/v2/country/IN/indicator"
)


async def fetch(indicator: str, periods: int = 8) -> str:
    if indicator not in _VALID_INDICATORS:
        return (
            f"Unknown indicator '{indicator}'. "
            f"Valid options: {', '.join(sorted(_VALID_INDICATORS))}"
        )

    wb_code, description = _WB_INDICATORS[indicator]
    url = f"{_WB_BASE}/{wb_code}"

    try:
        resp = await _fetch(
            url,
            params={"format": "json", "mrv": periods + 2, "per_page": periods + 2},
            retries=3,
        )
        data = resp.json()
        if not isinstance(data, list) or len(data) < 2:
            return f"Unexpected response from World Bank API for '{indicator}'."

        rows = [d for d in data[1] if d.get("value") is not None]
        rows = sorted(rows, key=lambda d: d["date"], reverse=True)[:periods]

        if not rows:
            return f"No data returned for '{indicator}' from World Bank API."

        return _format(indicator, description, rows, url)

    except Exception as exc:
        logger.error(f"RBI/WB fetch failed for '{indicator}': {exc}")
        return f"Macro data unavailable for '{indicator}': {type(exc).__name__}: {exc}"


def _format(indicator: str, description: str, rows: list[dict], source_url: str) -> str:
    lines = [
        f"India Macroeconomic Data — {indicator}",
        f"Series: {description}",
        f"Source: World Bank Development Indicators ({source_url.split('/')[6]})",
        f"Data frequency: Annual  |  Country: India",
        "",
        f"{'Year':<6} | {'Value':>12}",
        "-" * 22,
    ]

    for row in rows:
        year = row["date"]
        val = row["value"]

        if indicator == "forex_reserves":
            # Convert USD to USD billion
            display = f"{val / 1e9:>11.1f}B"
        elif indicator in ("cpi", "wpi", "gdp_growth", "repo_rate", "npa_ratio"):
            display = f"{val:>11.2f}%"
        elif indicator in ("bank_credit_growth", "bank_deposits"):
            display = f"{val:>10.1f}% GDP"
        else:
            display = f"{val:>12.2f}"

        lines.append(f"{year:<6} | {display}")

    # Add interpretation hint
    lines.append("")
    if indicator == "repo_rate":
        lines.append("Note: This is the bank lending rate (proxy). RBI repo rate is the central bank")
        lines.append("policy rate (typically 150-250 bps below lending rate).")
    elif indicator == "bank_credit_growth":
        lines.append("Note: Shows private credit as % of GDP (level, not growth rate).")
        lines.append("Rising % indicates credit expansion relative to the economy.")
    elif indicator == "wpi":
        lines.append("Note: GDP deflator used as WPI proxy (both measure economy-wide price changes).")

    return "\n".join(lines)[:6000]
