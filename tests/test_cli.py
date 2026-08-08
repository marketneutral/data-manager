"""Tests for data_manager.cli — argument handling + main() dispatch."""

from data_manager import cli


class Args:
    def __init__(self, ticker=None, all=False):
        self.ticker = ticker
        self.all = all


def test_tickers_from_args_ticker_list(conn):
    args = Args(ticker="aapl, msft")
    assert cli._tickers_from_args(args, conn) == ["AAPL", "MSFT"]


def test_tickers_from_args_ticker_trims_empties(conn):
    args = Args(ticker="aapl,, msft, ")
    assert cli._tickers_from_args(args, conn) == ["AAPL", "MSFT"]


def test_tickers_from_args_all(conn):
    for t in ["MSFT", "AAPL"]:
        conn.execute(
            "INSERT OR REPLACE INTO universe (ticker, name, source, added_at) "
            "VALUES (?, ?, ?, ?)", (t, t, "IWV", "2026-01-01"))
    conn.commit()
    args = Args(all=True)
    assert cli._tickers_from_args(args, conn) == ["AAPL", "MSFT"]


def test_tickers_from_args_none(conn):
    assert cli._tickers_from_args(Args(), conn) == []


def test_main_status(tmp_path, capsys):
    rc = cli.main(["status", "--db", str(tmp_path / "s.db")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "universe:" in out
    assert "prices:" in out


def test_main_update_universe(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "update_universe", lambda conn: 3)
    rc = cli.main(["update-universe", "--db", str(tmp_path / "u.db")])
    assert rc == 0
    assert "Stored 3 universe tickers." in capsys.readouterr().out


def test_main_update_prices_requires_tickers(tmp_path, capsys):
    rc = cli.main(["update-prices", "--start", "2026-01-01",
                   "--db", str(tmp_path / "p.db")])
    assert rc == 1
    assert "No tickers" in capsys.readouterr().out
