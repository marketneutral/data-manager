"""ETFs maintained alongside the stock universe (not IWV constituents).

These are tradable proxies/benchmarks the risk model and diagnostics can use as
macro, sector, volatility, or factor-reference series. They are stored in the
`prices` (and `classifications`) tables like any ticker, but are deliberately
kept OUT of the `universe` table -- universe is the Russell 3000 stock list, and
universe-wide jobs (fundamentals, ratios, quarterly, SEC enrichment) do not
apply to funds.

Refresh (10y, as of today; the explicit from/to range is what unlocks 10y --
FMP's no-params default is only ~1,254 rows / ~5y):
    uv run data-manager update-prices --ticker "$(python - <<'EOF'
from data_manager.etfs import etf_tickers; print(",".join(etf_tickers()))
EOF
)" --start 2016-08-01
    uv run data-manager update-classifications --ticker <same list>
"""

ETFS: dict[str, dict] = {
    # ---- benchmarks / broad market ----
    "SPY": {"name": "SPDR S&P 500 ETF Trust", "category": "benchmark"},
    "QQQ": {"name": "Invesco QQQ Trust (Nasdaq-100)", "category": "benchmark"},
    "IWM": {"name": "iShares Russell 2000 ETF", "category": "benchmark"},
    "IWV": {"name": "iShares Russell 3000 ETF", "category": "benchmark"},
    # ---- volatility ----
    "VXX": {"name": "iPath S&P 500 VIX Short-Term Futures ETN", "category": "volatility"},
    # ---- US sectors (SPDR Select Sector set) ----
    "XLV": {"name": "Health Care Select Sector SPDR", "category": "sector"},
    "XLF": {"name": "Financial Select Sector SPDR", "category": "sector"},
    "XLI": {"name": "Industrial Select Sector SPDR", "category": "sector"},
    "XLB": {"name": "Materials Select Sector SPDR", "category": "sector"},
    "XLU": {"name": "Utilities Select Sector SPDR", "category": "sector"},
    "XLK": {"name": "Technology Select Sector SPDR", "category": "sector"},
    "XLC": {"name": "Communication Services Select Sector SPDR", "category": "sector"},
    "XLRE": {"name": "Real Estate Select Sector SPDR", "category": "sector"},
    "XLP": {"name": "Consumer Staples Select Sector SPDR", "category": "sector"},
    "XLY": {"name": "Consumer Discretionary Select Sector SPDR", "category": "sector"},
    "XLE": {"name": "Energy Select Sector SPDR", "category": "sector"},
    # ---- bonds ----
    "AGG": {"name": "iShares Core U.S. Aggregate Bond ETF", "category": "bonds"},
    "TLT": {"name": "iShares 20+ Year Treasury Bond ETF", "category": "bonds"},
    "SHY": {"name": "iShares 1-3 Year Treasury Bond ETF", "category": "bonds"},
    "IEF": {"name": "iShares 7-10 Year Treasury Bond ETF", "category": "bonds"},
    "LQD": {"name": "iShares iBoxx $ Investment Grade Corporate Bond ETF", "category": "bonds"},
    "HYG": {"name": "iShares iBoxx $ High Yield Corporate Bond ETF", "category": "bonds"},
    # ---- international ----
    "EFA": {"name": "iShares MSCI EAFE ETF", "category": "international"},
    "EEM": {"name": "iShares MSCI Emerging Markets ETF", "category": "international"},
    # ---- commodities / FX ----
    "GLD": {"name": "SPDR Gold Shares", "category": "commodities"},
    "USO": {"name": "United States Oil Fund", "category": "commodities"},
    "UUP": {"name": "Invesco DB US Dollar Index Bullish Fund", "category": "fx"},
}


def etf_tickers() -> list[str]:
    """Sorted ETF ticker list (for CLI --ticker arguments)."""
    return sorted(ETFS)
