"""Hand-written orchestrator: runs the agents in order and merges their typed output into RunState."""
import argparse
import os
from datetime import datetime

import llm
import tools
import trace
from agents import analyst, context, index_notes
from state import RunState


def step(agent: str, fn, *args, summary=lambda out: ""):
    """Run one agent with agent_start/agent_end trace events."""
    trace.log(agent, "agent_start")
    try:
        out = fn(*args)
    except Exception as e:  # a required step failed: record it and stop, never carry on with partial state
        trace.log(agent, "agent_error", detail=f"{type(e).__name__}: {e}")
        trace.log("orchestrator", "done", status="failed", failed_agent=agent)
        raise SystemExit(f"Run stopped: {agent} failed: {e} (see trace.jsonl)")
    trace.log(agent, "agent_end", summary=summary(out))
    return out


def handoff(frm: str, to: str, field: str):
    trace.log("orchestrator", "handoff", frm=frm, to=to, changed=[field])


def show(state: RunState):
    """Print the state built so far (used by --stop-after)."""
    for f in state.findings:
        print(f"{f.id} p{f.pass_no} [{f.kind}] {f.channel} wk{f.weeks} {f.metric}={f.value} (baseline {f.baseline}, {f.change_pct}%)"
              f"{' derived_from ' + f.derived_from if f.derived_from else ''}")
        print(f"    {f.statement}\n    evidence: {f.evidence.tool}({f.evidence.args})")
    for a in state.attachments:
        print(f"{a.finding_id} -> {a.note_id} [{a.implication}] {a.relevance}")
        for fu in a.follow_ups:
            print(f"    follow-up: {fu.tool}({fu.args})")
    used = {a.note_id for a in state.attachments}
    print("notes with no attachment:", [n.note_id for n in state.notes if n.note_id not in used] or "none")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--as-of-week", type=int, default=tools.WEEKS[-1])
    p.add_argument("--inject-error", action="store_true")
    p.add_argument("--model", default=None)
    p.add_argument("--stop-after", choices=["analyst1", "analyst2", "writer"], default=None)
    args = p.parse_args()
    if args.model:
        llm.MODEL = args.model
    if os.path.exists(trace.TRACE_PATH):
        os.remove(trace.TRACE_PATH)  # one trace per run

    state = RunState(run_id=datetime.now().strftime("%Y%m%d-%H%M%S"), as_of_week=args.as_of_week, model=llm.MODEL or "")
    trace.log("orchestrator", "run_start", run_id=state.run_id, as_of_week=state.as_of_week, model=state.model)

    state.notes = step("index_notes", index_notes.run, state, summary=lambda ns: [n.note_id for n in ns])
    handoff("index_notes", "analyst", "notes")

    state.findings += step("analyst", analyst.run, state, 1, summary=lambda fs: [f.id for f in fs])
    handoff("analyst", "context", "findings")
    if args.stop_after == "analyst1":
        return show(state)

    state.attachments = step("context", context.run, state, summary=lambda a: [f"{x.finding_id}->{x.note_id}" for x in a])
    handoff("context", "analyst", "attachments")
    if any(a.follow_ups for a in state.attachments):
        state.findings += step("analyst", analyst.run, state, 2, summary=lambda fs: [f.id for f in fs])
    else:
        trace.log("orchestrator", "skip", detail="no follow-ups from context, so analyst pass 2 is not needed")
    handoff("analyst", "writer", "findings")
    if args.stop_after == "analyst2":
        return show(state)


if __name__ == "__main__":
    main()
