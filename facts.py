"""What a recorded tool call proves, and a check that a finding says exactly that (also reusable by the reviewer)."""
import re

import tools

METRICS = ["spend_usd", "conversions", "revenue_usd", "cpa", "roas", "ctr", "cvr", "ratio"]
_KPI = {"spend": "spend_usd", "conversions": "conversions", "revenue": "revenue_usd", "cpa": "cpa", "roas": "roas"}
UP = {"higher", "increase", "increased", "up", "above", "rose", "grew", "more", "exceeds", "exceeded"}
DOWN = {"lower", "decrease", "decreased", "down", "below", "fell", "dropped", "less", "under"}


def _f(channel, weeks, metric, value, baseline=None, change_pct=None):
    return {"channel": channel, "weeks": sorted(weeks), "metric": _KPI.get(metric, metric),
            "value": value, "baseline": baseline, "change_pct": change_pct}


def facts_from(tool: str, args: dict, result: dict) -> list[dict]:
    """Every (channel, weeks, metric, value, baseline, change_pct) statement the tool result supports."""
    if "error" in result:
        return []
    if tool == "compute_kpi":
        return [_f(args["channel"], args["weeks"], k, result[k]) for k in _KPI]
    if tool == "compare_periods":
        return [_f(args["channel"], args["weeks"], result["metric"], result["value"], result["baseline"], result["change_pct"])]
    if tool == "compare_channels":  # the ratio belongs to channel_a; a and b are also plain values
        a, b, w, m = args["channel_a"], args["channel_b"], args["weeks"], result["metric"]
        return [_f(a, w, "ratio", result["ratio"]), _f(a, w, m, result["a"]), _f(b, w, m, result["b"])]
    if tool == "check_target":
        return [_f(args.get("channel", "blended"), [args["week"]], args["metric"], result["value"], result["threshold"])]
    if tool == "scan_changes":
        return [_f(c["channel"], c["weeks"], c["metric"], c["value"], c["baseline"], c["change_pct"]) for c in result["candidates"]]
    if tool == "get_weekly":
        return [_f(r["channel"], [r["week"]], m, v) for r in result["rows"] for m, v in r.items() if m not in ("week", "channel")]
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


def check_finding(d: dict, tool: str, args: dict, result: dict) -> str | None:
    """None if finding `d` is exactly supported by this call; otherwise a message the analyst can act on."""
    if d["metric"] not in METRICS:
        return f"metric must be one of {METRICS} (use 'ratio' for a compare_channels ratio)"
    facts = facts_from(tool, args, result)
    same = [x for x in facts if x["channel"] == d["channel"] and x["weeks"] == sorted(d["weeks"]) and x["metric"] == d["metric"]]
    if not same:
        seen = sorted({(x["channel"], str(x["weeks"]), x["metric"]) for x in facts})[:6]
        return f"call {tool}({args}) does not cover channel={d['channel']}, weeks={d['weeks']}, metric={d['metric']}. It covers e.g. {seen}"
    problem = None
    for x in [x for x in same if close(x["value"], d["value"])] or []:
        problem = None
        if d["baseline"] is not None and not close(x["baseline"], d["baseline"]):
            problem = f"baseline {d['baseline']} != tool baseline {x['baseline']} (set null if the tool gives none)"
        elif d["change_pct"] is not None and not close(x["change_pct"], d["change_pct"]):
            problem = f"change_pct {d['change_pct']} != tool change_pct {x['change_pct']} (set null if the tool gives none)"
        else:
            problem = _direction(d, x)
        if problem is None:
            break
    else:
        problem = problem or f"value {d['value']} does not match the tool value(s) {[x['value'] for x in same]}"
    if problem:
        return problem
    allowed = numbers(result) + [float(w) for w in tools.WEEKS]
    for tok in re.findall(r"\d[\d,]*\.?\d*", d["statement"]):
        if not any(close(float(tok.replace(",", "").rstrip(".")), n) or close(float(tok.replace(",", "").rstrip(".")), -n) for n in allowed):
            return f"statement number {tok} is not in the evidence call's result; remove it"
    return None


def _direction(d: dict, fact: dict) -> str | None:
    """Words like 'higher'/'lower' in the statement must agree with value vs baseline."""
    if fact["baseline"] is None or fact["value"] == fact["baseline"]:
        return None
    words = set(re.findall(r"[a-z]+", d["statement"].lower()))
    went_up = fact["value"] > fact["baseline"]
    if words & UP and not words & DOWN and not went_up:
        return f"statement says up but value {fact['value']} is below baseline {fact['baseline']}"
    if words & DOWN and not words & UP and went_up:
        return f"statement says down but value {fact['value']} is above baseline {fact['baseline']}"
    return None
