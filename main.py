"""Hand-written orchestrator: runs the agents in order and merges their typed output into RunState."""
import argparse
import os
from datetime import datetime

import llm
import tools
import trace
from agents import analyst
from state import RunState


def step(agent: str, fn, *args, summary=lambda out: ""):
    """Run one agent with agent_start/agent_end trace events."""
    trace.log(agent, "agent_start")
    out = fn(*args)
    trace.log(agent, "agent_end", summary=summary(out))
    return out


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

    new = step("analyst", analyst.run, state, 1, summary=lambda fs: [f.id for f in fs])
    state.findings += new
    trace.log("orchestrator", "handoff", frm="analyst", to="context", changed=["findings"], ids=[f.id for f in new])
    if args.stop_after == "analyst1":
        for f in state.findings:
            print(f"{f.id} [{f.kind}] {f.channel} wk{f.weeks} {f.metric}={f.value} (baseline {f.baseline}, {f.change_pct}%)")
            print(f"    {f.statement}\n    evidence: {f.evidence.tool}({f.evidence.args})")
        return


if __name__ == "__main__":
    main()
