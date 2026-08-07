"""Provider abstraction for the data-manager."""

from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """Interface for a data provider. Implementations fetch and return raw data."""

    name: str = "base"

    @abstractmethod
    def get_universe(self) -> list[dict]:
        """Return a list of {ticker, name, source} dicts for the universe."""

    @abstractmethod
    def get_prices(self, ticker: str, start: str, end: str) -> list[dict]:
        """Return daily OHLCV rows for a ticker: {date, open, high, low, close, adj_close, volume}."""

    @abstractmethod
    def get_classification(self, ticker: str) -> dict:
        """Return {sector, industry} for a ticker."""

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> list[dict]:
        """Return annual fundamental rows for a ticker (Piotroski F-Score components)."""
