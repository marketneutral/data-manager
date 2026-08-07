"""data-manager: acquire, store, and update market data for the quant research stack.

- Universe (R3000 constituents) via financialdatasets.ai (paid API).
- Prices, classifications, and fundamentals (Piotroski F-Score) via yfinance (free).

Data is stored in a SQLite database (default ~/.prime/agent/data_manager.db).
"""

from . import db
from .universe import (
    update_universe,
    update_prices,
    update_classifications,
    update_fundamentals,
    universe_tickers,
)

__all__ = [
    "db",
    "update_universe",
    "update_prices",
    "update_classifications",
    "update_fundamentals",
    "universe_tickers",
]
