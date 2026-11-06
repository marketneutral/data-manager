"""Loading the store: convert the vendor's files, compare with what is stored, gate, commit.

    load(files, mode)   full: the files are the vendor's whole history (a first load, or the weekly reconciliation);
                              a stored row the files no longer have is reported as dropped.
                        increment: the files carry only what the vendor changed (a nightly load); nothing is
                              inferred about rows they do not mention.
    accept(load_id)     commit a quarantined load after review.

A change is a value that differs by more than the vendor's rounding (the adapter gives a tolerance per row), so a
mechanical rewrite (the vendor rescaling history for a new split) changes nothing. Changed and dropped rows are
superseded, never deleted. The gate holds back a load that drops history, rewrites many old rows, or that the
adapter could not convert cleanly; a held-back load is staged on disk and changes nothing until accepted.
"""
import datetime as dt
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from . import read
from . import sharadar as default_adapter
from .schema import TABLES, connect

DEFAULTS = dict(
    max_dropped_rows=1000,        # dropped price rows (full loads) before the load is held back
    max_dropped_events=50,        # dropped splits, dividends, other adjustments or filings
    max_changed_old_rows=5000,    # changed price rows dated more than old_days before the data's end
    max_changed_old_securities=100,
    max_changed_old_volume_rows=250_000,      # the vendor revises volume history routinely
    max_changed_old_volume_securities=5000,
    old_days=30,
    max_factor_problems=250_000,  # rows whose vendor split factor disagrees with the splits
)

TOLERANCE = {  # column -> the adapter's tolerance column, or a fixed absolute tolerance
    "open_traded": "_tol_price", "high_traded": "_tol_price", "low_traded": "_tol_price",
    "close_traded": 1e-6, "volume_traded": "_tol_volume", "ratio": 1e-6, "amount_traded": "_tol_amount",
    "factor": "_tol_factor", "shares_filed": "_tol_shares",
}


import time as _time

_T0 = [_time.time()]


def _say(msg):
    print(f"[store {_time.time() - _T0[0]:7.0f}s] {msg}", flush=True)


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _differs(a, b, tol):
    if not (pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b)):
        a, b = a.astype(object), b.astype(object)
        na, nb = pd.isna(a), pd.isna(b)
        return (na != nb) | (~na & ~nb & (a.astype(str) != b.astype(str)))
    a, b = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
    na, nb = np.isnan(a), np.isnan(b)
    with np.errstate(invalid="ignore"):
        return pd.Series((na != nb) | (~na & ~nb & (np.abs(a - b) > tol)))


def diff(table, new, cur, full):
    """{'added', 'changed', 'missing'}: new rows (with values), changed rows (new values), and rows stored but not in
    the files (full loads only)."""
    key, vals = TABLES[table]
    cur = cur[key + vals]
    m = new.merge(cur, on=key, how="outer" if full else "left", suffixes=("", "__cur"), indicator=True)
    added = m[m._merge == "left_only"]
    both = m[m._merge == "both"].reset_index(drop=True)
    change = pd.Series(False, index=both.index)
    for v in vals:
        t = TOLERANCE.get(v, 0.0)
        tol = both[t].to_numpy(dtype=float) if isinstance(t, str) and t in both else (t if not isinstance(t, str) else 0.0)
        change |= _differs(both[v], both[v + "__cur"], tol).to_numpy()
    cols = key + vals
    return dict(added=added[cols].reset_index(drop=True), changed=both.loc[change, cols].reset_index(drop=True),
                missing=m.loc[m._merge == "right_only", key].reset_index(drop=True) if full else pd.DataFrame(columns=key))


def _scope(conn, table, new, full):
    """The current rows a batch is compared with: everything (full), or the rows of the securities it carries."""
    if full or not len(new):
        return read.rows(conn, table)
    ids = sorted(new.security_id.unique())
    parts = []
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        parts.append(read.rows(conn, table, where=f"security_id IN ({','.join('?' * len(part))})", params=part))
    return pd.concat(parts, ignore_index=True)


