"""Build data_quality_report.html: coverage/missingness/timeframe spec sheet.

Light, navy, clean-grid spec-sheet style (Columbia-deck conventions).
Run: python3 make_spec_sheet.py  ->  data_quality_report.html
"""
import json, sqlite3, datetime, os

DB = os.environ.get("DATA_MANAGER_DB", "/Users/jlarkin/.prime/agent/data_manager.db")
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row

def one(sql, args=()):
    r = con.execute(sql, args).fetchone()
    return r[0] if r else None

def pair(sql, args=()):
    r = con.execute(sql, args).fetchone()
    return (r[0], r[1])

D = {}
D["generated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
D["db_size_mb"] = round(os.path.getsize(DB) / 1e6, 1)
D["universe_total"] = one("SELECT COUNT(*) FROM universe")
s = con.execute("SELECT source, pulled_at, as_of, row_count FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
D["snap"] = {"source": s[0], "pulled": s[1], "as_of": s[2], "rows": s[3]}

D["ids"] = []
for col in ["figi", "cik", "sic", "sic_description", "lei"]:
    n = one(f"SELECT COUNT(*) FROM universe WHERE {col} IS NOT NULL AND {col} != ''")
    D["ids"].append({"key": col, "have": n, "pct": round(n / D["universe_total"] * 100, 1)})

D["class_rows"] = one("SELECT COUNT(*) FROM classifications")
D["class_sector"] = one("SELECT COUNT(*) FROM classifications WHERE sector IS NOT NULL AND sector!=''")
D["class_industry"] = one("SELECT COUNT(*) FROM classifications WHERE industry IS NOT NULL AND industry!=''")

D["prices_rows"] = one("SELECT COUNT(*) FROM prices")
D["prices_tickers"] = one("SELECT COUNT(DISTINCT ticker) FROM prices")
D["prices_min"], D["prices_max"] = pair("SELECT MIN(date), MAX(date) FROM prices")
rng = one("""SELECT COUNT(*) FROM (
  SELECT ticker FROM prices GROUP BY ticker
  HAVING MIN(date) <= '2016-08-01' AND MAX(date) >= '2025-08-01')""")
D["p_full10"] = rng
D["p_median_days"] = one("SELECT CAST(AVG(d) AS INT) FROM (SELECT ticker, COUNT(DISTINCT date) d FROM prices GROUP BY ticker)")
D["p_adj_min"], D["p_adj_max"] = pair("SELECT MIN(adjustment), MAX(adjustment) FROM prices")
missing = [r[0] for r in con.execute("SELECT ticker FROM universe WHERE ticker NOT IN (SELECT DISTINCT ticker FROM prices) ORDER BY ticker")]
D["p_missing"] = len(missing)
D["p_missing_sample"] = missing[:14]

D["fund_rows"] = one("SELECT COUNT(*) FROM fundamentals")
D["fund_tickers"] = one("SELECT COUNT(DISTINCT ticker) FROM fundamentals")
D["fund_fy_min"], D["fund_fy_max"] = pair("SELECT MIN(fiscal_year), MAX(fiscal_year) FROM fundamentals")
D["fscore"] = [one(f"SELECT COUNT(*) FROM fundamentals WHERE f_score={i}") for i in range(10)]

D["quart_rows"] = one("SELECT COUNT(*) FROM quarterly_statements")
D["quart_tickers"] = one("SELECT COUNT(DISTINCT ticker) FROM quarterly_statements")
D["quart_min"], D["quart_max"] = pair("SELECT MIN(period), MAX(period) FROM quarterly_statements")
D["rat_rows"] = one("SELECT COUNT(*) FROM ratios")
D["rat_tickers"] = one("SELECT COUNT(DISTINCT ticker) FROM ratios")
D["rat_asof"] = one("SELECT MAX(as_of) FROM ratios")
rc = ["trailing_pe","forward_pe","price_to_book","roe","net_margin","debt_to_equity","dividend_yield","beta"]
D["rat_missing"] = {c: one(f"SELECT COUNT(*) FROM ratios WHERE {c} IS NULL") for c in rc}

D["ff_rows"] = one("SELECT COUNT(*) FROM french_factors")
D["ff_min"], D["ff_max"] = pair("SELECT MIN(date), MAX(date) FROM french_factors")
D["ff_cols"] = {c: one(f"SELECT COUNT(*) FROM french_factors WHERE {c} IS NOT NULL")
                for c in ["mkt_rf","smb","hml","rmw","cma","mom","st_rev","lt_rev","rf"]}
con.close()

payload = json.dumps(D, default=str)

HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Data Manager — Spec Sheet</title>
<style>
  :root { --navy:#1A2C64; --navy2:#002060; --blue:#6CACE4; --lblue:#B9D9EB;
          --accent:#27BFEE; --ink:#3F3F3F; --green:#00B050; --red:#FF0000; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:Calibri,'Segoe UI',Helvetica,Arial,sans-serif; color:var(--ink);
         background:#fff; padding:34px 42px; max-width:1080px; margin:0 auto; }
  h1 { color:var(--navy); font-size:26px; border-bottom:2px solid var(--navy); padding-bottom:8px; }
  .sub { color:var(--navy2); font-size:13px; margin:4px 0 22px; }
  h2 { color:var(--navy); font-size:17px; margin:26px 0 10px; font-weight:700; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; margin:6px 0 4px; }
  th { text-align:left; font-weight:700; color:var(--navy2); border-bottom:1px solid var(--navy);
       font-size:12px; padding:5px 8px; }
  td { border-bottom:1px solid #dfe5ee; padding:5px 8px; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .bar { display:inline-block; height:9px; border-radius:4px; vertical-align:middle; margin-right:8px; }
  .b-full { background:var(--blue); } .b-part { background:var(--lblue); } .b-low { background:#f4c6c6; }
  .badge { display:inline-block; padding:1px 9px; border-radius:10px; font-size:10.5px; font-weight:700; }
  .ok { background:#e4f5e9; color:var(--green); } .gap { background:#fdeaea; color:var(--red); }
  .warn { background:#fff4d6; color:#9a6b00; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:0 36px; }
  .note { font-size:11px; color:var(--navy2); margin-top:4px; }
  .foot { margin-top:30px; padding-top:10px; border-top:1px solid var(--navy);
          font-size:10.5px; color:var(--navy2); }
  .kpis { display:flex; gap:14px; flex-wrap:wrap; margin:14px 0 6px; }
  .kpi { border:1px solid var(--navy); border-radius:8px; padding:10px 16px; min-width:130px; }
  .kpi .v { font-size:22px; color:var(--navy); font-weight:700; }
  .kpi .l { font-size:11px; color:var(--navy2); }
</style></head><body>
<h1>Data Manager — Data Spec Sheet</h1>
<div class="sub">Generated __generated__ · SQLite __db_size_mb__ MB · local only</div>

<div class="kpis">
  <div class="kpi"><div class="v">__universe_total__</div><div class="l">Universe tickers</div></div>
  <div class="kpi"><div class="v">__prices_tickers__</div><div class="l">Tickers w/ prices</div></div>
  <div class="kpi"><div class="v">{prices_rows:,}</div><div class="l">Price rows (10y)</div></div>
  <div class="kpi"><div class="v">__fund_tickers__</div><div class="l">Tickers w/ F-scores</div></div>
  <div class="kpi"><div class="v">__rat_tickers__</div><div class="l">Tickers w/ ratios</div></div>
  <div class="kpi"><div class="v">__quart_tickers__</div><div class="l">Tickers w/ quarterly</div></div>
</div>

<h2>1 · Universe (security master)</h2>
<table><tr><th>Source</th><th>Holding date (as-of)</th><th>Pulled</th><th class="num">Rows</th><th class="num">Unique</th></tr>
<tr><td>__snap_source___</td><td>__snap_as_of___</td><td>__snap_pulled___</td><td class="num">__snap_rows___</td><td class="num">__universe_total__</td></tr></table>

<h2>2 · Identifier coverage (missingness)</h2>
<table>
<tr><th>Identifier</th><th class="num">Have</th><th class="num">Missing</th><th style="width:44%">Coverage</th><th class="num">%</th></tr>
__id_rows__
</table>
<div class="note">LEI is an acknowledged gap: SEC only carries LEIs that companies registered (free source limit).</div>

<h2>3 · Classifications</h2>
<div class="grid">
<table>
<tr><th>Field</th><th class="num">Covered</th><th class="num">%</th></tr>
<tr><td>Sector (from IWV CSV)</td><td class="num">__class_sector__</td><td class="num">__class_sector_pct__%</td></tr>
<tr><td>Industry (FMP)</td><td class="num">__class_industry__</td><td class="num">__class_industry_pct__%</td></tr>
</table>
<div class="note">Industry missing on delisted/futures-symbol rows (e.g. ESU6, MSFUT) — non-equity CSV artifacts.</div>
</div>

<h2>4 · Prices — as-of OHLCV + adjustment (10-year window)</h2>
<table>
<tr><th>Metric</th><th class="num">Value</th></tr>
<tr><td>Rows</td><td class="num">{prices_rows:,}</td></tr>
<tr><td>Distinct tickers</td><td class="num">__prices_tickers__ / __universe_total__ (__prices_pct__%)</td></tr>
<tr><td>Date range</td><td class="num">__prices_min__ → __prices_max__</td></tr>
<tr><td>Tickers with full ~10y span (2016-08 → 2025+)</td><td class="num">__p_full10__</td></tr>
<tr><td>Median days per ticker</td><td class="num">__p_median_days__</td></tr>
<tr><td>Adjustment factor range</td><td class="num">__p_adj_min__ → __p_adj_max__</td></tr>
<tr><td>Missing (no price rows)</td><td class="num">__p_missing__</td></tr>
</table>
<div class="note">Missing tickers sample: __p_missing_sample__. Excludes non-equity CSV artifacts; delisted names will never recover.</div>

<h2>5 · Fundamentals — Piotroski F-Score (9 signals)</h2>
<div class="grid">
<table>
<tr><th>Metric</th><th class="num">Value</th></tr>
<tr><td>Rows</td><td class="num">{fund_rows:,}</td></tr>
<tr><td>Tickers covered</td><td class="num">__fund_tickers__ / __universe_total__ (__fund_pct__%)</td></tr>
<tr><td>Fiscal years</td><td class="num">__fund_fy_min__ → __fund_fy_max__</td></tr>
</table>
<div>
<table><tr><th>F-Score</th><th class="num">Companies</th><th style="width:52%">Distribution</th></tr>
__fscore_rows__
</table>
</div>
</div>

<h2>6 · Quarterly statements (FMP, ~10y)</h2>
<table><tr><th>Metric</th><th class="num">Value</th></tr>
<tr><td>Rows</td><td class="num">__quart_rows_comma__</td></tr>
<tr><td>Tickers covered</td><td class="num">__quart_tickers__ / __universe_total__ (__quart_pct__%)</td></tr>
<tr><td>Period range</td><td class="num">__quart_min__ → __quart_max__</td></tr></table>
<div class="note">Income/balance/cash-flow key items per fiscal quarter (FMP period=quarter, limit=40 ≈ 10y).</div>

<h2>7 · Ratios — point-in-time snapshot (__rat_asof__)</h2>
<div class="grid">
<table><tr><th>Metric</th><th class="num">Value</th></tr>
<tr><td>Snapshots</td><td class="num">__rat_rows__</td></tr>
<tr><td>Tickers covered</td><td class="num">__rat_tickers__ / __universe_total__ (__rat_pct__%)</td></tr></table>
<table>
<tr><th>Ratio field</th><th class="num">Missing</th><th style="width:42%">Populated</th></tr>
__rat_rows_html__
</table>
</div>

<h2>8 · Ken French daily factor returns</h2>
<table><tr><th>Metric</th><th class="num">Value</th></tr>
<tr><td>Rows (trading days)</td><td class="num">__ff_rows_comma__</td></tr>
<tr><td>Date range</td><td class="num">__ff_min__ → __ff_max__</td></tr>
<tr><td>Mkt-RF / SMB / HML populated</td><td class="num">__ff_mkt_rf__</td></tr>
<tr><td>RMW / CMA populated</td><td class="num">__ff_rmw__ / __ff_cma__</td></tr>
<tr><td>Mom / ST_Rev / LT_Rev populated</td><td class="num">__ff_mom__ / __ff_st_rev__ / __ff_lt_rev__</td></tr>
<tr><td>RF populated</td><td class="num">__ff_rf__</td></tr></table>
<div class="note">Percent per day; vendor -99.99 missing → NULL. Ken French Data Library (free); refreshed by update-french-factors / bulk-update.</div>

<div class="foot">Pipeline: universe (iShares IWV) → identifiers (FIGI/CIK/SIC via OpenFigi+SEC) → classifications →
prices (as-traded, resumable/paced) → fundamentals (Piotroski) → ratios (FMP TTM) → french factors (Ken French). All local · status = live coverage snapshot.</div>
</body></html>"""

def bar(pct):
    cls = "b-full" if pct >= 90 else ("b-part" if pct >= 40 else "b-low")
    w = max(2.0, pct)
    return f'<span class="bar {cls}" style="width:{min(100,w)}%"></span>'

id_rows = []
MAXP = max((i["pct"] for i in D["ids"]), default=100)
for i in D["ids"]:
    miss = D["universe_total"] - i["have"]
    badge = '<span class="badge ok">OK</span>' if i["pct"] >= 90 else ('<span class="badge warn">GAP</span>' if i["pct"] >= 40 else '<span class="badge gap">LOW</span>')
    id_rows.append(f"<tr><td>{i['key']}</td><td class='num'>{i['have']}</td><td class='num'>{miss}</td>"
                   f"<td>{bar(i['pct'])}</td><td class='num'>{i['pct']}% {badge}</td></tr>")

fscore_max = max(D["fscore"]) if max(D["fscore"]) else 1
fscore_rows = []
for i, n in enumerate(D["fscore"]):
    pct = (n / fscore_max * 100) if n else 0
    w = max(2.0, pct) if n else 0
    col = "var(--green)" if i >= 7 else ("var(--blue)" if i >= 4 else "#cbd4e4")
    fscore_rows.append(f"<tr><td>{i}</td><td class='num'>{n}</td><td><span class='bar' style='width:{min(100,w)}%;background:{col}'></span>{n}</td></tr>")

rat_rows_html = []
for c, n in D["rat_missing"].items():
    pct = round((D["rat_tickers"] - n) / D["rat_tickers"] * 100, 0) if D["rat_tickers"] else 0
    rat_rows_html.append(f"<tr><td>{c}</td><td class='num'>{n}</td><td>{bar(pct)}<span style='font-size:10.5px'>{int(pct)}%</span></td></tr>")

def fmt(x): return f"{x:,}"


repl = {
    "__generated__": D["generated"], "__db_size_mb__": D["db_size_mb"],
    "__universe_total__": D["universe_total"],
    "__snap_source__": D["snap"]["source"], "__snap_as_of__": D["snap"]["as_of"],
    "__snap_pulled__": D["snap"]["pulled"], "__snap_rows__": D["snap"]["rows"],
    "__prices_rows__": f"{D['prices_rows']:,}", "__prices_tickers__": D["prices_tickers"],
    "__prices_pct__": round(D["prices_tickers"]/D["universe_total"]*100,1),
    "__prices_min__": D["prices_min"], "__prices_max__": D["prices_max"],
    "__p_full10__": D["p_full10"], "__p_median_days__": D["p_median_days"],
    "__p_adj_min__": round(D["p_adj_min"],4), "__p_adj_max__": round(D["p_adj_max"],4),
    "__p_missing__": D["p_missing"], "__p_missing_sample__": ", ".join(D["p_missing_sample"]),
    "__fund_rows__": f"{D['fund_rows']:,}", "__fund_tickers__": D["fund_tickers"],
    "__fund_pct__": round(D["fund_tickers"]/D["universe_total"]*100,1),
    "__fund_fy_min__": D["fund_fy_min"], "__fund_fy_max__": D["fund_fy_max"],
    "__quart_rows_comma__": f"{D['quart_rows']:,}", "__quart_tickers__": D["quart_tickers"],
    "__quart_pct__": round(D["quart_tickers"]/D["universe_total"]*100,1),
    "__quart_min__": D["quart_min"], "__quart_max__": D["quart_max"],
    "__rat_rows__": D["rat_rows"], "__rat_tickers__": D["rat_tickers"],
    "__rat_pct__": round(D["rat_tickers"]/D["universe_total"]*100,1), "__rat_asof__": D["rat_asof"],
    "__class_sector__": D["class_sector"], "__class_sector_pct__": round(D["class_sector"]/D["universe_total"]*100,1),
    "__class_industry__": D["class_industry"], "__class_industry_pct__": round(D["class_industry"]/D["universe_total"]*100,1),
    "__id_rows__": "\n".join(id_rows), "__fscore_rows__": "\n".join(fscore_rows),
    "__rat_rows_html__": "\n".join(rat_rows_html),
    "__ff_rows_comma__": f"{D['ff_rows']:,}",
    "__ff_min__": D["ff_min"], "__ff_max__": D["ff_max"],
    "__ff_mkt_rf__": f"{D['ff_cols']['mkt_rf']:,}", "__ff_rmw__": f"{D['ff_cols']['rmw']:,}",
    "__ff_cma__": f"{D['ff_cols']['cma']:,}", "__ff_mom__": f"{D['ff_cols']['mom']:,}",
    "__ff_st_rev__": f"{D['ff_cols']['st_rev']:,}", "__ff_lt_rev__": f"{D['ff_cols']['lt_rev']:,}",
    "__ff_rf__": f"{D['ff_cols']['rf']:,}",
}
html = HTML
for k, v in repl.items():
    html = html.replace(k, str(v))
import re as _re
html = _re.sub(r"\{([a-z_]+):,\}", lambda m: f"{D[m.group(1)]:,}", html)  # legacy fmt tokens
open("/Users/jlarkin/dev/data-manager/data_quality_report.html", "w").write(html)
print("wrote data_quality_report.html", len(html), "bytes")
