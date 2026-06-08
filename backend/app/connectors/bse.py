"""BSE connector — uses yfinance (Yahoo Finance) for Indian equity data.

Ticker resolution priority:
  1. Direct NSE/BSE ticker if already in TICKER_CACHE
  2. Hardcoded common-name → ticker map (top Indian large-caps)
  3. Yahoo Finance search API
  4. Name-normalisation fallback (strip suffixes, try .NS)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Literal

import httpx
import yfinance as yf

logger = logging.getLogger(__name__)

FilingType = Literal["results", "shareholding", "announcements", "estimates"]

_TICKER_CACHE: dict[str, str] = {}

# Fast-path map for the most commonly researched Indian companies.
# Covers all 9 sample questions in PLAN.md §8.
_KNOWN: dict[str, str] = {
    "hdfc bank": "HDFCBANK.NS",
    "hdfcbank": "HDFCBANK.NS",
    "tcs": "TCS.NS",
    "tata consultancy": "TCS.NS",
    "tata consultancy services": "TCS.NS",
    "infosys": "INFY.NS",
    "wipro": "WIPRO.NS",
    "hcl tech": "HCLTECH.NS",
    "hcl technologies": "HCLTECH.NS",
    "tech mahindra": "TECHM.NS",
    "reliance": "RELIANCE.NS",
    "reliance industries": "RELIANCE.NS",
    "icici bank": "ICICIBANK.NS",
    "axis bank": "AXISBANK.NS",
    "kotak": "KOTAKBANK.NS",
    "kotak mahindra": "KOTAKBANK.NS",
    "kotak mahindra bank": "KOTAKBANK.NS",
    "bajaj finance": "BAJFINANCE.NS",
    "cholamandalam": "CHOLAFIN.NS",
    "chola": "CHOLAFIN.NS",
    "hindustan unilever": "HINDUNILVR.NS",
    "hul": "HINDUNILVR.NS",
    "nestle india": "NESTLEIND.NS",
    "nestle": "NESTLEIND.NS",
    "asian paints": "ASIANPAINT.NS",
    "zomato": "ZOMATO.NS",
    "paytm": "PAYTM.NS",
    "one97 communications": "PAYTM.NS",
    "adani enterprises": "ADANIENT.NS",
    "adani": "ADANIENT.NS",
    "deepak nitrite": "DEEPAKNTR.NS",
    "hdfc life": "HDFCLIFE.NS",
    "sbi": "SBIN.NS",
    "state bank": "SBIN.NS",
    "state bank of india": "SBIN.NS",
    "maruti": "MARUTI.NS",
    "maruti suzuki": "MARUTI.NS",
    "sun pharma": "SUNPHARMA.NS",
    "dr reddy": "DRREDDY.NS",
    "dr. reddy": "DRREDDY.NS",
    "cipla": "CIPLA.NS",
    "titan": "TITAN.NS",
    "bajaj auto": "BAJAJ-AUTO.NS",
    "ltimindtree": "LTIM.NS",
    "l&t": "LT.NS",
    "larsen": "LT.NS",
    "larsen and toubro": "LT.NS",
    "ongc": "ONGC.NS",
    "power grid": "POWERGRID.NS",
    "ntpc": "NTPC.NS",
    "coal india": "COALINDIA.NS",
}

_YAHOO_SEARCH_URL = os.environ.get(
    "YAHOO_SEARCH_URL", "https://query2.finance.yahoo.com/v1/finance/search"
)

_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}


async def fetch(company: str, filing_type: FilingType, quarters: int = 4) -> str:
    quarters = min(max(1, quarters), 8)
    ticker_sym = await _resolve_ticker(company)
    if not ticker_sym:
        return (
            f"Could not resolve '{company}' to an NSE/BSE ticker. "
            "Try the exact NSE ticker symbol (e.g. 'HDFCBANK', 'RELIANCE', 'TCS')."
        )
    try:
        ticker = yf.Ticker(ticker_sym)
        if filing_type == "results":
            return _fetch_results(ticker, ticker_sym, quarters)
        elif filing_type == "shareholding":
            return _fetch_shareholding(ticker, ticker_sym)
        elif filing_type == "announcements":
            return _fetch_announcements(ticker, ticker_sym)
        elif filing_type == "estimates":
            return _fetch_estimates(ticker, ticker_sym)
        else:
            return f"Unknown filing_type '{filing_type}'. Use: results | shareholding | announcements | estimates"
    except Exception as exc:
        logger.error(f"BSE fetch failed for {company}/{filing_type}: {exc}")
        return f"Data unavailable for '{company}' ({filing_type}): {type(exc).__name__}: {exc}"


async def _resolve_ticker(company: str) -> str | None:
    key = company.strip().lower()
    if key in _TICKER_CACHE:
        return _TICKER_CACHE[key]

    # 1. Hardcoded map (fast path, no network)
    if key in _KNOWN:
        _TICKER_CACHE[key] = _KNOWN[key]
        return _KNOWN[key]

    # 2. If it already looks like a valid ticker format (no spaces, short)
    upper = company.strip().upper()
    if " " not in upper and len(upper) <= 15:
        for candidate in [f"{upper}.NS", f"{upper}.BO", upper]:
            if "." in candidate or candidate == upper:
                _TICKER_CACHE[key] = candidate if "." in candidate else f"{candidate}.NS"
                return _TICKER_CACHE[key]

    # 3. Yahoo Finance search API
    sym = await _search_yahoo(company)
    if sym:
        _TICKER_CACHE[key] = sym
        return sym

    # 4. Name normalisation fallback
    normalised = (
        upper
        .replace(" LIMITED", "").replace(" LTD", "").replace(" INDUSTRIES", "")
        .replace(" BANK", "BANK").replace(" FINANCE", "FIN")
        .replace(" TECHNOLOGIES", "TECH").replace(" TECHNOLOGY", "TECH")
        .replace(" COMMUNICATIONS", "").replace(" INDIA", "")
        .replace(" ", "")[:12]
    )
    candidate = f"{normalised}.NS"
    _TICKER_CACHE[key] = candidate
    logger.info(f"Ticker fallback for '{company}' → {candidate}")
    return candidate


async def _search_yahoo(company: str) -> str | None:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
        for query in [company, f"{company} NSE"]:
            try:
                r = await c.get(
                    _YAHOO_SEARCH_URL,
                    params={"q": query, "lang": "en-US", "region": "IN",
                            "quotesCount": 5, "newsCount": 0},
                    headers=_YF_HEADERS,
                )
                if r.status_code != 200 or not r.content:
                    continue
                quotes = r.json().get("quotes", [])
                indian = [q for q in quotes
                          if q.get("symbol", "").endswith((".NS", ".BO"))
                          and q.get("quoteType") == "EQUITY"]
                if indian:
                    ns = [q for q in indian if q["symbol"].endswith(".NS")]
                    return (ns or indian)[0]["symbol"]
            except Exception as exc:
                logger.debug(f"Yahoo search failed for '{query}': {exc}")
    return None


def _fmt_cr(val: object) -> str:
    """Format a raw INR value (from yfinance) as Crores."""
    try:
        return f"{float(val) / 1e7:>12.0f}"
    except (TypeError, ValueError):
        return f"{'N/A':>12}"


def _fetch_results(ticker: yf.Ticker, symbol: str, quarters: int) -> str:
    qf = ticker.quarterly_income_stmt
    if qf is None or qf.empty:
        return f"No quarterly financial results found for {symbol}."

    want = ["Total Revenue", "Gross Profit", "Operating Income", "Net Income",
            "Basic EPS", "Diluted EPS"]
    available = [r for r in want if r in qf.index]
    if not available:
        return f"Quarterly results for {symbol} found but key rows (Revenue, Net Income) missing."

    cols = list(qf.columns[:quarters])
    col_labels = [str(c)[:10] for c in cols]
    lines = [
        f"BSE/NSE Quarterly Results — {symbol}  (INR Crores; EPS in INR)",
        "",
        f"{'Metric':<25} | " + " | ".join(f"{lbl:>12}" for lbl in col_labels),
        "-" * (27 + 16 * len(cols)),
    ]

    for metric in available:
        row_vals = []
        for col in cols:
            try:
                val = qf.loc[metric, col]
                if hasattr(val, "item"):
                    val = val.item()
                if metric in ("Basic EPS", "Diluted EPS"):
                    row_vals.append(f"{float(val):>12.2f}" if val is not None else f"{'N/A':>12}")
                else:
                    row_vals.append(_fmt_cr(val))
            except Exception:
                row_vals.append(f"{'N/A':>12}")
        lines.append(f"{metric:<25} | " + " | ".join(row_vals))

    return "\n".join(lines)[:8000]


def _fetch_shareholding(ticker: yf.Ticker, symbol: str) -> str:
    lines = [f"BSE/NSE Shareholding & Ownership — {symbol}  (source: Yahoo Finance)", ""]

    # Major holders summary
    try:
        mh = ticker.major_holders
        if mh is not None and not mh.empty:
            lines.append("=== Ownership Summary ===")
            for label, row in mh.iterrows():
                try:
                    val = row.iloc[0]
                    pct = f"{float(val)*100:.2f}%" if isinstance(val, float) and val <= 1.0 else str(val)
                    lines.append(f"  {str(label):<45} {pct}")
                except Exception:
                    pass
            lines.append("")
    except Exception as exc:
        logger.warning(f"major_holders failed for {symbol}: {exc}")

    # Institutional holders
    try:
        ih = ticker.institutional_holders
        if ih is not None and not ih.empty:
            lines.append("=== Top Institutional Holders ===")
            lines.append(f"{'Holder':<40} | {'Shares':>14} | {'% Held':>8}")
            lines.append("-" * 70)
            for _, row in ih.head(10).iterrows():
                holder = str(row.get("Holder", row.iloc[0]))[:39]
                shares = row.get("Shares", row.get("shares", "N/A"))
                pct = row.get("% Out", row.get("pctHeld", row.get("% Held", "N/A")))
                pct_str = f"{float(pct)*100:.2f}%" if isinstance(pct, (int, float)) else str(pct)
                lines.append(f"{holder:<40} | {str(shares):>14} | {pct_str:>8}")
    except Exception as exc:
        logger.warning(f"institutional_holders failed for {symbol}: {exc}")

    if len(lines) <= 3:
        return f"No shareholding data available for {symbol}."

    return "\n".join(lines)[:8000]


def _fetch_announcements(ticker: yf.Ticker, symbol: str) -> str:
    lines = [f"BSE/NSE Events & Announcements — {symbol}  (source: Yahoo Finance)", ""]

    # Upcoming calendar events
    try:
        cal = ticker.calendar
        if cal:
            lines.append("=== Upcoming Events ===")
            if "Earnings Date" in cal:
                dates = cal["Earnings Date"]
                date_str = ", ".join(str(d) for d in (dates if isinstance(dates, list) else [dates]))
                lines.append(f"  Next Earnings Date : {date_str}")
            if "Ex-Dividend Date" in cal:
                lines.append(f"  Ex-Dividend Date   : {cal['Ex-Dividend Date']}")
            if "Earnings Average" in cal:
                lines.append(f"  Consensus EPS Est  : ₹{cal['Earnings Average']:.2f}")
            if "Revenue Average" in cal:
                lines.append(f"  Consensus Revenue  : ₹{cal['Revenue Average']/1e7:.0f} Cr")
            lines.append("")
    except Exception:
        pass

    # Dividend / split history
    try:
        actions = ticker.actions
        if actions is not None and not actions.empty:
            divs = actions[actions.get("Dividends", actions.iloc[:, 0]) > 0] if "Dividends" in actions.columns else actions
            if not divs.empty:
                lines.append("=== Dividend History (recent) ===")
                lines.append(f"{'Date':<12} | {'Dividend (₹)':>13} | {'Split':>8}")
                lines.append("-" * 40)
                for date, row in actions.tail(8).iterrows():
                    div = row.get("Dividends", 0)
                    split = row.get("Stock Splits", 0)
                    if div > 0 or split > 0:
                        lines.append(f"{str(date)[:10]:<12} | {div:>13.2f} | {split:>8.2f}")
                lines.append("")
    except Exception:
        pass

    # Recent news
    try:
        news = ticker.news
        if news:
            lines.append("=== Recent News & Announcements ===")
            for item in news[:8]:
                content = item.get("content", {})
                title = content.get("title", item.get("title", ""))
                pub = content.get("pubDate", item.get("providerPublishTime", ""))
                if isinstance(pub, int):
                    pub = datetime.fromtimestamp(pub, tz=timezone.utc).strftime("%Y-%m-%d")
                if title:
                    lines.append(f"  [{str(pub)[:10]}] {title[:100]}")
    except Exception:
        pass

    if len(lines) <= 3:
        return f"No announcement data available for {symbol}."

    return "\n".join(lines)[:8000]


def _fetch_estimates(ticker: yf.Ticker, symbol: str) -> str:
    lines = [
        f"BSE/NSE Analyst Estimates — {symbol}  (source: Yahoo Finance consensus)",
        "",
    ]
    any_data = False

    # EPS estimates
    try:
        ee = ticker.earnings_estimate
        if ee is not None and not ee.empty:
            any_data = True
            lines.append("=== EPS Estimates (INR) ===")
            header = f"{'Period':<16} | {'Analysts':>8} | {'Avg':>8} | {'Low':>8} | {'High':>8} | {'Year-Ago':>9}"
            lines.append(header)
            lines.append("-" * len(header))
            period_labels = {
                "0q": "Current Qtr", "+1q": "Next Qtr",
                "0y": "Current Year", "+1y": "Next Year",
            }
            for period in ["0q", "+1q", "0y", "+1y"]:
                if period not in ee.index:
                    continue
                row = ee.loc[period]
                label = period_labels.get(period, period)
                n = row.get("numberOfAnalysts", float("nan"))
                avg = row.get("avg", float("nan"))
                low = row.get("low", float("nan"))
                high = row.get("high", float("nan"))
                ago = row.get("yearAgoEps", float("nan"))
                def _f(v):
                    try: return f"{float(v):>8.2f}"
                    except: return f"{'N/A':>8}"
                lines.append(
                    f"{label:<16} | {int(n) if n == n else 'N/A':>8} | "
                    f"{_f(avg)} | {_f(low)} | {_f(high)} | {_f(ago)}"
                )
            lines.append("")
    except Exception as exc:
        logger.warning(f"earnings_estimate failed for {symbol}: {exc}")

    # Revenue estimates
    try:
        re = ticker.revenue_estimate
        if re is not None and not re.empty:
            any_data = True
            lines.append("=== Revenue Estimates (INR Crores) ===")
            header = f"{'Period':<16} | {'Analysts':>8} | {'Avg':>12} | {'Low':>12} | {'High':>12}"
            lines.append(header)
            lines.append("-" * len(header))
            for period in ["0q", "+1q", "0y", "+1y"]:
                if period not in re.index:
                    continue
                row = re.loc[period]
                label = period_labels.get(period, period) if 'period_labels' in dir() else period
                n = row.get("numberOfAnalysts", float("nan"))
                def _cr(v):
                    try: return f"{float(v) / 1e7:>12,.0f}"
                    except: return f"{'N/A':>12}"
                lines.append(
                    f"{label:<16} | {int(n) if n == n else 'N/A':>8} | "
                    f"{_cr(row.get('avg'))} | {_cr(row.get('low'))} | {_cr(row.get('high'))}"
                )
            lines.append("")
    except Exception as exc:
        logger.warning(f"revenue_estimate failed for {symbol}: {exc}")

    # Growth estimates
    try:
        ge = ticker.growth_estimates
        if ge is not None and not ge.empty:
            any_data = True
            lines.append("=== EPS Growth Estimates ===")
            growth_labels = {
                "0q": "Current Qtr", "+1q": "Next Qtr",
                "0y": "Current Year", "+1y": "Next Year", "+5y": "Next 5 Yrs (ann.)",
            }
            col = ge.columns[0] if len(ge.columns) > 0 else None
            if col is not None:
                for period, label in growth_labels.items():
                    if period in ge.index:
                        val = ge.loc[period, col]
                        try:
                            pct = float(val) * 100
                            if pct != pct:  # nan check
                                lines.append(f"  {label:<22}: N/A")
                            else:
                                lines.append(f"  {label:<22}: {pct:+.1f}%")
                        except Exception:
                            pass
            lines.append("")
    except Exception as exc:
        logger.warning(f"growth_estimates failed for {symbol}: {exc}")

    # Price targets
    try:
        pt = ticker.analyst_price_targets
        if pt and isinstance(pt, dict) and pt.get("mean"):
            any_data = True
            current = pt.get("current", float("nan"))
            mean = pt.get("mean", float("nan"))
            low = pt.get("low", float("nan"))
            high = pt.get("high", float("nan"))
            try:
                upside = ((float(mean) / float(current)) - 1) * 100
                upside_str = f"  Target upside vs current: {upside:+.1f}%"
            except Exception:
                upside_str = ""
            lines.append("=== Analyst Price Targets (INR) ===")
            lines.append(
                f"  Current: ₹{current:,.0f}  |  Low: ₹{low:,.0f}  |"
                f"  Mean: ₹{mean:,.0f}  |  High: ₹{high:,.0f}"
            )
            if upside_str:
                lines.append(upside_str)
            lines.append("")
    except Exception as exc:
        logger.warning(f"analyst_price_targets failed for {symbol}: {exc}")

    # Recommendations summary
    try:
        rs = ticker.recommendations_summary
        if rs is not None and not rs.empty:
            any_data = True
            lines.append("=== Analyst Recommendations (recent months) ===")
            lines.append(
                f"{'Period':<8} | {'StrongBuy':>9} | {'Buy':>5} | {'Hold':>5} | {'Sell':>5} | {'StrongSell':>10}"
            )
            lines.append("-" * 55)
            for _, row in rs.head(4).iterrows():
                period = str(row.get("period", ""))
                sb = int(row.get("strongBuy", 0))
                b  = int(row.get("buy", 0))
                h  = int(row.get("hold", 0))
                s  = int(row.get("sell", 0))
                ss = int(row.get("strongSell", 0))
                lines.append(f"{period:<8} | {sb:>9} | {b:>5} | {h:>5} | {s:>5} | {ss:>10}")
            lines.append("")
    except Exception as exc:
        logger.warning(f"recommendations_summary failed for {symbol}: {exc}")

    if not any_data:
        return (
            f"No analyst estimate data available for {symbol}. "
            "This is common for companies with limited sell-side coverage."
        )

    lines.append(
        "Note: Estimates are analyst consensus (Yahoo Finance aggregation), "
        "not company guidance. Treat as market expectations, subject to revision."
    )
    return "\n".join(lines)[:6000]