BATCH_ROWS = int(__import__("os").environ.get("DATA_MANAGER_STORE_BATCH_ROWS", 2_000_000))          # a table this large is compared a batch of securities at a time
BATCH_SECURITIES = 1500


def _diff_in_batches(conn, table, new, full):
    """diff() a batch of securities at a time, so a full load never holds two copies of every row at once."""
    stored = [r[0] for r in conn.execute(f"SELECT DISTINCT security_id FROM {table} WHERE superseded_in IS NULL")]
    ids = sorted(set(new.security_id.unique()) | (set(stored) if full else set()))
    by = new.groupby("security_id", sort=False).indices
    parts = {k: [] for k in ("added", "changed", "missing")}
    for i in range(0, len(ids), BATCH_SECURITIES):
        batch = ids[i:i + BATCH_SECURITIES]
        idx = [by[b] for b in batch if b in by]
        nb = new.iloc[np.concatenate(idx)] if idx else new.iloc[:0]
        cur = pd.concat([read.rows(conn, table, where=f"security_id IN ({','.join('?' * len(part))})", params=part)
                         for part in (batch[j:j + 900] for j in range(0, len(batch), 900))], ignore_index=True)
        d = diff(table, nb, cur, full)
        for k in parts:
            parts[k].append(d[k])
    return {k: pd.concat(v, ignore_index=True) for k, v in parts.items()}


def _change_kinds(old, before):
    """Split changed old price rows into those whose prices moved and those where only volume did."""
    if before is None or not len(old):
        return {"old prices changed": old} if len(old) else {}
    j = old.merge(before, on=["security_id", "date"], suffixes=("", "__b"), how="left")
    price_cols = ["open_traded", "high_traded", "low_traded", "close_traded"]
    moved = np.zeros(len(j), bool)
    for c in price_cols:
        a, b = j[c].to_numpy(float), j[c + "__b"].to_numpy(float)
        with np.errstate(invalid="ignore"):
            moved |= (np.isnan(a) != np.isnan(b)) | (np.abs(a - b) > 1e-3 * np.maximum(1, np.abs(b)))
    out = {}
    if moved.any():
        out["old prices changed"] = old[moved]
    if (~moved).any():
        out["old volume revised"] = old[~moved]
    return out


def gate(diffs, problems, first, data_through, cfg, prices_after=None, prices_before=None):
    """The findings on a load: [{kind, severity ('hold' or 'notice'), count, detail}]."""
    out = []

    def add(kind, severity, count, detail=None):
        if count:
            out.append(dict(kind=kind, severity=severity, count=int(count), detail=detail))

    for p in problems:
        sev = "hold" if p["kind"] == "row factor differs from the splits" and p.get("rows", 0) > cfg["max_factor_problems"] else "notice"
        add(p["kind"], sev, p.get("rows", 1), {k: v for k, v in p.items() if k not in ("kind",)})
    if first:
        return out
    px = diffs.get("prices")
    if px is not None:
        n = len(px["missing"])
        add("price history dropped", "hold" if n > cfg["max_dropped_rows"] else "notice", n,
            dict(securities=int(px["missing"].security_id.nunique()),
                 top=px["missing"].security_id.value_counts().head(10).to_dict()))
        cutoff = (pd.Timestamp(data_through) - pd.Timedelta(days=cfg["old_days"])).strftime("%Y-%m-%d") if data_through else None
        if cutoff and len(px["changed"]):
            old = px["changed"][px["changed"].date < cutoff]
            cur = prices_before if prices_before is not None else None
            kinds = _change_kinds(old, cur)
            for kind, rows in kinds.items():
                n_sec = rows.security_id.nunique()
                limit_rows = cfg["max_changed_old_rows"] if kind == "old prices changed" else cfg["max_changed_old_volume_rows"]
                limit_sec = cfg["max_changed_old_securities"] if kind == "old prices changed" else cfg["max_changed_old_volume_securities"]
                hold = len(rows) > limit_rows or n_sec > limit_sec
                add(kind, "hold" if hold else "notice", len(rows),
                    dict(securities=int(n_sec), top=rows.security_id.value_counts().head(10).to_dict()))
    for t in ("splits", "dividends", "other_adjustments", "shares"):
        d = diffs.get(t)
        if d is None:
            continue
        n = len(d["missing"])
        add(f"{t} dropped", "hold" if n > cfg["max_dropped_events"] else "notice", n)
        add(f"{t} changed", "notice", len(d["changed"]))
    s = diffs.get("securities")
    if s is not None and len(s["changed"]):
        add("securities changed (ticker, labels, dates)", "notice", len(s["changed"]))
    if prices_after is not None and len(prices_after):
        add("split-sized move with no split", "notice", _split_sized_moves(prices_after))
    return out


