import sqlite3, datetime
from data_manager.universe import update_prices, universe_tickers
from data_manager.providers.fmp import FMPProvider
con = sqlite3.connect("/Users/jlarkin/.prime/agent/data_manager.db")
covered = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM prices")}
con.close()
todo = [t for t in universe_tickers() if t not in covered]
print("retry2:", len(todo), todo)
n = update_prices(todo, "2016-08-01", datetime.date.today().isoformat(), provider=FMPProvider(), pace=0.6)
print("retry2 done, rows added:", n)
