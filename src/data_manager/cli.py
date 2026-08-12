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
    build_universe_pit,
    universe_tickers,
)
from . import enrich

BULK_ORDER = ("tickers", "stocks", "actions", "metrics", "sp500", "funds", "fundamentals")


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
    p_pit = sub.add_parser("build-universe-pit", help="Construct a PIT investable universe (master+prices+sf1).")
    p_pit.add_argument("--asof", default=None, help="YYYY-MM-DD (default today).")
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

    p_blk = sub.add_parser("load-bulk", help="Load a downloaded Sharadar bulk zip (tickers/stocks/funds/actions/metrics/sp500/fundamentals).")
    p_blk.add_argument("table", choices=["tickers","stocks","funds","actions","metrics","sp500","fundamentals"])
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
    p_bu = sub.add_parser("bulk-update", help="Incremental update (manifest-skipped downloads + reload + derive).")
    p_bu.add_argument("--tables", default=None, help="comma list (default: all)")
    p_bu.add_argument("--force", action="store_true")
    p_bu.add_argument("--dir", default=None)
    p_bu.add_argument("--asof", default=None)
    p_bu.add_argument("--no-derive", action="store_true")
    p_bu.add_argument("--no-pit", action="store_true")
    p_bu.add_argument("--db", default=None)

    args = parser.parse_args(argv)
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
    elif args.command == "build-universe-pit":
        types = tuple(args.types.split(",")) if args.types else None
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
              "fundamentals": BL.load_fundamentals}[args.table]
        n = fn(args.file, conn)
        print(f"Loaded {n} rows into {args.table}.")
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
        else:
            tabs = [t.strip() for t in args.tables.split(",")] if args.tables else None
            res = B.bulk_update(dest_dir=d, conn=conn, tables=tabs, force=args.force,
                                derive=not args.no_derive, pit=not args.no_pit, pit_asof=args.asof)
            print("UPDATE DONE: loaded", res["loaded"], "| skipped:", res["skipped"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
