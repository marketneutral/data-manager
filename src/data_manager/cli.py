"""Command-line interface for the data-manager."""

import argparse
import sys

from . import db
from .universe import (
    update_universe,
    update_prices,
    update_classifications,
    update_fundamentals,
    update_ratios,
    update_quarterly,
    universe_tickers,
)
from . import enrich


def _tickers_from_args(args, conn):
    if args.ticker:
        return [t.strip().upper() for t in args.ticker.split(",") if t.strip()]
    if args.all:
        return universe_tickers(conn)
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(prog="data-manager", description="Acquire and update market data.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show table counts + identifier coverage.")
    p_status.add_argument("--db", default=None)
    p_universe = sub.add_parser("update-universe", help="Fetch R3000 constituents (financialdatasets.ai).")
    p_universe.add_argument("--db", default=None, help="SQLite DB path.")

    p_enr_figi = sub.add_parser("enrich-figi", help="Enrich universe with FIGI (OpenFigi).")
    p_enr_figi.add_argument("--db", default=None)
    p_enr_cik = sub.add_parser("enrich-cik", help="Enrich universe with CIK (SEC).")
    p_enr_cik.add_argument("--db", default=None)
    p_enr_sic = sub.add_parser("enrich-sic", help="Enrich universe with SIC + LEI (SEC submissions).")
    p_enr_sic.add_argument("--max", type=int, default=None, help="Cap number of companies.")
    p_enr_sic.add_argument("--db", default=None)

    p_prices = sub.add_parser("update-prices", help="Fetch daily as-traded OHLCV prices (FMP).")
    p_prices.add_argument("--ticker", default=None, help="Comma-separated tickers.")
    p_prices.add_argument("--all", action="store_true", help="All universe tickers.")
    p_prices.add_argument("--start", required=True, help="Start date YYYY-MM-DD.")
    p_prices.add_argument("--end", default=None, help="End date YYYY-MM-DD (default today).")
    p_prices.add_argument("--force", action="store_true",
                         help="refetch even if the ticker already covers --end "
                              "(use to extend history depth with an earlier --start)")
    p_prices.add_argument("--db", default=None)

    p_class = sub.add_parser("update-classifications", help="Fetch sector/industry (FMP).")
    p_class.add_argument("--ticker", default=None)
    p_class.add_argument("--all", action="store_true")
    p_class.add_argument("--db", default=None)

    p_fund = sub.add_parser("update-fundamentals", help="Fetch Piotroski F-Score fundamentals (FMP).")
    p_ratios = sub.add_parser("update-ratios", help="Snapshot point-in-time fundamental ratios (FMP TTM).")
    p_quart = sub.add_parser("update-quarterly", help="Fetch ~10y quarterly statements (FMP).")
    p_quart.add_argument("--ticker", default=None)
    p_quart.add_argument("--all", action="store_true")
    p_quart.add_argument("--db", default=None)
    p_ratios.add_argument("--ticker", default=None)
    p_ratios.add_argument("--all", action="store_true")
    p_ratios.add_argument("--db", default=None)
    p_fund.add_argument("--ticker", default=None)
    p_fund.add_argument("--all", action="store_true")
    p_fund.add_argument("--db", default=None)

    args = parser.parse_args(argv)
    conn = db.connect(args.db)

    if args.command == "status":
        n_u = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
        n_p = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        n_c = conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
        n_f = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
        def cnt(col, tbl="universe"):
            return conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {col} IS NOT NULL AND {col} != ''").fetchone()[0]
        ind = conn.execute("SELECT COUNT(*) FROM classifications WHERE industry IS NOT NULL AND industry != ''").fetchone()[0]
        print(f"universe:        {n_u}")
        print(f"  figi:          {cnt('figi')}")
        print(f"  cik:           {cnt('cik')}")
        print(f"  sic:           {cnt('sic')}")
        print(f"  lei:           {cnt('lei')}")
        print(f"classifications:{n_c} (industry filled: {ind})")
        print(f"prices:         {n_p}")
        n_r = conn.execute("SELECT COUNT(*) FROM ratios").fetchone()[0]
        n_rt = conn.execute("SELECT COUNT(DISTINCT ticker) FROM ratios").fetchone()[0]
        print(f"ratios:         {n_r} snapshots ({n_rt} tickers)")
        print(f"fundamentals:   {n_f}")
        sn = conn.execute("SELECT source, pulled_at, as_of, row_count FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if sn:
            print(f"last snapshot:   {sn[0]} pulled {sn[1]} as_of {sn[2]} rows {sn[3]}")
    elif args.command == "enrich-figi":
        n = enrich.enrich_figi(conn)
        print(f"Enriched {n} tickers with FIGI.")
    elif args.command == "enrich-cik":
        n = enrich.enrich_cik(conn)
        print(f"Enriched {n} tickers with CIK.")
    elif args.command == "enrich-sic":
        n = enrich.enrich_sic_lei(conn, max_tickers=args.max)
        print(f"Enriched {n} tickers with SIC/LEI.")
    elif args.command == "update-universe":
        n = update_universe(conn)
        print(f"Stored {n} universe tickers.")
    elif args.command == "update-prices":
        import datetime as dt
        end = args.end or dt.date.today().isoformat()
        tickers = _tickers_from_args(args, conn)
        if not tickers:
            print("No tickers. Pass --ticker or --all.")
            return 1
        n = update_prices(tickers, args.start, end, conn,
                          force=getattr(args, "force", False))
        print(f"Stored {n} price rows for {len(tickers)} tickers.")
    elif args.command == "update-quarterly":
        tickers = _tickers_from_args(args, conn)
        if not tickers:
            print("No tickers. Pass --ticker or --all.")
            return 1
        n = update_quarterly(tickers, conn)
        print(f"Stored {n} quarterly statement rows.")
    elif args.command == "update-ratios":
        tickers = _tickers_from_args(args, conn)
        if not tickers:
            print("No tickers. Pass --ticker or --all.")
            return 1
        n = update_ratios(tickers, conn)
        print(f"Stored {n} ratio snapshots.")
    elif args.command == "update-classifications":
        tickers = _tickers_from_args(args, conn)
        if not tickers:
            print("No tickers. Pass --ticker or --all.")
            return 1
        n = update_classifications(tickers, conn)
        print(f"Updated classifications for {n} tickers.")
    elif args.command == "update-fundamentals":
        tickers = _tickers_from_args(args, conn)
        if not tickers:
            print("No tickers. Pass --ticker or --all.")
            return 1
        n = update_fundamentals(tickers, conn)
        print(f"Stored {n} fundamental rows for {len(tickers)} tickers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
