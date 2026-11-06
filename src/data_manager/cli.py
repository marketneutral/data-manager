import os
"""Command-line interface for the data-manager."""

import argparse
import json
import sys

from . import db
from .universe import (
    update_universe,
    update_prices,
    update_classifications,
    update_fundamentals,
    update_ratios,
    update_quarterly,
    update_master,
    update_actions,
    update_sp500,
    update_master_all,
    update_actions_all,
    update_metrics_all,
    update_sf1_all,
    update_prices_all_stocks,
    build_piotroski,
    build_quarterly,
    build_ratios,
    build_shares_pit,
    build_universe_pit,
    build_universe_pit_history,
    universe_tickers,
)
from . import enrich

BULK_ORDER = ("tickers", "stocks", "actions", "metrics", "sp500", "funds", "fundamentals", "insiders", "sf3")


def _tickers_from_args(args, conn):
    if args.ticker:
        return [t.strip().upper() for t in args.ticker.split(",") if t.strip()]
    if args.all:
        return universe_tickers(conn)
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(prog="data-manager", description="Acquire and update market data.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show table counts + coverage.")
    p_status.add_argument("--db", default=None)
    p_universe = sub.add_parser("update-universe", help="Fetch R3000 constituents (iShares IWV holdings CSV).")
    p_universe.add_argument("--db", default=None, help="SQLite DB path.")

    p_master = sub.add_parser("update-master", help="Mirror Sharadar securities master (--all = whole table).")
    p_master.add_argument("--ticker", default=None)
    p_master.add_argument("--all", action="store_true")
    p_master.add_argument("--db", default=None)
    p_act = sub.add_parser("update-actions", help="Mirror Sharadar corporate actions (--all = whole table).")
    p_act.add_argument("--ticker", default=None)
    p_act.add_argument("--all", action="store_true")
    p_act.add_argument("--db", default=None)
    p_met = sub.add_parser("update-metrics", help="Mirror the Sharadar metrics snapshot (whole table).")
    p_met.add_argument("--all", action="store_true")
    p_met.add_argument("--db", default=None)
    p_sf1 = sub.add_parser("update-sf1", help="Full SF1 mirror, all dimensions ARY/MRY/ARQ/MRQ (batched).")
    p_sf1.add_argument("--all", action="store_true")
    p_sf1.add_argument("--db", default=None)

    p_prices = sub.add_parser("update-prices", help="As-traded OHLCV prices (--all = every stock in master).")
    p_prices.add_argument("--ticker", default=None, help="Comma-separated tickers.")
    p_prices.add_argument("--all", action="store_true", help="All stocks in the securities master.")
    p_prices.add_argument("--start", required=True, help="Start date YYYY-MM-DD.")
    p_prices.add_argument("--end", default=None, help="End date YYYY-MM-DD (default today).")
    p_prices.add_argument("--force", action="store_true",
                         help="refetch even if the ticker already covers --end")
    p_prices.add_argument("--db", default=None)

    p_class = sub.add_parser("update-classifications", help="Sector/industry (from master; GICS-mapped).")
    p_class.add_argument("--ticker", default=None)
    p_class.add_argument("--all", action="store_true")
    p_class.add_argument("--force", action="store_true", help="refetch even if an industry exists")
    p_class.add_argument("--db", default=None)

    p_bp = sub.add_parser("build-piotroski", help="Local: derive Piotroski F-scores from the sf1 ARY mirror.")
    p_bp.add_argument("--db", default=None)
    p_bq = sub.add_parser("build-quarterly", help="Local: derive quarterly statements from the sf1 ARQ mirror.")
    p_bq.add_argument("--db", default=None)
    p_br = sub.add_parser("build-ratios", help="Local: derive ratio snapshots from the sf1 MRY mirror.")
    p_br.add_argument("--db", default=None)
    p_pa = sub.add_parser("pit-audit", help="Check the warehouse's point-in-time invariants; exit 1 on any error.")
    p_pa.add_argument("--db", default=None)
    p_pa.add_argument("--no-record", action="store_true", help="do not append the run to pit_audit_runs")
    p_bs = sub.add_parser("build-shares-pit", help="Local: derive point-in-time shares outstanding (sf1 shares / later splits).")
    p_bs.add_argument("--db", default=None)
    p_pit = sub.add_parser("build-universe-pit", help="Construct a PIT investable universe (master+prices+sf1).")
    p_pit.add_argument("--asof", default=None, help="YYYY-MM-DD (default today; ignored with --history).")
    p_pit.add_argument("--history", action="store_true", help="build the universe for EVERY trading day (1998->now)")
    p_pit.add_argument("--start", default=None, help="history: start date YYYY-MM-DD")
    p_pit.add_argument("--end", default=None, help="history: end date YYYY-MM-DD")
    p_pit.add_argument("--min-price", type=float, default=2.0)
    p_pit.add_argument("--min-mcap", type=float, default=300_000_000.0)
    p_pit.add_argument("--min-dvol", type=float, default=5_000_000.0)
    p_pit.add_argument("--lookback", type=int, default=20, help="trailing days for $volume avg")
    p_pit.add_argument("--min-dvol-days", type=int, default=10)
    p_pit.add_argument("--max-quote-age", type=int, default=10)
    p_pit.add_argument("--types", default=None, help="comma categories (default: Domestic Common Stock + classes)")
    p_pit.add_argument("--db", default=None)

    p_sp500 = sub.add_parser("update-sp500", help="S&P500 membership (current + per-member history).")
    p_sp500.add_argument("--db", default=None)

    p_ff = sub.add_parser("update-french-factors",
                          help="Ken French US daily factor returns (3F / 5F / Mom / ST_Rev / LT_Rev).")
    p_ff.add_argument("--force", action="store_true", help="re-download even if the site file is unchanged")
    p_ff.add_argument("--dir", default=None, help="download cache dir (default ~/.prime/agent/bulk)")
    p_ff.add_argument("--db", default=None)

    p_blk = sub.add_parser("load-bulk", help="Load a downloaded Sharadar bulk zip (tickers/stocks/funds/actions/metrics/sp500/fundamentals).")
    p_blk.add_argument("table", choices=["tickers","stocks","funds","actions","metrics","sp500","fundamentals","insiders","sf3"])
    p_blk.add_argument("--file", required=True, help="path to the .csv.zip")
    p_blk.add_argument("--db", default=None)

    p_bd = sub.add_parser("bulk-download", help="Download Sharadar bulk zips (or check status with --status).")
    p_bd.add_argument("table", choices=["all"] + list(BULK_ORDER))
    p_bd.add_argument("--status", action="store_true", help="only print server file info")
    p_bd.add_argument("--force", action="store_true", help="re-download even if the manifest says up to date")
    p_bd.add_argument("--dir", default=None, help="bulk cache dir (default ~/.prime/agent/bulk)")
    p_bd.add_argument("--db", default=None)
    p_bz = sub.add_parser("bulk-fromzero", help="Full warehouse rebuild from bulk zips (download all, wipe, load, derive, PIT).")
    p_bz.add_argument("--dir", default=None)
    p_bz.add_argument("--asof", default=None, help="PIT universe as-of date (default today)")
    p_bz.add_argument("--no-derive", action="store_true")
    p_bz.add_argument("--no-pit", action="store_true")
    p_bz.add_argument("--db", default=None)
    p_opt = sub.add_parser("optimize-db", help="Backup (optional) + checkpoint + analyze + integrity + vacuum.")
    p_opt.add_argument("--backup", default=None, help="path for a consistent backup before optimizing")
    p_opt.add_argument("--no-vacuum", action="store_true")
    p_opt.add_argument("--quick", action="store_true", help="quick_check instead of full integrity_check")
    p_opt.add_argument("--db", default=None)
    p_bu = sub.add_parser("bulk-update", help="Incremental update (manifest-skipped downloads + reload + derive).")
    p_bu.add_argument("--tables", default=None, help="comma list (default: all)")
    p_bu.add_argument("--force", action="store_true")
    p_bu.add_argument("--dir", default=None)
    p_bu.add_argument("--asof", default=None)
    p_bu.add_argument("--no-derive", action="store_true")
    p_bu.add_argument("--no-pit", action="store_true")
    p_bu.add_argument("--db", default=None)

    p_sl = sub.add_parser("store-load", help="Load vendor files into the store (warehouse.db): convert, compare, gate, commit.")
    p_sl.add_argument("--dir", default=None, help="bulk zip directory for a full load (default ~/.prime/agent/bulk)")
    p_sl.add_argument("--file", action="append", default=[], help="name=path for a vendor file (stocks, funds, tickers, actions, fundamentals)")
    p_sl.add_argument("--increment", action="store_true", help="the files carry only what changed (a nightly load)")
    p_sl.add_argument("--store", default=None)
    p_sl.add_argument("--note", default=None)
    p_ss = sub.add_parser("store-status", help="The store's loads and current row counts.")
    p_ss.add_argument("--store", default=None)
    p_sa = sub.add_parser("store-accept", help="Commit a quarantined load after review.")
    p_sa.add_argument("load_id", type=int)
    p_sa.add_argument("--by", required=True)
    p_sa.add_argument("--note", default=None)
    p_sa.add_argument("--store", default=None)
    p_sr = sub.add_parser("store-reject", help="Reject a quarantined load.")
    p_sr.add_argument("load_id", type=int)
    p_sr.add_argument("--by", required=True)
    p_sr.add_argument("--note", default=None)
    p_sr.add_argument("--store", default=None)

    p_ep = sub.add_parser("export-panel", help="Build a versioned panel (parquet + panel.json) from the store.")
    p_ep.add_argument("--out", default=os.path.expanduser("~/data/panels"))
    p_ep.add_argument("--store", default=None)
    p_ep.add_argument("--load", type=int, default=None, help="build as of this load (default: the last accepted)")
    p_ep.add_argument("--end", default=None, help="last date to include (YYYY-MM-DD)")

    args = parser.parse_args(argv)
    if args.command == "export-panel":
        from . import panel as P
        folder, m = P.build(args.out, store=args.store, at=args.load, end=args.end)
        print(f"panel {m['version']}: {m['n_tickers']:,} stocks x {m['n_dates']:,} days, {m['start']} to {m['end']}, from load {m['store']['load_id']} -> {folder}")
        return 0
    if args.command.startswith("store-"):
        return _store_command(args)
    conn = db.connect(args.db)

    if args.command == "status":
        def cnt(tbl, col=None, extra=""):
            q = f"SELECT COUNT(*) FROM {tbl} {extra}"
            return conn.execute(q).fetchone()[0]
        n_sp = conn.execute("SELECT COUNT(DISTINCT ticker) FROM sp500_membership").fetchone()[0]
        n_sf1 = conn.execute("SELECT COUNT(*) FROM sf1").fetchone()[0]
        n_met = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        n_pit = conn.execute("SELECT COUNT(DISTINCT as_of) FROM universe_pit").fetchone()[0]
        n_mas = conn.execute("SELECT COUNT(*) FROM securities_master").fetchone()[0]
        print(f"universe:        {cnt('universe')}")
        print(f"master:         {n_mas}  (stocks: {conn.execute('SELECT COUNT(*) FROM securities_master WHERE "table"=\'stocks\'').fetchone()[0]})")
        print(f"corp_actions:   {cnt('corporate_actions')}")
        print(f"metrics:        {n_met}")
        print(f"sf1 mirror:     {n_sf1} rows")
        print(f"prices:         {cnt('prices')}")
        print(f"classifications:{cnt('classifications')}")
        print(f"fundamentals:   {cnt('fundamentals')}")
        print(f"quarterly:      {cnt('quarterly_statements')}")
        print(f"ratios:         {cnt('ratios')}")
        print(f"sp500 members:  {n_sp} tickers")
        print(f"universe_pit:   {n_pit} as-of dates built")
        n_ins = conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0]
        n_ff = conn.execute("SELECT COUNT(*) FROM french_factors").fetchone()[0]
        ff_min, ff_max = conn.execute(
            "SELECT MIN(date), MAX(date) FROM french_factors").fetchone()
        ff_max = ff_max or "-"
        print(f"french_factors: {n_ff} rows ({ff_min or '-'} -> {ff_max})")
        ins_min, ins_max = conn.execute(
            "SELECT MIN(filingdate), MAX(filingdate) FROM insider_transactions").fetchone()
        print(f"insider_trades: {n_ins} rows ({ins_min or '-'} -> {ins_max or '-'})")
        n_io = conn.execute("SELECT COUNT(*) FROM institutional_ownership").fetchone()[0]
        io_min, io_max = conn.execute(
            "SELECT MIN(calendardate), MAX(calendardate) FROM institutional_ownership").fetchone()
        print(f"inst_ownership: {n_io} rows ({io_min or '-'} -> {io_max or '-'})")
    elif args.command == "update-universe":
        n = update_universe(conn)
        print(f"Stored {n} universe tickers.")
    elif args.command == "update-master":
        if args.all:
            n = update_master_all(conn)
            print(f"Securities master rows stored: {n}.")
        else:
            tickers = _tickers_from_args(args, conn)
            if not tickers:
                print("No tickers. Pass --ticker or --all.")
                return 1
            n = update_master(tickers, conn)
            print(f"Securities master rows for {n} tickers.")
    elif args.command == "update-actions":
        if args.all:
            n = update_actions_all(conn)
            print(f"Corporate action rows stored: {n}.")
        else:
            tickers = _tickers_from_args(args, conn)
            if not tickers:
                print("No tickers. Pass --ticker or --all.")
                return 1
            n = update_actions(tickers, conn)
            print(f"Corporate action rows: {n}.")
    elif args.command == "update-metrics":
        n = update_metrics_all(conn)
        print(f"Metrics rows stored: {n}.")
    elif args.command == "update-sf1":
        n = update_sf1_all(conn)
        print(f"SF1 rows stored: {n}.")
    elif args.command == "update-prices":
        import datetime as dt
        end = args.end or dt.date.today().isoformat()
        if args.all:
            n = update_prices_all_stocks(conn, args.start, end)
            print(f"Stored {n} price rows across all stocks.")
        else:
            tickers = _tickers_from_args(args, conn)
            if not tickers:
                print("No tickers. Pass --ticker or --all.")
                return 1
            n = update_prices(tickers, args.start, end, conn, force=getattr(args, "force", False))
            print(f"Stored {n} price rows for {len(tickers)} tickers.")
    elif args.command == "update-classifications":
        tickers = _tickers_from_args(args, conn)
        if not tickers:
            print("No tickers. Pass --ticker or --all.")
            return 1
        n = update_classifications(tickers, conn, force=args.force)
        print(f"Updated classifications for {n} tickers.")
    elif args.command == "build-piotroski":
        n = build_piotroski(conn)
        print(f"F-score rows derived: {n}.")
    elif args.command == "build-quarterly":
        n = build_quarterly(conn)
        print(f"Quarterly rows derived: {n}.")
    elif args.command == "build-ratios":
        n = build_ratios(conn)
        print(f"Ratio snapshots derived: {n}.")
    elif args.command == "pit-audit":
        from . import pit_audit
        res = pit_audit.run(conn, record=not args.no_record)
        print(f"[pit-audit] overall: {res['status']}")
        return 1 if res["status"] == "error" else 0
    elif args.command == "build-shares-pit":
        n = build_shares_pit(conn)
        print(f"PIT share-count rows derived: {n}.")
    elif args.command == "build-universe-pit":
        types = tuple(args.types.split(",")) if args.types else None
        if args.history:
            n = build_universe_pit_history(conn, min_price=args.min_price,
                                           min_mcap=args.min_mcap, min_dvol=args.min_dvol,
                                           lookback=args.lookback,
                                           min_dvol_days=args.min_dvol_days,
                                           max_quote_age=args.max_quote_age,
                                           types=types, start=args.start, end=args.end)
            print(f"PIT universe history built: {n} stock-days.")
        else:
            n = build_universe_pit(conn, as_of=args.asof, min_price=args.min_price,
                                   min_mcap=args.min_mcap, min_dvol=args.min_dvol,
                                   lookback=args.lookback, min_dvol_days=args.min_dvol_days,
                                   max_quote_age=args.max_quote_age, types=types)
            print(f"PIT universe members as of {args.asof or 'today'}: {n}.")
    elif args.command == "update-sp500":
        n = update_sp500(conn)
        print(f"S&P500 membership rows: {n}.")
    elif args.command == "load-bulk":
        from . import bulkload as BL
        fn = {"tickers": BL.load_tickers, "stocks": BL.load_stocks, "funds": BL.load_funds,
              "actions": BL.load_actions, "metrics": BL.load_metrics, "sp500": BL.load_sp500,
              "fundamentals": BL.load_fundamentals, "insiders": BL.load_insiders,
              "sf3": BL.load_institutional_ownership}[args.table]
        n = fn(args.file, conn)
        print(f"Loaded {n} rows into {args.table}.")
    elif args.command == "optimize-db":
        from . import dbopt as OP
        OP.optimize_db(conn, backup_path=args.backup, vacuum=not args.no_vacuum, quick=args.quick)
    elif args.command == "update-french-factors":
        from . import factors as FF
        report = FF.update_french_factors(conn, dest_dir=args.dir or FF.DEFAULT_DIR,
                                          force=args.force)
        print(f"French factor rows in db: {report['rows']:,} "
              f"(downloaded: {report['downloaded']}, skipped: {report['skipped']})")
    elif args.command in ("bulk-download", "bulk-fromzero", "bulk-update"):
        from . import bulk as B
        d = args.dir or B.DEFAULT_DIR
        if args.command == "bulk-download":
            if args.status:
                tables = BULK_ORDER if args.table == "all" else [args.table]
                for t in tables:
                    info = B.bulk_status(t)
                    f = next((x for x in info.get("files", []) if x.get("history") == "full"), None)
                    print(f"{t}: {f}")
                return 0
            tables = BULK_ORDER if args.table == "all" else [args.table]
            for t in tables:
                B.sync_table(t, d, force=args.force)
            print("Download pass complete.")
        elif args.command == "bulk-fromzero":
            counts = B.bulk_fromzero(dest_dir=d, conn=conn, pit_asof=args.asof,
                                     derive=not args.no_derive, pit=not args.no_pit)
            print("FROM-ZERO DONE:", json.dumps(counts))
            if counts.get("pit_audit") == "error":
                print("[pit-audit] ERROR: a point-in-time invariant failed; see pit_audit_runs")
                return 1
        else:
            tabs = [t.strip() for t in args.tables.split(",")] if args.tables else None
            res = B.bulk_update(dest_dir=d, conn=conn, tables=tabs, force=args.force,
                                derive=not args.no_derive, pit=not args.no_pit, pit_asof=args.asof)
            print("UPDATE DONE: loaded", res["loaded"], "| skipped:", res["skipped"])
            if res["loaded"].get("pit_audit") == "error":
                print("[pit-audit] ERROR: a point-in-time invariant failed; see pit_audit_runs")
                return 1
    return 0


