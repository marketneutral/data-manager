import os
os.environ["FMP_API_KEY"] = [l.split("=",1)[1].strip() for l in open("/Users/jlarkin/.env") if l.startswith("FMP_API_KEY=")][0]
from data_manager.universe import update_fundamentals, universe_tickers
from data_manager.providers.fmp import FMPProvider
n = update_fundamentals(universe_tickers(), provider=FMPProvider(), pace=0.4)
print(f"FMP fundamentals full rebuild complete: {n} rows")
