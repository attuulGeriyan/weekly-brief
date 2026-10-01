"""Analyst: an LLM with real tools. Pass 1 works on the numbers only and never sees the notes."""
import re
from typing import Literal

from pydantic import BaseModel, ValidationError

import llm
import tools
import trace
from state import Evidence, Finding, RunState


class FindingDraft(BaseModel):  # what the model submits; the harness adds id, pass_no and evidence
    kind: Literal["anomaly", "trend", "test", "target", "summary"]
    channel: str
    weeks: list[int]
    metric: str
    value: float
    baseline: float | None = None
    change_pct: float | None = None
    statement: str
    call_id: int  # the tool call whose result contains `value`
    derived_from: str | None = None


class Submission(BaseModel):
    findings: list[FindingDraft]


SYSTEM = """You are a marketing data analyst. Investigate the data with the tools; you never calculate numbers yourself.
Every number you report (value, baseline, change_pct) must be copied from a tool result. Do not round or derive new ones.
If no tool result gives a change_pct for a finding (only scan_changes does), set it to null.
Data: channels {channels}; weeks {weeks} (week 1 is the earliest). Blended means all channels summed, then divided.
Each tool result has a "call_id". When you submit a finding, set call_id to the call whose result contains its `value`.
A statement is one factual sentence with numbers only: no guesses about causes."""

PASS1 = """The latest week is {week}. You have no notes or context beyond the data.
Find 5-8 findings. You must include: (a) blended results for the latest week versus the earlier weeks (use compare_periods
with weeks=[latest week] and baseline_weeks=the earlier weeks), and (b) the most important anomalies and
sustained trend breaks, which may be about spend, conversions, revenue or CPA.
Prefer compare_periods for period comparisons: it is like-for-like and returns value, baseline and change_pct.
Start with scan_changes using its default metrics, then size things with the other tools. You may make several tool
calls in one step. You have 8 steps, so call submit_findings by step 6 at the latest."""


def _numbers(obj, out=None):
    """Every number anywhere inside a tool result."""
    out = [] if out is None else out
    if isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        [_numbers(v, out) for v in obj.values()]
    elif isinstance(obj, list):
        [_numbers(v, out) for v in obj]
    return out


def _has(x, nums):
    return any(abs(x - n) <= 0.011 for n in nums)


def run(state: RunState, pass_no: int = 1, task: str | None = None, source: str = "analyst") -> list[Finding]:
    calls = []  # every tool call made in this loop: {tool, args, result}

    def emit(event, **payload):
        trace.log("analyst", event, **{"pass": pass_no}, **payload)

    def wrap(name):  # record each call so evidence can't be invented
        def call(**args):
            result = tools.TOOLS[name]["fn"](**args)
            calls.append({"tool": name, "args": args, "result": result})
            return {"call_id": len(calls) - 1, **result}
        return call

    def submit(findings):
        try:
            sub = Submission.model_validate({"findings": findings})
        except ValidationError as e:
            return {"error": f"invalid findings: {e.errors()[0]['loc']} {e.errors()[0]['msg']}"}
        if not 5 <= len(sub.findings) <= 8 and pass_no == 1:
            return {"error": f"submit 5-8 findings, got {len(sub.findings)}"}
        all_nums = [abs(n) for c in calls for n in _numbers(c["result"])]
        for i, d in enumerate(sub.findings):
            if not 0 <= d.call_id < len(calls) or "error" in calls[d.call_id]["result"]:
                return {"error": f"finding {i}: call_id {d.call_id} is not a successful tool call"}
            if not _has(d.value, _numbers(calls[d.call_id]["result"])):
                return {"error": f"finding {i}: value {d.value} is not in the result of call {d.call_id}"}
            for name in ("baseline", "change_pct"):
                x = getattr(d, name)
                if x is not None and not _has(abs(x), all_nums):
                    return {"error": f"finding {i}: {name} {x} is not in any tool result; copy it or set null"}
            for tok in re.findall(r"\d[\d,]*\.?\d*", d.statement):  # every number in the sentence must come from a tool
                x = float(tok.replace(",", "").rstrip("."))
                if not (x.is_integer() and int(x) in tools.WEEKS) and not _has(x, all_nums):
                    return {"error": f"finding {i}: statement number {tok} is not in any tool result; remove it"}
            if d.channel not in tools.CHANNELS + ["blended"] or any(w not in tools.WEEKS for w in d.weeks):
                return {"error": f"finding {i}: unknown channel or week"}
        return {"ok": True, "drafts": [d.model_dump() for d in sub.findings]}

    toolset = [{"name": n, "description": t["description"], "parameters": t["parameters"], "fn": wrap(n)}
               for n, t in tools.TOOLS.items()]
    toolset.append({"name": "submit_findings", "description": "Submit the final findings. Call once, at the end.",
                    "parameters": Submission, "fn": submit, "final": True})
    system = SYSTEM.format(channels=tools.CHANNELS, weeks=tools.WEEKS)
    user = task or PASS1.format(week=state.as_of_week)
    out = llm.run_tool_loop(system, user, toolset, max_steps=8, on_event=emit)
    if not out["final"]:
        emit("error", detail="analyst ended without submitting findings")
        return []
    findings = []
    for d in out["final"]["drafts"]:
        call = calls[d.pop("call_id")]
        fid = f"F{len(state.findings) + len(findings) + 1}"
        findings.append(Finding(id=fid, pass_no=pass_no, evidence=Evidence(**call), **d))
    return findings
