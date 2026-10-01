"""Deterministic pandas tools over the channel CSV. They return dicts and never raise."""
import pandas as pd

DF = pd.read_csv("data/channel_weekly.csv")
CHANNELS = sorted(DF["channel"].unique())
WEEKS = sorted(int(w) for w in DF["week"].unique())
BASE = ["spend_usd", "impressions", "clicks", "conversions", "revenue_usd"]
DERIVED = ["cpa", "roas", "ctr", "cvr"]
KPI_ALIAS = {"spend_usd": "spend", "revenue_usd": "revenue"}  # compare_channels metric -> compute_kpi key


def _div(a, b):
    return round(float(a) / float(b), 2) if b else None


def _derive(spend, impressions, clicks, conversions, revenue) -> dict:
    """Derived metrics from summed base columns (so 'blended' is sum-then-divide)."""
    return {"cpa": _div(spend, conversions), "roas": _div(revenue, spend),
            "ctr": _div(clicks, impressions), "cvr": _div(conversions, clicks)}


def _with_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["cpa"] = df["spend_usd"] / df["conversions"].replace(0, float("nan"))
    df["roas"] = df["revenue_usd"] / df["spend_usd"]
    df["ctr"] = df["clicks"] / df["impressions"]
    df["cvr"] = df["conversions"] / df["clicks"]
    return df


def _bad(what, got, valid):
    return {"error": f"unknown {what}: {got}", "valid": valid}


def _check(channels=(), weeks=(), exclude=(), allow_blended=False):
    """Return an error dict if any channel/week is unknown, else None."""
    ok_ch = CHANNELS + (["blended"] if allow_blended else [])
    for c in channels:
        if c not in ok_ch:
            return _bad("channel", c, ok_ch)
    for w in weeks:
        if w not in WEEKS:
            return _bad("week", w, WEEKS)
    for e in exclude:
        if len(e) != 2 or e[0] not in CHANNELS or e[1] not in WEEKS:
            return {"error": f"exclude entries must be [channel, week], got {e}", "valid": [CHANNELS, WEEKS]}
    return None


def get_weekly(channels: list[str] | None = None, weeks: list[int] | None = None, metrics: list[str] | None = None) -> dict:
    channels, weeks = channels or CHANNELS, weeks or WEEKS
    metrics = metrics or ["spend_usd", "conversions", "revenue_usd"]
    if err := _check(channels, weeks):
        return err
    bad = [m for m in metrics if m not in BASE + DERIVED]
    if bad:
        return _bad("metric", bad, BASE + DERIVED)
    df = _with_derived(DF)
    df = df[df["channel"].isin(channels) & df["week"].isin(weeks)]
    rows = df[["week", "channel"] + metrics].round(2).to_dict("records")
    return {"rows": rows}


def compute_kpi(channel: str, weeks: list[int], exclude: list[list] = []) -> dict:
    if err := _check([channel], weeks, exclude, allow_blended=True):
        return err
    df = DF[DF["week"].isin(weeks)]
    if channel != "blended":
        df = df[df["channel"] == channel]
    for ch, wk in exclude:  # drop broken channel-weeks
        df = df[~((df["channel"] == ch) & (df["week"] == wk))]
    if df.empty:
        return {"error": "no rows left after filtering", "valid": WEEKS}
    s = df[BASE].sum()
    k = _derive(s.spend_usd, s.impressions, s.clicks, s.conversions, s.revenue_usd)
    return {"spend": round(float(s.spend_usd), 2), "conversions": round(float(s.conversions), 2),
            "revenue": round(float(s.revenue_usd), 2), "cpa": k["cpa"], "roas": k["roas"],
            "weeks_used": sorted(int(w) for w in df["week"].unique()), "excluded": exclude}


def compare_channels(channel_a: str, channel_b: str, weeks: list[int], metric: str = "cpa", exclude: list[list] = []) -> dict:
    key = KPI_ALIAS.get(metric, metric)
    a, b = compute_kpi(channel_a, weeks, exclude), compute_kpi(channel_b, weeks, exclude)
    for r in (a, b):
        if "error" in r:
            return r
    if key not in ("spend", "conversions", "revenue", "cpa", "roas"):
        return _bad("metric", metric, ["spend_usd", "conversions", "revenue_usd", "cpa", "roas"])
    return {"a": a[key], "b": b[key], "ratio": _div(a[key], b[key]), "metric": metric}


def check_target(metric: str, week: int, op: str, threshold: float, channel: str = "blended", exclude: list[list] = []) -> dict:
    if metric not in ("cpa", "revenue_usd") or op not in ("<", ">"):
        return {"error": "metric must be cpa|revenue_usd and op must be < or >", "valid": ["cpa", "revenue_usd", "<", ">"]}
    kpi = compute_kpi(channel, [week], exclude)
    if "error" in kpi:
        return kpi
    value = kpi["cpa"] if metric == "cpa" else kpi["revenue"]
    met = value is not None and (value < threshold if op == "<" else value > threshold)
    return {"value": value, "threshold": threshold, "met": bool(met)}


def scan_changes(metrics: list[str] = ["spend_usd", "conversions", "cpa"], z: float = 2.0, min_shift: float = 0.20, min_block: int = 3) -> dict:
    """Candidates: single weeks far from the channel median (> z * MAD), and sustained level shifts."""
    bad = [m for m in metrics if m not in BASE + DERIVED]
    if bad:
        return _bad("metric", bad, BASE + DERIVED)
    df = _with_derived(DF)
    out = []
    for ch, g in df.groupby("channel"):
        g = g.sort_values("week")
        for m in metrics:
            s = g.set_index("week")[m].dropna()
            med = s.median()
            mad = (s - med).abs().median()
            if mad > 0:
                for wk, v in s.items():
                    if abs(v - med) > z * mad:
                        out.append({"channel": ch, "weeks": [int(wk)], "metric": m, "value": round(float(v), 2),
                                    "baseline": round(float(med), 2), "change_pct": round(float(100 * (v - med) / med), 1), "type": "outlier"})
            best = None  # level shift: split point where later-block mean differs most from earlier-block mean
            for k in range(min_block, len(s) - min_block + 1):
                early, late = s.iloc[:k].mean(), s.iloc[k:].mean()
                chg = (late - early) / early if early else 0
                if abs(chg) > min_shift and (best is None or abs(chg) > abs(best[0])):
                    best = (chg, k, early, late)
            if best:
                chg, k, early, late = best
                out.append({"channel": ch, "weeks": [int(w) for w in s.index[k:]], "metric": m, "value": round(float(late), 2),
                            "baseline": round(float(early), 2), "change_pct": round(float(100 * chg), 1), "type": "level_shift"})
    out.sort(key=lambda r: -abs(r["change_pct"]))
    return {"candidates": out}


TOOLS = {f.__name__: f for f in (get_weekly, compute_kpi, scan_changes, compare_channels, check_target)}
