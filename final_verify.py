"""End-of-pipeline verification: asserts coverage minima and prints a report."""
import sqlite3
con = sqlite3.connect("/Users/jlarkin/.prime/agent/data_manager.db")
ok = True

def cnt(sql):
    return con.execute(sql).fetchone()[0]

lines = []
lines.append("=== FINAL DATA-MANAGER REPORT ===")
n_u = cnt("SELECT COUNT(*) FROM universe")
for c in ["figi","cik","sic"]:
    n = cnt(f"SELECT COUNT(*) FROM universe WHERE {c} IS NOT NULL AND {c} != ''")
    lines.append(f"  universe.{c}: {n}/{n_u}")
n_price = cnt("SELECT COUNT(*) FROM prices")
n_pricet = cnt("SELECT COUNT(DISTINCT ticker) FROM prices")
lines.append(f"  prices: {n_price:,} rows across {n_pricet} tickers ({n_pricet/n_u*100:.0f}% of universe)")
n_fund = cnt("SELECT COUNT(*) FROM fundamentals")
n_fundt = cnt("SELECT COUNT(DISTINCT ticker) FROM fundamentals")
lines.append(f"  fundamentals: {n_fund} rows across {n_fundt} tickers")
n_rat = cnt("SELECT COUNT(*) FROM ratios")
lines.append(f"  ratios: {n_rat} snapshots")
n_ff = cnt("SELECT COUNT(*) FROM french_factors")
ff_min, ff_max = con.execute("SELECT MIN(date), MAX(date) FROM french_factors").fetchone()
lines.append(f"  french_factors: {n_ff} rows ({ff_min} -> {ff_max})")

# assertions
asserts = [
    ("prices present", n_pricet > n_u * 0.75),
    ("fundamentals present", n_fundt > 0),
    ("ratios present", n_rat > 0),
    ("french factors present", n_ff > 20000),
    ("sic done", cnt("SELECT COUNT(*) FROM universe WHERE sic IS NOT NULL AND sic!=''") > 2500),
]
for name, passed in asserts:
    ok = ok and passed
    lines.append(("  PASS " if passed else "  FAIL ") + name)
print("\n".join(lines))
print("OVERALL:", "OK" if ok else "NOT DONE")
