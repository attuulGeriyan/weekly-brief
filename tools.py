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


def compute_kpi(channel: str, weeks: list[int], exclude: list[list] = []) -> dict:
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
    k = _derive(s.spend_usd, s.impressions, s.clicks, s.conversions, s.revenue_usd)
    return {"spend": round(float(s.spend_usd), 2), "conversions": round(float(s.conversions), 2),
            "revenue": round(float(s.revenue_usd), 2), "cpa": k["cpa"], "roas": k["roas"],
            "weeks_used": sorted(int(w) for w in df["week"].unique()), "excluded": exclude}


def compare_channels(channel_a: str, channel_b: str, weeks: list[int], metric: str = "cpa", exclude: list[list] = []) -> dict:
    key, exclude = KPI_ALIAS.get(metric, metric), _ex(exclude)
    a, b = compute_kpi(channel_a, weeks, exclude), compute_kpi(channel_b, weeks, exclude)
    for r in (a, b):
        if "error" in r:
            return r
    if key not in ("spend", "conversions", "revenue", "cpa", "roas"):
        return _bad("metric", metric, ["spend_usd", "conversions", "revenue_usd", "cpa", "roas"])
    return {"a": a[key], "b": b[key], "ratio": _div(a[key], b[key]), "metric": metric}


def compare_periods(channel: str, weeks: list[int], baseline_weeks: list[int], metric: str = "cpa", exclude: list[list] = []) -> dict:
    """Like-for-like change of one period vs another: weekly averages for totals, plain ratios for cpa/roas."""
    key, exclude = KPI_ALIAS.get(metric, metric), _ex(exclude)
    a, b = compute_kpi(channel, weeks, exclude), compute_kpi(channel, baseline_weeks, exclude)
    for r in (a, b):
        if "error" in r:
            return r
    if key not in ("spend", "conversions", "revenue", "cpa", "roas"):
        return _bad("metric", metric, ["spend_usd", "conversions", "revenue_usd", "cpa", "roas"])
    va, vb = a[key], b[key]
    if key in ("spend", "conversions", "revenue"):  # totals -> per-week averages so period lengths don't matter
        va, vb = round(va / len(a["weeks_used"]), 2), round(vb / len(b["weeks_used"]), 2)
    change = round(100 * (va - vb) / vb, 1) if vb else None
    return {"value": va, "baseline": vb, "change_pct": change, "metric": metric, "weeks": weeks, "baseline_weeks": baseline_weeks}


def check_target(metric: str, week: int, op: str, threshold: float, channel: str = "blended", exclude: list[list] = []) -> dict:
    if metric not in ("cpa", "revenue_usd") or op not in ("<", ">"):
        return {"error": "metric must be cpa|revenue_usd and op must be < or >", "valid": ["cpa", "revenue_usd", "<", ">"]}
    kpi = compute_kpi(channel, [week], _ex(exclude))
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


_S, _I = {"type": "string"}, {"type": "integer"}
_CH = {**_S, "description": "a channel name, or 'blended' for all channels summed"}
_WK = {"type": "array", "items": _I, "description": "week numbers"}
_EX = {"type": "array", "description": "[channel, week] pairs to leave out, e.g. broken data",
       "items": {"type": "array", "items": _S}}
_MET = {"type": "array", "items": _S}


def _obj(desc, props, required):
    return {"description": desc, "parameters": {"type": "object", "properties": props, "required": required}}


TOOLS = {  # name -> {fn, description, parameters}; the LLM only ever sees these specs
    "get_weekly": {"fn": get_weekly, **_obj("Raw weekly rows per channel/week for chosen metrics.",
                   {"channels": {"type": "array", "items": _S}, "weeks": _WK, "metrics": _MET}, [])},
    "compute_kpi": {"fn": compute_kpi, **_obj("Spend, conversions, revenue, CPA, ROAS summed over weeks (optionally excluding channel-weeks).",
                    {"channel": _CH, "weeks": _WK, "exclude": _EX}, ["channel", "weeks"])},
    "scan_changes": {"fn": scan_changes, **_obj("Deterministic list of outlier weeks and level shifts per channel: where to look.",
                     {"metrics": _MET, "z": {"type": "number"}}, [])},
    "compare_channels": {"fn": compare_channels, **_obj("Ratio of a metric (default cpa) between two channels over weeks.",
                         {"channel_a": _CH, "channel_b": _CH, "weeks": _WK, "metric": _S, "exclude": _EX},
                         ["channel_a", "channel_b", "weeks"])},
    "compare_periods": {"fn": compare_periods, **_obj("Change of a metric in `weeks` vs `baseline_weeks` (weekly averages for spend/conversions/revenue_usd; cpa and roas as ratios). Returns value, baseline, change_pct.",
                        {"channel": _CH, "weeks": _WK, "baseline_weeks": _WK, "metric": _S, "exclude": _EX},
                        ["channel", "weeks", "baseline_weeks", "metric"])},
    "check_target": {"fn": check_target, **_obj("Does a week's cpa or revenue_usd meet a threshold (op '<' or '>')?",
                     {"metric": _S, "week": _I, "op": _S, "threshold": {"type": "number"}, "channel": _CH, "exclude": _EX},
                     ["metric", "week", "op", "threshold"])},
}
