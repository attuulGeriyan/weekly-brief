"""JSON schemas and descriptions of the tools, exactly what the LLM sees (data only; the functions live in tools.py)."""
_S, _I = {"type": "string"}, {"type": "integer"}
_CH = {**_S, "description": "a channel name, or 'blended' for all channels summed"}
_WK = {"type": "array", "items": _I, "description": "week numbers"}
_EX = {"type": "array", "description": "[channel, week] pairs to leave out, e.g. broken data",
       "items": {"type": "array", "items": _S}}
_MET = {"type": "array", "items": _S}


def _obj(desc, props, required):
    return {"description": desc, "parameters": {"type": "object", "properties": props, "required": required}}


SPECS = {  # name -> {description, parameters}; the LLM only ever sees these
    "get_weekly": _obj("Raw weekly rows per channel/week for chosen metrics.",
                   {"channels": {"type": "array", "items": _S}, "weeks": _WK, "metrics": _MET}, []),
    "compute_kpi": _obj("Spend, conversions, revenue, CPA, ROAS summed over weeks (optionally excluding channel-weeks).",
                    {"channel": _CH, "weeks": _WK, "exclude": _EX}, ["channel", "weeks"]),
    "scan_changes": _obj("Deterministic list of outlier weeks and level shifts per channel: where to look.",
                     {"metrics": _MET, "z": {"type": "number"}}, []),
    "compare_channels": _obj("Ratio of a metric (default cpa) between two channels over weeks.",
                         {"channel_a": _CH, "channel_b": _CH, "weeks": _WK, "metric": _S, "exclude": _EX},
                         ["channel_a", "channel_b", "weeks"]),
    "compare_periods": _obj("Change of a metric in `weeks` vs `baseline_weeks` (weekly averages for spend/conversions/revenue_usd; cpa and roas as ratios). Returns value, baseline, change_pct.",
                        {"channel": _CH, "weeks": _WK, "baseline_weeks": _WK, "metric": _S, "exclude": _EX},
                        ["channel", "weeks", "baseline_weeks", "metric"]),
    "check_target": _obj("Does a week's cpa or revenue_usd meet a threshold (op '<' or '>')?",
                     {"metric": _S, "week": _I, "op": _S, "threshold": {"type": "number"}, "channel": _CH, "exclude": _EX},
                     ["metric", "week", "op", "threshold"]),
}