COMMON_RATIOS = np.log([2, 3, 4, 5, 10, 1.5, 20, 50, 100])


def _split_sized_moves(px):
    """Rows (new or changed) whose close moves by a common split ratio (within 2%) from the previous row, where the
    batch has no split. px: security_id, date, close_traded, new (bool), split (bool), sorted."""
    same = px.security_id.eq(px.security_id.shift()).to_numpy()
    c = pd.to_numeric(px.close_traded, errors="coerce").to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.abs(np.log(c[1:] / c[:-1]))
    r = np.concatenate([[np.nan], r])
    near = np.zeros(len(px), bool)
    for c in COMMON_RATIOS:
        near |= np.abs(r - c) < 0.02
    return int((same & near & px.new.to_numpy(dtype=bool) & ~px.split.to_numpy(dtype=bool)).sum())


def _commit(conn, load_id, diffs, source):
    for table, d in diffs.items():
        key, vals = TABLES[table]
        gone = pd.concat([d["changed"][key], d["missing"][key]], ignore_index=True)
        if len(gone):
            conn.execute("DROP TABLE IF EXISTS temp._gone")
            conn.execute(f"CREATE TEMP TABLE _gone ({', '.join(key)})")
            conn.executemany(f"INSERT INTO _gone VALUES ({','.join('?' * len(key))})",
                             gone.astype(object).itertuples(index=False, name=None))
            conn.execute(f"UPDATE {table} SET superseded_in = ? WHERE superseded_in IS NULL AND ({', '.join(key)}) IN "
                         f"(SELECT {', '.join(key)} FROM _gone)", (load_id,))
        new = pd.concat([d["added"], d["changed"]], ignore_index=True)
        if len(new):
            cols = key + vals + ["source", "added_in"]
            new = new[key + vals].astype(object).where(new[key + vals].notna(), None)
            rows = ((*r, source, load_id) for r in new.itertuples(index=False, name=None))
            conn.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)


def _stage_dir(store_path, load_id):
    return Path(store_path).expanduser().parent / "warehouse_staged" / str(load_id)


