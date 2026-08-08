import os
os.environ.pop("FMP_API_KEY", None)
from data_manager.universe import update_ratios, universe_tickers
from data_manager.providers.yfinance import YFinanceProvider
n = update_ratios(universe_tickers(), provider=YFinanceProvider())
print(f"ratios done: {n} snapshots")
