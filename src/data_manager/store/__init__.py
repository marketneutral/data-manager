"""The store: a warehouse built for a nightly load.

Each fact is stored once, in units that later events do not change:

    prices              open/high/low/close and volume as traded that day (volume exact, not truncated)
    splits              one row per split the vendor applied: ex-date and ratio (s in s:1)
    dividends           one row per cash dividend: ex-date and amount per share, in that day's units
    other_adjustments   anything else the vendor's total-return adjustment includes (spinoffs, ...): ex-date and factor
    shares              shares outstanding as filed: filing date, report period, count in the filing date's units
    securities          one row per security and version: ticker, name, labels (sector, industry, ...), dates

A later split or dividend adds an event row and leaves every historical row alone. Adjusted prices are computed when
they are read (store.read), from the events known as of the date asked for.

Nothing is deleted. Every row records the load that added it (added_in) and the load that superseded it
(superseded_in, NULL while current), so the state as of any past load can be rebuilt exactly. The `loads` table
records each load: its files, its counts, and whether it was accepted or quarantined.

Securities are keyed by security_id (Sharadar's permaticker for source 'sharadar'), never by ticker, so a recycled
ticker cannot mix two companies. Every row records its source. Vendor-specific code lives in one adapter module per
vendor (store.sharadar); nothing else names a vendor's columns.

The store lives in its own file (DATA_MANAGER_STORE, default ~/.prime/agent/warehouse.db), separate from the
legacy data_manager.db.
"""
import os
from pathlib import Path

DEFAULT_STORE = Path(os.environ.get("DATA_MANAGER_STORE", "~/.prime/agent/warehouse.db")).expanduser()
