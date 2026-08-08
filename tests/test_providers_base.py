"""Tests for data_manager.providers.base — the provider interface."""

import pytest

from data_manager.providers.base import BaseProvider


def test_base_provider_is_abstract():
    with pytest.raises(TypeError):
        BaseProvider()


def test_concrete_provider_instantiates():
    class P(BaseProvider):
        name = "p"

        def get_universe(self):
            return []

        def get_prices(self, ticker, start, end):
            return []

        def get_classification(self, ticker):
            return {}

        def get_fundamentals(self, ticker):
            return []

    p = P()
    assert isinstance(p, BaseProvider)
    assert p.name == "p"


def test_missing_abstract_method_blocks_instantiation():
    class P(BaseProvider):
        name = "p"

        def get_universe(self):
            return []

        def get_prices(self, ticker, start, end):
            return []

        def get_classification(self, ticker):
            return {}
        # get_fundamentals deliberately omitted

    with pytest.raises(TypeError):
        P()


def test_default_name_is_base():
    # The abstract class itself declares name="base" as the default.
    assert BaseProvider.name == "base"
