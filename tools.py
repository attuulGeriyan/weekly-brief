"""Deterministic pandas tools over the channel CSV. They return dicts and never raise."""
from datetime import date, timedelta

import pandas as pd

DF = pd.read_csv("data/channel_weekly.csv")
CHANNELS = sorted(DF["channel"].unique())
WEEKS = sorted(int(w) for w in DF["week"].unique())
WEEK_START = dict(zip(DF["week"], DF["week_start"]))  # week_start dates are irregular, so always map via this


def week_window(week: int) -> tuple[str, str]:
    """ISO (first day, last day) covered by a week: 7 days from its week_start."""
    start = date.fromisoformat(WEEK_START[week])
    return WEEK_START[week], (start + timedelta(days=6)).isoformat()


BASE = ["spend_usd", "impressions", "clicks", "conversions", "revenue_usd"]
DERIVED = ["cpa", "roas", "ctr", "cvr"]
KPI_ALIAS = {"spend_usd": "spend", "revenue_usd": "revenue"}  # compare_channels metric -> compute_kpi key


def _r(x, n=2):
    """Round for display only; every ratio and change is computed from unrounded numbers first."""
    return None if x is None else round(float(x), n)


def _ratios(s) -> dict:
    """Derived metrics from summed base columns (so 'blended' and any period are sum-then-divide)."""
    d = lambda a, b: float(a) / float(b) if b else None
    return {"cpa": d(s.spend_usd, s.conversions), "roas": d(s.revenue_usd, s.spend_usd),
            "ctr": d(s.clicks, s.impressions), "cvr": d(s.conversions, s.clicks)}


def _with_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["cpa"] = df["spend_usd"] / df["conversions"].replace(0, float("nan"))
    df["roas"] = df["revenue_usd"] / df["spend_usd"]
    df["ctr"] = df["clicks"] / df["impressions"]
    df["cvr"] = df["conversions"] / df["clicks"]
    return df


def _ex(exclude) -> list:
    """Coerce exclude entries to [channel, int week] (LLMs sometimes send the week as a string)."""
    try:
        return [[c, int(w)] for c, w in exclude]
    except (TypeError, ValueError):
        return exclude


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


def _kpi(channel: str, weeks: list[int], exclude) -> dict:
    """Unrounded KPIs for a channel over weeks (minus excluded channel-weeks)."""
    exclude = _ex(exclude)
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
    k = _ratios(s)
    return {"spend": float(s.spend_usd), "conversions": float(s.conversions), "revenue": float(s.revenue_usd),
            "cpa": k["cpa"], "roas": k["roas"], "weeks_used": sorted(int(w) for w in df["week"].unique()), "excluded": exclude}


def compute_kpi(channel: str, weeks: list[int], exclude: list[list] = []) -> dict:
    """Totals over the weeks (spend, conversions, revenue) and period ratios (cpa, roas)."""
    k = _kpi(channel, weeks, exclude)
    return k if "error" in k else {key: (_r(v) if isinstance(v, float) else v) for key, v in k.items()}


def compare_channels(channel_a: str, channel_b: str, weeks: list[int], metric: str = "cpa", exclude: list[list] = []) -> dict:
    """Values of a metric for two channels over the same weeks (totals for spend/conversions/revenue) and their ratio."""
    key = KPI_ALIAS.get(metric, metric)
    a, b = _kpi(channel_a, weeks, exclude), _kpi(channel_b, weeks, exclude)
    if err := next((r for r in (a, b) if "error" in r), None):
        return err
    if key not in ("spend", "conversions", "revenue", "cpa", "roas"):
        return _bad("metric", metric, ["spend_usd", "conversions", "revenue_usd", "cpa", "roas"])
    return {"a": _r(a[key]), "b": _r(b[key]), "ratio": _r(a[key] / b[key]) if b[key] else None, "metric": metric}


