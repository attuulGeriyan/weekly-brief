"""Analyst: an LLM with real tools. Pass 1 works on the numbers only and never sees the notes."""
import json
from typing import Literal

from pydantic import BaseModel, ValidationError

import facts
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
    call_id: int  # the tool call that supports this finding
    derived_from: str | None = None


class Submission(BaseModel):
    findings: list[FindingDraft]


SYSTEM = """You are a marketing data analyst. Investigate the data with the tools; you never calculate numbers yourself.
Every number you report must be copied from a tool result. Do not round or derive new ones.
A finding is checked against ONE tool call (its call_id): channel, weeks and metric must be exactly what that call covered,
value/baseline/change_pct exactly what it returned (null if it returned none), and words like higher/lower must match the data.
metric is one of spend_usd, conversions, revenue_usd, cpa, roas, ctr, cvr, or "ratio" for a compare_channels ratio
(then channel is channel_a). Statements may only contain numbers that appear in that call's result.
Data: channels {channels}; weeks {weeks} (week 1 is the earliest). Blended means all channels summed, then divided.
Each tool result has a "call_id". When you submit a finding, set call_id to the call whose result contains its `value`.
A statement is one factual sentence with numbers only: no guesses about causes."""

PASS1 = """The latest week is {week}. You have no notes or context beyond the data.
Find 5-8 findings. You must include: (a) blended results for the latest week versus the earlier weeks (use compare_periods
with weeks=[latest week] and baseline_weeks=the earlier weeks), and (b) the most important anomalies and
sustained trend breaks, which may be about spend, conversions, revenue or CPA.
Prefer compare_periods for period comparisons: it is like-for-like and returns value, baseline and change_pct.
Make sure every channel that scan_changes flags appears in at least one finding.
Start with scan_changes using its default metrics, then size things with the other tools. You may make several tool
calls in one step. You have 8 steps, so call submit_findings by step 6 at the latest."""


PASS2 = """Context from the team notes now tells us what else to compute. For each item below, run its follow-up tool calls
(you may adjust args, for example a fuller week range, but keep the intent), then submit one finding per follow-up result.
Set derived_from to the first finding id listed for the item. Use kind "test" for decision-rule evaluations
and "target" for target checks. Leave baseline null unless a tool result gives one. Statements: numbers only, no interpretation.
When a call excludes data records, say so in plain words, e.g. "excluding the google_search records for weeks 5 and 6" (never a Python list);
never call such a number corrected or true.

{items}"""


def _task(state: RunState, pass_no: int) -> str:
    if pass_no == 1:
        return PASS1.format(week=state.as_of_week)
    groups = {}  # identical follow-ups from several attachments are run once
    for a in state.attachments:
        for fu in a.follow_ups:
            g = groups.setdefault((fu.tool, json.dumps(fu.args, sort_keys=True)), {"fu": fu, "findings": [], "notes": [], "impl": []})
            g["findings"].append(a.finding_id)
            g["notes"] += [a.note_id]
            g["impl"] += [a.implication]
    items = [f"- findings {g['findings']} / notes {sorted(set(g['notes']))} ({', '.join(sorted(set(g['impl'])))}): "
             f"{g['fu'].tool}({g['fu'].args}) because {g['fu'].why}" for g in groups.values()]
    return PASS2.format(items="\n".join(items))


def _key(tool: str, args: dict) -> tuple:
    """Which question a call answers (tool + channel/metric), so pass 2 can check every follow-up was answered."""
    ids = {"check_target": ("metric", "channel"), "compare_channels": ("channel_a", "channel_b", "metric")}.get(tool, ("channel",))
    defaults = {"channel": "blended", "metric": "cpa"}
    return (tool,) + tuple(args.get(k, defaults.get(k)) for k in ids)


def run(state: RunState, pass_no: int = 1) -> list[Finding]:
    calls = []  # every tool call made in this loop: {tool, args, result}

    def emit(event, **payload):
        trace.log("analyst", event, **{"pass": pass_no}, **payload)

    def wrap(name):  # record each call so evidence can't be invented
        def call(**args):
            result = tools.TOOLS[name]["fn"](**args)
            calls.append({"tool": name, "args": args, "result": result})
            return {"call_id": len(calls) - 1, **result}
        return call

    accepted = []  # validated drafts; kept here so the tool result stays small and readable in the trace

    def submit(findings):
        try:
            sub = Submission.model_validate({"findings": findings})
        except ValidationError as e:
            return {"error": f"invalid findings: {e.errors()[0]['loc']} {e.errors()[0]['msg']}"}
        if pass_no == 1 and not 5 <= len(sub.findings) <= 8:
            return {"error": f"submit 5-8 findings, got {len(sub.findings)}"}
        refs = [f.id for f in state.findings] + [n.note_id for n in state.notes]
        if pass_no == 2 and any(d.derived_from not in refs for d in sub.findings):
            return {"error": f"derived_from must be one of {refs}"}
        for i, d in enumerate(sub.findings):
            if not 0 <= d.call_id < len(calls) or "error" in calls[d.call_id]["result"]:
                return {"error": f"finding {i}: call_id {d.call_id} is not a successful tool call"}
            c = calls[d.call_id]
            if problem := facts.check_finding(d.model_dump(), c["tool"], c["args"], c["result"]):
                return {"error": f"finding {i}: {problem}"}
        if pass_no == 2:  # every validated follow-up must be answered by at least one finding
            need = {_key(fu.tool, fu.args): fu for a in state.attachments for fu in a.follow_ups}
            got = {_key(calls[d.call_id]["tool"], calls[d.call_id]["args"]) for d in sub.findings}
            if missing := [fu for k, fu in need.items() if k not in got]:
                todo = "; ".join(f"{fu.tool}({fu.args})" for fu in missing)
                return {"error": f"not done yet. Call these tools first, then resubmit ALL findings including one for each: {todo}"}
        accepted[:] = [d.model_dump() for d in sub.findings]
        return {"ok": True, "accepted": len(accepted)}

    toolset = [{"name": n, "description": t["description"], "parameters": t["parameters"], "fn": wrap(n)}
               for n, t in tools.TOOLS.items()]
    toolset.append({"name": "submit_findings", "description": "Submit the final findings. Call once, at the end.",
                    "parameters": Submission, "fn": submit, "final": True})
    system = SYSTEM.format(channels=tools.CHANNELS, weeks=tools.WEEKS)
    user = _task(state, pass_no)
    out = llm.run_tool_loop(system, user, toolset, max_steps=8, on_event=emit)
    if not out["final"]:
        emit("error", detail="no valid submission")
        raise RuntimeError(f"analyst pass {pass_no} produced no valid findings after {out['steps']} steps")
    findings = []
    for d in accepted:
        call = calls[d.pop("call_id")]
        f = Finding(id=f"F{len(state.findings) + len(findings) + 1}", pass_no=pass_no, evidence=Evidence(**call), **d)
        # readable proof in the trace (tool_result events are truncated): the finding next to the call that supports it
        emit("finding_recorded", id=f.id, kind=f.kind, channel=f.channel, weeks=f.weeks, metric=f.metric, value=f.value,
             baseline=f.baseline, change_pct=f.change_pct, statement=f.statement, derived_from=f.derived_from,
             evidence={"tool": call["tool"], "args": call["args"]})
        findings.append(f)
    return findings
