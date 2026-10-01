"""Append-only trace. Every agent step calls log(); one JSON object per line."""
import json
from datetime import datetime, timezone

TRACE_PATH = "trace.jsonl"
_seq = 0
_labels: dict = {}  # extra fields stamped on every row (used to label the invalid-draft demonstration)


def start(path: str = "trace.jsonl", **labels) -> None:
    """Begin a new trace file (truncating it); `labels` are added to every row."""
    global TRACE_PATH, _seq, _labels
    TRACE_PATH, _seq, _labels = path, 0, labels
    open(path, "w").close()


def log(agent: str, event: str, **payload) -> None:
    global _seq
    _seq += 1
    row = {"seq": _seq, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "agent": agent, "event": event, **_labels, **payload}
    with open(TRACE_PATH, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")
