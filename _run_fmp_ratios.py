import os, sqlite3, time, threading
os.environ["FMP_API_KEY"] = [l.split("=",1)[1].strip() for l in open("/Users/jlarkin/.env") if l.startswith("FMP_API_KEY=")][0]
from data_manager.providers.fmp import FMPProvider
DB = "/Users/jlarkin/.prime/agent/data_manager.db"
RATIO_COLS = ["trailing_pe","forward_pe","price_to_book","price_to_sales","roe","roa",
              "net_margin","gross_margin","operating_margin","debt_to_equity",
              "current_ratio","dividend_yield","market_cap","enterprise_value",
              "ev_to_ebitda","beta","shares_outstanding"]

con = sqlite3.connect(DB)
tickers = [r[0] for r in con.execute("SELECT DISTINCT ticker FROM universe")]
have = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM ratios")}
todo = [t for t in tickers if t not in have]
con.close()
print(f"ratios FMP: {len(todo)} pending", flush=True)

provider = FMPProvider()
lock = threading.Lock()
count = 0
def work(ticker):
    global count
    try:
        r = provider.get_ratios(ticker)
    except Exception as e:
        print(f"[ratios] {ticker}: {type(e).__name__}", flush=True); return
    if not r:
        return
    cols = ", ".join(RATIO_COLS); marks = ", ".join(["?"] * len(RATIO_COLS))
    conn = sqlite3.connect(DB)
    conn.execute(f"INSERT OR REPLACE INTO ratios (ticker, as_of, {cols}) VALUES (?, date('now'), {marks})",
                 (ticker,) + tuple(r.get(c) for c in RATIO_COLS))
    conn.commit(); conn.close()
    with lock:
        count += 1
        if count % 200 == 0:
            print(f"[ratios] {count} done", flush=True)

workers = 3
while todo:
    chunk, todo = todo[:workers], todo[workers:]
    ts = [threading.Thread(target=work, args=(t,)) for t in chunk]
    [t.start() for t in ts]; [t.join() for t in ts]
    time.sleep(0.2)  # throttle: ~3 req/ticker * 3 workers / 1.1s ~= 500/min < 750 cap
print(f"ratios FMP complete: {count}")
