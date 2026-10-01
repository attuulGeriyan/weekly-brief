"""What a recorded tool call proves, and a check that a finding says exactly that (also reusable by the reviewer)."""
import re

import tools

METRICS = ["spend_usd", "conversions", "revenue_usd", "cpa", "roas", "ctr", "cvr", "ratio"]
ADDITIVE = ("spend_usd", "conversions", "revenue_usd")  # metrics where "total" vs "weekly average" changes the meaning
_KPI = {"spend": "spend_usd", "conversions": "conversions", "revenue": "revenue_usd", "cpa": "cpa", "roas": "roas"}
UP = {"higher", "increase", "increased", "up", "above", "rose", "grew", "more", "exceeds", "exceeded"}
DOWN = {"lower", "decrease", "decreased", "down", "below", "fell", "dropped", "less", "under"}
# how a number was aggregated -> words the sentence must use, and how to describe it
AGG_RE = {"weekly_average": r"averag|per week|a week|weekly|mean", "weekly_median": r"median|typical", "total": r"total|combined|\bsum\b|overall"}
AGG_LABEL = {"weekly_average": "weekly average", "weekly_median": "typical week (median)", "total": "total over the weeks",
             "single_week": "single week", "period_ratio": "period figure"}


def _f(channel, weeks, metric, value, baseline=None, change_pct=None, agg=None, agg_base=None):
    return {"channel": channel, "weeks": sorted(weeks), "metric": _KPI.get(metric, metric), "value": value,
            "baseline": baseline, "change_pct": change_pct, "agg": agg, "agg_base": agg_base}


def _sum_or_week(metric: str, n_weeks: int) -> str:
    """Aggregation of a summed KPI: totals for additive metrics, period ratios for cpa/roas."""
    return ("total" if n_weeks > 1 else "single_week") if metric in ADDITIVE else "period_ratio"


def facts_from(tool: str, args: dict, result: dict) -> list[dict]:
    """Every (channel, weeks, metric, value, baseline, change_pct, aggregation) statement the tool result supports."""
    if "error" in result:
        return []
    if tool == "compute_kpi":
        n = len(result["weeks_used"])
        return [_f(args["channel"], args["weeks"], k, result[k], agg=_sum_or_week(_KPI[k], n)) for k in _KPI]
    if tool == "compare_periods":
        m = _KPI.get(result["metric"], result["metric"])
        kind = lambda ws: ("single_week" if len(ws) == 1 else "weekly_average") if m in ADDITIVE else "period_ratio"
        return [_f(args["channel"], args["weeks"], m, result["value"], result["baseline"], result["change_pct"],
                   kind(args["weeks"]), kind(args["baseline_weeks"]))]
    if tool == "compare_channels":  # the ratio belongs to channel_a; a and b are also plain values
        a, b, w, m = args["channel_a"], args["channel_b"], args["weeks"], _KPI.get(result["metric"], result["metric"])
        return [_f(a, w, "ratio", result["ratio"], agg="period_ratio"), _f(a, w, m, result["a"], agg=_sum_or_week(m, len(w))),
                _f(b, w, m, result["b"], agg=_sum_or_week(m, len(w)))]
    if tool == "check_target":
        return [_f(args.get("channel", "blended"), [args["week"]], args["metric"], result["value"], result["threshold"], agg="single_week", agg_base="target")]
    if tool == "scan_changes":
        out = []
        for c in result["candidates"]:
            shift = c["type"] == "level_shift"
            ratio_metric = c["metric"] not in ADDITIVE
            out.append(_f(c["channel"], c["weeks"], c["metric"], c["value"], c["baseline"], c["change_pct"],
                          ("period_ratio" if ratio_metric else "weekly_average") if shift else "single_week",
                          ("period_ratio" if ratio_metric else "weekly_average") if shift else "weekly_median"))
        return out
    if tool == "get_weekly":
        return [_f(r["channel"], [r["week"]], m, v, agg="single_week") for r in result["rows"] for m, v in r.items() if m not in ("week", "channel")]
    return []


