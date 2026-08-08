import os, sqlite3, time, threading
os.environ["FMP_API_KEY"] = [l.split("=",1)[1].strip() for l in open("/Users/jlarkin/.env") if l.startswith("FMP_API_KEY=")][0]
from data_manager.providers.fmp import FMPProvider
DB = "/Users/jlarkin/.prime/agent/data_manager.db"
Q_COLS = ["net_income","revenue","gross_profit","operating_cash_flow","total_assets",
          "total_liabilities","current_assets","current_liabilities","shares_out","roa","cfo"]
con = sqlite3.connect(DB)
tickers = [r[0] for r in con.execute("SELECT DISTINCT ticker FROM universe")]
have = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM quarterly_statements")}
todo = [t for t in tickers if t not in have]
con.close()
print(f"quarterly FMP: {len(todo)} pending", flush=True)
provider = FMPProvider()
lock = threading.Lock()
count = 0
def work(ticker):
    global count
    try:
        rows = provider.get_quarterly(ticker)
    except Exception as e:
        print(f"[q] {ticker}: {type(e).__name__}", flush=True); return
    if not rows:
        return
    cols = ", ".join(Q_COLS); marks = ", ".join(["?"] * len(Q_COLS))
    conn = sqlite3.connect(DB)
    conn.executemany(f"INSERT OR REPLACE INTO quarterly_statements (ticker, period, {cols}) VALUES (?, ?, {marks})",
                     [(ticker, r.get("period")) + tuple(r.get(c) for c in Q_COLS) for r in rows])
    conn.commit(); conn.close()
    with lock:
        count += 1
        if count % 250 == 0:
            print(f"[q] {count} done", flush=True)
workers = 3
while todo:
    chunk, todo = todo[:workers], todo[workers:]
    ts = [threading.Thread(target=work, args=(t,)) for t in chunk]
    [t.start() for t in ts]; [t.join() for t in ts]
    time.sleep(0.2)
print(f"quarterly FMP complete: {count}")