def load(files, mode="full", store=None, adapter=default_adapter, config=None, note=None, hash_files=True):
    """Load the vendor's files into the store. Returns the load's record (load_id, status, summary)."""
    from . import DEFAULT_STORE
    store = store or DEFAULT_STORE
    cfg = dict(DEFAULTS, **(config or {}))
    full = mode == "full"
    conn = connect(store)
    first = conn.execute("SELECT count(*) FROM loads WHERE status='accepted'").fetchone()[0] == 0
    info = {k: (adapter.file_info(p) if hash_files else dict(path=str(p))) for k, p in files.items()}
    cur = conn.execute("INSERT INTO loads (source, mode, started_at, files, status) VALUES (?,?,?,?, 'staged')",
                       (adapter.SOURCE, mode, _now(), json.dumps(info)))
    load_id = cur.lastrowid
    conn.commit()

    _T0[0] = _time.time()
    _say(f"load {load_id} ({mode}): reading {', '.join(files)}")
    vendor = adapter.read_files(files)
    _say("read: " + ", ".join(f"{k} {len(v):,}" for k, v in vendor.items()))
    known = None if full else read.rows(conn, "splits")
    facts = adapter.normalize(vendor, known_splits=known)
    _say("converted: " + ", ".join(f"{k} {len(v):,}" for k, v in facts.items() if hasattr(v, "__len__") and not k.startswith("_") and k != "problems"))
    problems = facts.pop("problems", [])
    checks = {k: facts.pop(k) for k in list(facts) if k.startswith("_")}
    data_through = facts["prices"].date.max() if "prices" in facts and len(facts["prices"]) else None

    diffs = {}
    for t in TABLES:
        if t in facts:
            diffs[t] = (_diff_in_batches(conn, t, facts[t], full) if len(facts[t]) > BATCH_ROWS
                        else diff(t, facts[t], _scope(conn, t, facts[t], full), full))
            _say(f"compared {t}: " + ", ".join(f"{k} {len(v):,}" for k, v in diffs[t].items()))
    prices_after = None
    if "prices" in diffs and not first:
        p = diffs["prices"]
        touched = pd.concat([p["added"], p["changed"]])
        if len(touched):
            ids = touched.security_id.unique()
            since = (pd.Timestamp(touched.date.min()) - pd.Timedelta(days=15)).strftime("%Y-%m-%d")
            ids_l = sorted(ids)
            prev = pd.concat([read.rows(conn, "prices", where=f"date >= ? AND security_id IN ({','.join('?' * len(part))})",
                                        params=[since] + part, columns=["security_id", "date", "close_traded"])
                              for part in (ids_l[j:j + 900] for j in range(0, len(ids_l), 900))], ignore_index=True)
            after = pd.concat([prev[["security_id", "date", "close_traded"]],
                               touched[["security_id", "date", "close_traded"]]]).drop_duplicates(["security_id", "date"], keep="last")
            after = after[after.security_id.isin(ids)].sort_values(["security_id", "date"]).reset_index(drop=True)
            k = after.security_id + "|" + after.date
            after["new"] = k.isin(set(touched.security_id + "|" + touched.date))
            sp = facts.get("splits", pd.DataFrame(columns=["security_id", "ex_date"]))
            after["split"] = k.isin(set(sp.security_id + "|" + sp.ex_date))
            prices_after = after
    before = None
    if "prices" in diffs and len(diffs["prices"]["changed"]):
        ch = diffs["prices"]["changed"]
        ids_c = sorted(ch.security_id.unique())
        before = pd.concat([read.rows(conn, "prices", where=f"security_id IN ({','.join('?' * len(part))})", params=part)
                            for part in (ids_c[j:j + 900] for j in range(0, len(ids_c), 900))], ignore_index=True)
        before = before.merge(ch[["security_id", "date"]], on=["security_id", "date"])
    findings = gate(diffs, problems, first, data_through, cfg, prices_after, before)
    held = any(f["severity"] == "hold" for f in findings)
    summary = dict(counts={t: {k: len(v) for k, v in d.items()} for t, d in diffs.items()}, findings=findings,
                   data_through=data_through)
    if held:
        stage = _stage_dir(store, load_id)
        stage.mkdir(parents=True, exist_ok=True)
        for t, f in facts.items():
            f.to_parquet(stage / f"{t}.parquet")
        conn.execute("UPDATE loads SET status='quarantined', finished_at=?, data_through=?, summary=?, note=? WHERE load_id=?",
                     (_now(), data_through, json.dumps(summary, default=str), note, load_id))
        conn.commit()
        return dict(load_id=load_id, status="quarantined", summary=summary)
    _say("gate: " + ("; ".join(f"{f['kind']} {f['count']:,} ({f['severity']})" for f in findings) or "nothing to report"))
    _commit(conn, load_id, diffs, adapter.SOURCE)
    _say("committed")
    from . import audit
    summary["audit"] = audit.run(facts, checks)
    _say("audited")
    conn.execute("UPDATE loads SET status='accepted', finished_at=?, data_through=?, summary=?, note=? WHERE load_id=?",
                 (_now(), data_through, json.dumps(summary, default=str), note, load_id))
    conn.commit()
    return dict(load_id=load_id, status="accepted", summary=summary)


