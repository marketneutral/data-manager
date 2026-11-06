"""Integer keys for (security, date): one int64 per row, ordered by security then date, so tens of millions of rows
sort and match without building a Python string per row."""
import numpy as np
import pandas as pd

DAY = 1 << 20                      # days since 1970 fit in 20 bits for centuries


def day_number(dates):
    """'YYYY-MM-DD' strings -> days since 1970 (int64)."""
    return pd.to_datetime(pd.Series(dates).to_numpy(), format="%Y-%m-%d").to_numpy().astype("datetime64[D]").astype(np.int64)


class Keys:
    """Codes for a set of security ids (in sorted order, so code order is id order) and keys built on them."""

    def __init__(self, *id_series):
        ids = pd.unique(pd.concat([pd.Series(np.asarray(s, dtype=object)) for s in id_series], ignore_index=True))
        self.ids = np.sort(ids.astype(str))
        self._cat = pd.CategoricalDtype(self.ids)

    def code(self, ids):
        c = pd.Categorical(pd.Series(np.asarray(ids, dtype=object)).astype(str), dtype=self._cat).codes.astype(np.int64)
        return c                                   # -1 for an id not in the set

    def key(self, ids, dates):
        return self.code(ids) * DAY + day_number(dates)