def numbers(obj, out=None) -> list[float]:
    """Every number anywhere inside a tool result."""
    out = [] if out is None else out
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(float(obj))
    elif isinstance(obj, dict):
        [numbers(v, out) for v in obj.values()]
    elif isinstance(obj, list):
        [numbers(v, out) for v in obj]
    return out


def close(a, b) -> bool:
    return a is not None and b is not None and abs(a - b) <= 0.011


def match(d: dict, tool: str, args: dict, result: dict) -> tuple[dict | None, str | None]:
    """(matching fact, None) if finding `d` is exactly supported by this call; otherwise (None, message the analyst can act on)."""
    if d["metric"] not in METRICS:
        return None, f"metric must be one of {METRICS} (use 'ratio' for a compare_channels ratio)"
    facts = facts_from(tool, args, result)
    same = [x for x in facts if x["channel"] == d["channel"] and x["weeks"] == sorted(d["weeks"]) and x["metric"] == d["metric"]]
    if not same:
        seen = sorted({(x["channel"], str(x["weeks"]), x["metric"]) for x in facts})[:6]
        return None, f"call {tool}({args}) does not cover channel={d['channel']}, weeks={d['weeks']}, metric={d['metric']}. It covers e.g. {seen}"
    problem = None
    for x in [x for x in same if close(x["value"], d["value"])]:
        if d["baseline"] is not None and not close(x["baseline"], d["baseline"]):
            problem = f"baseline {d['baseline']} != tool baseline {x['baseline']} (set null if the tool gives none)"
        elif d["change_pct"] is not None and not close(x["change_pct"], d["change_pct"]):
            problem = f"change_pct {d['change_pct']} != tool change_pct {x['change_pct']} (set null if the tool gives none)"
        elif problem := _direction(d, x) or _aggregation(d, x) or _statement_numbers(d, result):
            continue
        else:
            return x, None
    return None, problem or f"value {d['value']} does not match the tool value(s) {[x['value'] for x in same]}"


def check_finding(d: dict, tool: str, args: dict, result: dict) -> str | None:
    return match(d, tool, args, result)[1]


def _statement_numbers(d: dict, result: dict) -> str | None:
    allowed = numbers(result) + [float(w) for w in tools.WEEKS]
    for tok in re.findall(r"\d[\d,]*\.?\d*", d["statement"]):
        x = float(tok.replace(",", "").rstrip("."))
        if not any(close(x, n) or close(x, -n) for n in allowed):
            return f"statement number {tok} is not in the evidence call's result; remove it"
    return None


def _aggregation(d: dict, x: dict) -> str | None:
    """A weekly average must not read as a period total: the statement has to name how spend/conversions/revenue were aggregated."""
    if x["metric"] not in ADDITIVE:
        return None
    for label, agg, given in (("value", x["agg"], True), ("baseline", x["agg_base"], d["baseline"] is not None)):
        if given and AGG_RE.get(agg) and not re.search(AGG_RE[agg], d["statement"], re.I):
            return f"the {label} is a {AGG_LABEL[agg]}; the statement must say so (e.g. 'weekly average', 'per week', 'total')"
    return None


def _direction(d: dict, fact: dict) -> str | None:
    """Words like 'higher'/'lower' in the statement must agree with value vs baseline."""
    if fact["baseline"] is None or fact["value"] == fact["baseline"] or fact["agg_base"] == "target":
        return None
    words = set(re.findall(r"[a-z]+", d["statement"].lower()))
    went_up = fact["value"] > fact["baseline"]
    if words & UP and not words & DOWN and not went_up:
        return f"statement says up but value {fact['value']} is below baseline {fact['baseline']}"
    if words & DOWN and not words & UP and went_up:
        return f"statement says down but value {fact['value']} is above baseline {fact['baseline']}"
    return None
