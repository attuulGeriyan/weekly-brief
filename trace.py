"""Append-only trace. Every agent step calls log(); one JSON object per line."""
import json
from datetime import datetime, timezone

TRACE_PATH = "trace.jsonl"
_seq = 0


def log(agent: str, event: str, **payload) -> None:
    global _seq
    _seq += 1
    row = {"seq": _seq, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "agent": agent, "event": event, **payload}
    with open(TRACE_PATH, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")
