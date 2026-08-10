"""data-manager: acquire, store, and update market data for the quant research stack.

- Universe (R3000 constituents) via the free iShares IWV CSV.
- Prices (as-traded, split jumps preserved), classifications, fundamentals
  (Piotroski F-Score), ratios, and quarterly statements via FMP.

Data is stored in a SQLite database (default ~/.prime/agent/data_manager.db).
"""

from . import db
from .universe import (
    update_universe,
    update_prices,
    update_classifications,
    update_fundamentals,
    universe_tickers,
    adjusted_prices,
)

__all__ = [
    "db",
    "update_universe",
    "update_prices",
    "update_classifications",
    "update_fundamentals",
    "universe_tickers",
    "adjusted_prices",
]
