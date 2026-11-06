"""Checks on a load: does the store's version of the facts agree with the vendor's own numbers?

    volume_whole_shares   as-traded volume on rows before a later split is whole shares (it is printed so)
    total_return          our dividend and other-adjustment step equals the vendor's total-return step, within its
                          3-decimal rounding, on every day
    splits                splits found from the vendor's factor, and how many have an ACTIONS row for their ratio
    market_cap            shares filed x the close on the filing date against the vendor's marketcap
    inside_vendor_dates   price rows outside the vendor's first and last price dates for the security
"""
import numpy as np
import pandas as pd

from .read import event_factors

SEP = "\x01"


def _keys(sid, date):
    return (pd.Series(sid).astype(str).to_numpy() + SEP + pd.Series(date).astype(str).to_numpy()).astype(object)


def run(facts, checks):
    out = {}
    px = facts.get("prices")
    if px is None or not len(px):
        return out
    px = px.sort_values(["security_id", "date"]).reset_index(drop=True)
    if "_F" in px:
        pre = px[(np.abs(px._F - 1) > 1e-9) & (px.volume_traded > 0)]
        v = pre.volume_traded.to_numpy()
        out["volume_whole_shares"] = dict(rows=int(len(pre)),
                                          share=round(float((np.abs(v - np.round(v)) < 1e-4 * np.maximum(1, v)).mean()), 4) if len(v) else None)
    vc = checks.get("_vendor_check")
    if vc is not None and len(vc):
        vc = vc.sort_values(["security_id", "date"]).reset_index(drop=True)
        logq = event_factors(px, None, facts.get("dividends"), facts.get("other_adjustments"))
        vq = np.log(vc.q.to_numpy(dtype=float))
        ok = vc.same.to_numpy(dtype=bool) & np.isfinite(vq)
        gap = np.abs(logq[ok] - vq[ok])
        noise = 4 * vc.q_noise.to_numpy(dtype=float)[ok] + 1e-6
        if ok.any():
            out["total_return"] = dict(days=int(ok.sum()), within_rounding=round(float((gap <= noise).mean()), 5),
                                       within_0_1pct=round(float((gap <= 1e-3).mean()), 5),
                                       over_1pct_beyond_rounding=int(((gap > 1e-2) & (gap > noise)).sum()))
    sp = facts.get("splits")
    if sp is not None:
        out["splits"] = dict(count=int(len(sp)), with_action_ratio=int((sp.how == "action").sum()),
                             from_factor_only=int((sp.how == "factor").sum()))
    sh, mc = facts.get("shares"), checks.get("_vendor_marketcap")
    if sh is not None and mc is not None and len(sh):
        m = sh.merge(mc, on=["security_id", "filing_date", "dimension"], how="inner").drop_duplicates(
            ["security_id", "filing_date", "dimension", "report_period"])
        m = m[m.dimension == "ARQ"]
        pkey = _keys(px.security_id, px.date)
        pos = np.searchsorted(pkey, _keys(m.security_id, m.filing_date), side="right") - 1
        good = (pos >= 0) & (px.security_id.to_numpy()[np.maximum(pos, 0)] == m.security_id.to_numpy())
        close = np.where(good, px.close_traded.to_numpy()[np.maximum(pos, 0)], np.nan)
        ours = m.shares_filed.to_numpy() * close
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.abs(np.log(ours / m.marketcap.to_numpy(dtype=float)))
        r = r[np.isfinite(r)]
        out["market_cap"] = dict(filings=int(len(r)), within_1pct=round(float((r < 0.01).mean()), 4) if len(r) else None,
                                 off_over_2x=int((r > np.log(2)).sum()))
    se = facts.get("securities")
    if se is not None and len(se):
        span = se.set_index("security_id")[["first_price_date", "last_price_date"]]
        j = px[["security_id", "date"]].join(span, on="security_id")
        outside = (j.first_price_date.notna() & (j.date < j.first_price_date)) | (j.last_price_date.notna() & (j.date > j.last_price_date))
        out["inside_vendor_dates"] = dict(rows_outside=int(outside.sum()), securities=int(j.security_id[outside].nunique()))
    return out
