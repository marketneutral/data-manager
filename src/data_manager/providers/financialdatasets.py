"""financialdatasets.ai provider — used for the R3000 universe (paid API)."""

import os
import time

import httpx
from dotenv import load_dotenv

from .base import BaseProvider

# Load API key from .env (never print the value)
load_dotenv(os.path.expanduser("~/.env"))
load_dotenv(os.path.expanduser("~/dev/data-manager/.env"))

API_BASE = "https://api.financialdatasets.ai"
# Russell 1000 + Russell 2000 = Russell 3000
R1000_ETF = "IWB"
R2000_ETF = "IWM"


class FinancialDatasetsProvider(BaseProvider):
    name = "financialdatasets"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("FINDAT")
        if not self.api_key:
            raise RuntimeError("FINDAT API key not found. Set it in ~/.env or data-manager/.env")
        self.headers = {"X-API-KEY": self.api_key}

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = httpx.get(f"{API_BASE}{path}", params=params, headers=self.headers, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def _get_all_holdings(self, etf: str) -> list[dict]:
        """Fetch all holdings for an ETF using offset-based pagination.

        The API returns 10 holdings per page regardless of `limit`; offset-based
        pagination reliably walks the full list (the cursor form stops at 1000).
        """
        holdings = []
        offset = 0
        while True:
            resp = httpx.get(
                f"{API_BASE}/index-funds",
                params={"ticker": etf, "limit": 1000, "offset": offset},
                headers=self.headers,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            page = data.get("holdings", [])
            holdings.extend(page)
            returned = data.get("fund", {}).get("returned", len(page))
            if not data.get("next_page_url") or not page:
                break
            offset += returned
            time.sleep(0.05)  # be gentle with rate limits
        return holdings

    def get_universe(self) -> list[dict]:
        """Return R3000 constituents (IWB + IWM), deduped by ticker."""
        seen = {}
        for etf in (R1000_ETF, R2000_ETF):
            for h in self._get_all_holdings(etf):
                ticker = h.get("ticker")
                if not ticker:
                    continue
                seen.setdefault(ticker, {
                    "ticker": ticker,
                    "name": h.get("name"),
                    "source": etf,
                })
        return list(seen.values())

    # Prices / classification / fundamentals are handled by FMP.
    def get_prices(self, ticker, start, end):
        raise NotImplementedError("Use FMPProvider for prices.")

    def get_classification(self, ticker):
        raise NotImplementedError("Use FMPProvider for classifications.")

    def get_fundamentals(self, ticker):
        raise NotImplementedError("Use FMPProvider for fundamentals.")