def _store_command(args):
    """The store's commands; they never open the legacy data_manager.db."""
    from .store import load as SL, read as SR, sharadar as SA
    from .store.schema import connect as store_connect
    if args.command == "store-load":
        files = SA.bulk_files(args.dir or os.path.expanduser("~/.prime/agent/bulk")) if not args.file else {}
        files.update(dict(f.split("=", 1) for f in args.file))
        r = SL.load(files, mode="increment" if args.increment else "full", store=args.store, note=args.note)
        print(json.dumps(dict(load_id=r["load_id"], status=r["status"], counts=r["summary"]["counts"],
                              findings=r["summary"]["findings"], audit=r["summary"].get("audit")), indent=1, default=str))
        return 0 if r["status"] == "accepted" else 2
    if args.command == "store-status":
        conn = store_connect(args.store, readonly=True)
        for r in SR.loads(conn).itertuples():
            print(f"load {r.load_id}: {r.status:11s} {r.mode:9s} through {r.data_through}  started {r.started_at}"
                  + (f"  reviewed by {r.reviewed_by}" if isinstance(r.reviewed_by, str) and r.reviewed_by else ""))
        for t in ("securities", "prices", "splits", "dividends", "other_adjustments", "shares"):
            n = conn.execute(f"SELECT count(*) FROM {t} WHERE superseded_in IS NULL").fetchone()[0]
            m = conn.execute(f"SELECT count(*) FROM {t} WHERE superseded_in IS NOT NULL").fetchone()[0]
            print(f"  {t:18s} current {n:>12,}   superseded {m:>10,}")
        return 0
    if args.command == "store-accept":
        r = SL.accept(args.load_id, args.by, store=args.store, note=args.note)
        print(json.dumps(dict(load_id=r["load_id"], status=r["status"], counts=r["summary"].get("accepted_counts")), default=str))
        return 0
    if args.command == "store-reject":
        SL.reject(args.load_id, args.by, store=args.store, note=args.note)
        print(f"load {args.load_id} rejected")
        return 0


if __name__ == "__main__":
    sys.exit(main())
