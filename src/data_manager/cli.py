"""Command-line interface for the data-manager."""

import argparse
import sys

from . import db
from .universe import (
    update_universe,
    update_prices,
    update_classifications,
    update_fundamentals,
    universe_tickers,
)


def _tickers_from_args(args, conn):
    if args.ticker:
        return [t.strip().upper() for t in args.ticker.split(",") if t.strip()]
    if args.all:
        return universe_tickers(conn)
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(prog="data-manager", description="Acquire and update market data.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_universe = sub.add_parser("update-universe", help="Fetch R3000 constituents (financialdatasets.ai).")
    p_universe.add_argument("--db", default=None, help="SQLite DB path.")

    p_prices = sub.add_parser("update-prices", help="Fetch daily OHLCV prices (yfinance).")
    p_prices.add_argument("--ticker", default=None, help="Comma-separated tickers.")
    p_prices.add_argument("--all", action="store_true", help="All universe tickers.")
    p_prices.add_argument("--start", required=True, help="Start date YYYY-MM-DD.")
    p_prices.add_argument("--end", default=None, help="End date YYYY-MM-DD (default today).")
    p_prices.add_argument("--db", default=None)

    p_class = sub.add_parser("update-classifications", help="Fetch sector/industry (yfinance).")
    p_class.add_argument("--ticker", default=None)
    p_class.add_argument("--all", action="store_true")
    p_class.add_argument("--db", default=None)

    p_fund = sub.add_parser("update-fundamentals", help="Fetch Piotroski F-Score fundamentals (yfinance).")
    p_fund.add_argument("--ticker", default=None)
    p_fund.add_argument("--all", action="store_true")
    p_fund.add_argument("--db", default=None)

    args = parser.parse_args(argv)
    conn = db.connect(args.db)

    if args.command == "update-universe":
        n = update_universe(conn)
        print(f"Stored {n} universe tickers.")
    elif args.command == "update-prices":
        import datetime as dt
        end = args.end or dt.date.today().isoformat()
        tickers = _tickers_from_args(args, conn)
        if not tickers:
            print("No tickers. Pass --ticker or --all.")
            return 1
        n = update_prices(tickers, args.start, end, conn)
        print(f"Stored {n} price rows for {len(tickers)} tickers.")
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
