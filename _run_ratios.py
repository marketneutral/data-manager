from data_manager.universe import update_ratios, universe_tickers
from data_manager.providers.fmp import FMPProvider
n = update_ratios(universe_tickers(), provider=FMPProvider())
print(f"ratios done: {n} snapshots")
