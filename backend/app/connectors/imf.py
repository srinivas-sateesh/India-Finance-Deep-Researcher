"""Macro projections connector — World Bank WDI (actuals) + GEP (forecasts).

GDP growth is the only indicator with accessible forward projections:
  - Historical actuals: World Bank WDI (NY.GDP.MKTP.KD.ZG)
  - Forward forecasts:  World Bank Global Economic Prospects source=27 (NYGDPMKTPKDZ)

For all other macro indicators (inflation, fiscal balance, etc.), forward projections
are not accessible from any free public API on this network. Those indicators return
a guidance message pointing to fetch_rbi_indicator for historical data.

Design note: IMF DataMapper API returns 403 for all programmatic clients.
dataservices.imf.org is DNS-blocked. World Bank GEP is the only working source
for India macro projections.
"""

import datetime
import logging
import os

from app.connectors import _fetch

logger = logging.getLogger(__name__)

_WB_BASE = os.environ.get(
    "WORLDBANK_API_BASE", "https://api.worldbank.org/v2/country/IN/indicator"
)
_GEP_SOURCE = "27"  # World Bank Global Economic Prospects dataset
_CURRENT_YEAR = datetime.date.today().year

_PROJECTION_GUIDANCE = {
    "inflation": "cpi",
    "current_account": None,
    "fiscal_balance": None,
    "unemployment": None,
    "gdp_per_capita": "gdp_growth",
}

_VALID_INDICATORS = {"gdp_growth", "inflation", "current_account", "fiscal_balance",
                     "unemployment", "gdp_per_capita"}


async def fetch(indicator: str, periods: int = 10) -> str:
    if indicator not in _VALID_INDICATORS:
        return (
            f"Unknown indicator '{indicator}'. "
            f"Valid options: {', '.join(sorted(_VALID_INDICATORS))}"
        )

    if indicator != "gdp_growth":
        rbi_indicator = _PROJECTION_GUIDANCE.get(indicator)
        rbi_hint = (
            f" Use fetch_rbi_indicator('{rbi_indicator}') for historical data on this metric."
            if rbi_indicator else ""
        )
        return (
            f"Forward projections for '{indicator}' are not available from any accessible "
            f"free API on this network. The IMF DataMapper API returns 403, and the World Bank "
            f"Global Economic Prospects dataset only publishes GDP growth forecasts for India.{rbi_hint}"
        )

    return await _fetch_gdp_growth(periods)


async def _fetch_gdp_growth(periods: int) -> str:
    try:
        # Fetch historical actuals from WDI
        resp_wdi = await _fetch(
            f"{_WB_BASE}/NY.GDP.MKTP.KD.ZG",
            params={"format": "json", "mrv": periods, "per_page": periods},
            retries=3,
        )
        wdi_rows = {
            row["date"]: float(row["value"])
            for row in resp_wdi.json()[1]
            if row.get("value") is not None
        }
    except Exception as exc:
        logger.error(f"WDI GDP fetch failed: {exc}")
        wdi_rows = {}

    try:
        # Fetch forward forecasts from World Bank Global Economic Prospects
        resp_gep = await _fetch(
            f"{_WB_BASE}/NYGDPMKTPKDZ",
            params={"source": _GEP_SOURCE, "format": "json", "mrv": 8, "per_page": 8},
            retries=3,
        )
        gep_rows = {
            row["date"]: float(row["value"])
            for row in resp_gep.json()[1]
            if row.get("value") is not None
        }
    except Exception as exc:
        logger.error(f"GEP GDP fetch failed: {exc}")
        gep_rows = {}

    if not wdi_rows and not gep_rows:
        return "GDP growth data unavailable from both WDI and GEP sources."

    # Merge: WDI wins for any year it has data; GEP fills gaps (including future years)
    combined: dict[str, tuple[float, str]] = {}
    for yr, val in wdi_rows.items():
        combined[yr] = (val, "[actual]  ")
    for yr, val in gep_rows.items():
        if yr not in combined:
            tag = "[forecast]" if int(yr) >= _CURRENT_YEAR else "[prelim.] "
            combined[yr] = (val, tag)

    rows = sorted(combined.items(), key=lambda x: x[0])
    rows = rows[-periods:]

    lines = [
        "India GDP Growth Outlook",
        "Series: Real GDP Growth Rate (annual %)",
        "Sources: World Bank WDI (actuals) + World Bank Global Economic Prospects (forecasts)",
        "",
        f"{'Year':<6} | {'Tag':<11} | {'Value':>10}",
        "-" * 32,
    ]
    for yr, (val, tag) in rows:
        lines.append(f"{yr:<6} | {tag} | {val:>9.2f}%")

    lines += [
        "",
        "Note: [forecast] rows are World Bank GEP projections, updated ~twice yearly.",
        "Note: [prelim.] rows are GEP estimates for years not yet in WDI actuals.",
    ]

    return "\n".join(lines)[:4000]