def accept(load_id, reviewed_by, store=None, note=None):
    """Commit a quarantined load after review, compared again with the store as it is now."""
    from . import DEFAULT_STORE
    store = store or DEFAULT_STORE
    conn = connect(store)
    row = conn.execute("SELECT status, mode, summary FROM loads WHERE load_id=?", (load_id,)).fetchone()
    if not row or row[0] != "quarantined":
        raise ValueError(f"load {load_id} is not quarantined")
    later = conn.execute("SELECT count(*) FROM loads WHERE status='accepted' AND load_id > ?", (load_id,)).fetchone()[0]
    if later:
        raise ValueError(f"a later load was accepted after {load_id}: load the vendor's files again instead")
    stage = _stage_dir(store, load_id)
    facts = {p.stem: pd.read_parquet(p) for p in stage.glob("*.parquet")}
    full = row[1] == "full"
    diffs = {t: diff(t, facts[t], _scope(conn, t, facts[t], full), full) for t in TABLES if t in facts}
    _commit(conn, load_id, diffs, default_adapter.SOURCE)
    summary = json.loads(row[2])
    summary["accepted_counts"] = {t: {k: len(v) for k, v in d.items()} for t, d in diffs.items()}
    conn.execute("UPDATE loads SET status='accepted', finished_at=?, summary=?, reviewed_by=?, note=? WHERE load_id=?",
                 (_now(), json.dumps(summary, default=str), reviewed_by, note, load_id))
    conn.commit()
    shutil.rmtree(stage, ignore_errors=True)
    return dict(load_id=load_id, status="accepted", summary=summary)


def reject(load_id, reviewed_by, store=None, note=None):
    from . import DEFAULT_STORE
    store = store or DEFAULT_STORE
    conn = connect(store)
    conn.execute("UPDATE loads SET status='rejected', reviewed_by=?, note=? WHERE load_id=? AND status='quarantined'",
                 (reviewed_by, note, load_id))
    conn.commit()
    shutil.rmtree(_stage_dir(store, load_id), ignore_errors=True)


def nightly(store=None, dest_root=None, adapter=default_adapter, overlap_days=1, config=None, today=None):
    """The nightly load: fetch what the vendor changed since the last accepted load (with a day's overlap) into a
    dated folder (kept: the vendor's files for that night), load it as an increment, and say whether a new trading day
    arrived. Returns the load's record plus 'new_day' (bool) and 'folder'."""
    from . import DEFAULT_STORE
    store = store or DEFAULT_STORE
    conn = connect(store)
    last = conn.execute("SELECT max(load_id), max(data_through) FROM loads WHERE status='accepted'").fetchone()
    if last[0] is None:
        raise ValueError("the store has no accepted load: run a full load first (data-manager store-load)")
    started = conn.execute("SELECT started_at FROM loads WHERE load_id=?", (last[0],)).fetchone()[0]
    since = (pd.Timestamp(started[:10]) - pd.Timedelta(days=overlap_days)).strftime("%Y-%m-%d")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M")
    folder = Path(dest_root or Path(store).expanduser().parent / "increments") / stamp
    _say(f"fetching what {adapter.SOURCE} changed since {since} into {folder}")
    files = adapter.fetch_increment(since, folder, today=today)
    r = load(files, mode="increment", store=store, adapter=adapter, config=config,
             note=f"nightly: changes since {since}", hash_files=True)
    through = r["summary"].get("data_through")
    r.update(new_day=bool(through and last[1] and through > last[1]), previous_through=last[1], folder=str(folder))
    return r
