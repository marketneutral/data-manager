"""iShares provider — free source for the R3000 universe and sector classifications.

Pulls the iShares Russell 3000 ETF (IWV) latest holdings CSV directly from
ishares.com. Free, authoritative (BlackRock), includes ticker, name, and sector.
"""

import csv
import io

import httpx

from .base import BaseProvider

IWV_PAGE = "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf"
IWV_CSV = IWV_PAGE + "/latest-holdings.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


class ISharesProvider(BaseProvider):
    name = "ishares"

    def __init__(self):
        self._text: str | None = None

    def _load(self) -> str:
        """Fetch the IWV holdings CSV once and cache it."""
        if self._text is None:
            with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=60) as client:
                client.get(IWV_PAGE)  # warm up session cookies
                resp = client.get(IWV_CSV)
                resp.raise_for_status()
            self._text = resp.text
        return self._text

    def _lines(self) -> list[str]:
        return self._load().splitlines()

    def get_as_of_date(self) -> str | None:
        """Return the holdings 'as of' date from the CSV header (e.g. 2026-08-06)."""
        import datetime as dt
        for ln in self._lines():
            if ln.strip().startswith("Fund Holdings as of"):
                raw = ln.split("Fund Holdings as of", 1)[1].strip().strip('"')
                try:
                    return dt.datetime.strptime(raw, "%b %d, %Y").date().isoformat()
                except ValueError:
                    return raw
        return None

    def _fetch_holdings_csv(self) -> list[dict]:
        """Parse the IWV holdings CSV into a list of row dicts."""
        lines = self._lines()
        start = next((i for i, ln in enumerate(lines)
                      if ln.strip().startswith("Ticker,")), None)
        if start is None:
            return []
        return list(csv.DictReader(lines[start:]))

    def get_universe(self) -> list[dict]:
        """Return R3000 constituents as {ticker, name, source, sector}."""
        out = []
        for row in self._fetch_holdings_csv():
            ticker = (row.get("Ticker") or "").strip()
            if not ticker:
                continue
            out.append({
                "ticker": ticker,
                "name": (row.get("Name") or "").strip(),
                "source": "IWV",
                "sector": (row.get("Sector") or "").strip() or None,
            })
        return out

    # Prices / fundamentals handled by yfinance (free).
    def get_prices(self, ticker, start, end):
        raise NotImplementedError("Use YFinanceProvider for prices.")

    def get_classification(self, ticker):
        raise NotImplementedError("Use YFinanceProvider for classifications.")

    def get_fundamentals(self, ticker):
        raise NotImplementedError("Use YFinanceProvider for fundamentals.")