def compare_periods(channel: str, weeks: list[int], baseline_weeks: list[int], metric: str = "cpa", exclude: list[list] = []) -> dict:
    """Like-for-like change of one period vs another: weekly averages for spend/conversions/revenue, period ratios for cpa/roas."""
    key = KPI_ALIAS.get(metric, metric)
    a, b = _kpi(channel, weeks, exclude), _kpi(channel, baseline_weeks, exclude)
    if err := next((r for r in (a, b) if "error" in r), None):
        return err
    if key not in ("spend", "conversions", "revenue", "cpa", "roas"):
        return _bad("metric", metric, ["spend_usd", "conversions", "revenue_usd", "cpa", "roas"])
    va, vb = a[key], b[key]
    if key in ("spend", "conversions", "revenue"):  # totals -> per-week averages so period lengths don't matter
        va, vb = va / len(a["weeks_used"]), vb / len(b["weeks_used"])
    change = 100 * (va - vb) / vb if vb else None  # from unrounded inputs
    return {"value": _r(va), "baseline": _r(vb), "change_pct": _r(change, 1), "metric": metric, "weeks": weeks, "baseline_weeks": baseline_weeks}


def check_target(metric: str, week: int, op: str, threshold: float, channel: str = "blended", exclude: list[list] = []) -> dict:
    if metric not in ("cpa", "revenue_usd") or op not in ("<", ">"):
        return {"error": "metric must be cpa|revenue_usd and op must be < or >", "valid": ["cpa", "revenue_usd", "<", ">"]}
    kpi = _kpi(channel, [week], exclude)
    if "error" in kpi:
        return kpi
    value = kpi["cpa"] if metric == "cpa" else kpi["revenue"]
    met = value is not None and (value < threshold if op == "<" else value > threshold)
    return {"value": _r(value), "threshold": threshold, "met": bool(met)}


def _block(g: pd.DataFrame, m: str):
    """Value of metric m over a block of weekly rows: ratios are sum-then-divide (like compute_kpi), totals are weekly means."""
    return _ratios(g[BASE].sum())[m] if m in DERIVED else float(g[m].mean())


def scan_changes(metrics: list[str] = ["spend_usd", "conversions", "cpa"], z: float = 2.0, min_shift: float = 0.20, min_block: int = 3) -> dict:
    """Candidates: single weeks far from the channel median (> z * MAD), and sustained level shifts (later block vs earlier block)."""
    bad = [m for m in metrics if m not in BASE + DERIVED]
    if bad:
        return _bad("metric", bad, BASE + DERIVED)
    df = _with_derived(DF)
    out = []
    for ch, g in df.groupby("channel"):
        g = g.sort_values("week").reset_index(drop=True)
        for m in metrics:
            s = g.set_index("week")[m].dropna()
            med = s.median()
            mad = (s - med).abs().median()
            if mad > 0:
                for wk, v in s.items():
                    if abs(v - med) > z * mad:
                        out.append({"channel": ch, "weeks": [int(wk)], "metric": m, "value": _r(v), "baseline": _r(med),
                                    "change_pct": _r(100 * (v - med) / med, 1), "type": "outlier"})
            best = None  # level shift: the split point where the later block differs most from the earlier block
            for k in range(min_block, len(g) - min_block + 1):
                early, late = _block(g.iloc[:k], m), _block(g.iloc[k:], m)
                chg = (late - early) / early if early and late is not None else 0
                if abs(chg) > min_shift and (best is None or abs(chg) > abs(best[0])):
                    best = (chg, k, early, late)
            if best:
                chg, k, early, late = best
                out.append({"channel": ch, "weeks": [int(w) for w in g["week"].iloc[k:]], "metric": m, "value": _r(late),
                            "baseline": _r(early), "change_pct": _r(100 * chg, 1), "type": "level_shift"})
    out.sort(key=lambda r: -abs(r["change_pct"]))
    return {"candidates": out}


from tool_specs import SPECS  # noqa: E402  (descriptions + JSON schemas the LLM sees)

TOOLS = {n: {"fn": fn, **SPECS[n]} for n, fn in (("get_weekly", get_weekly), ("compute_kpi", compute_kpi), ("scan_changes", scan_changes),
                                                  ("compare_channels", compare_channels), ("compare_periods", compare_periods),
                                                  ("check_target", check_target))}
