#!/bin/bash
cd /Users/jlarkin/dev/data-manager
LOG=logs/perf_after.log
: > "$LOG"
uv run python - <<'PY' 2>&1 | tee "$LOG"
import sqlite3, time, os
db = os.path.expanduser('~/.prime/agent/data_manager.db')
conn = sqlite3.connect(db)
def t1(label, sql, params=()):
    best = 1e9
    for _ in range(3):
        t0 = time.time(); conn.execute(sql, params).fetchall(); best = min(best, time.time()-t0)
    print('%s %.1f ms' % (label, best*1000), flush=True)
    return best
t1('cross-section 2024-06-28', "SELECT ticker, close, volume FROM prices WHERE date=?",
   ('2024-06-28',))
t1('per-ticker 5y AAPL', 'SELECT COUNT(*) FROM prices WHERE ticker=? AND date>=?',
   ('AAPL','2021-01-01'))
t1('sf1 PIT shares AAPL', "SELECT shareswa FROM sf1 WHERE ticker=? AND dimension IN ('ARQ','ARY') AND date<=? ORDER BY date DESC LIMIT 1",
   ('AAPL','2026-08-11'))
t1('pit members 2020-03-23', 'SELECT COUNT(*) FROM universe_pit WHERE as_of=?',
   ('2020-03-23',))
t1('master categories', 'SELECT COUNT(*) FROM securities_master WHERE category IN (?,?,?)',
   ('Domestic Common Stock','Domestic Common Stock Primary Class','Domestic Common Stock Secondary Class'))
print('size_mb', round(os.path.getsize(db)/1e6,1), flush=True)
PY
