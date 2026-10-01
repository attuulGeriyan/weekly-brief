"""Hand-written orchestrator: runs the agents in order and merges their typed output into RunState."""
import argparse
from datetime import datetime

import demo
import llm
import tools
import trace
from agents import analyst, context, index_notes, writer
from loop import handoff, step, write_and_review
from state import RunState


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
    if state.drafts:
        print("\n--- last draft ---\n" + state.drafts[-1])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--as-of-week", type=int, default=tools.WEEKS[-1])
    p.add_argument("--inject-error", action="store_true",
                   help="SEPARATE demonstration: corrupt the saved genuine draft and show reject -> retry -> approve")
    p.add_argument("--model", default=None)
    p.add_argument("--stop-after", choices=["analyst1", "analyst2", "writer"], default=None)
    args = p.parse_args()
    if args.model:
        llm.MODEL = args.model
    if args.inject_error:
        return demo.run()

    trace.start("trace.jsonl")  # one trace per genuine run
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

    approved = write_and_review(state)
    brief = writer.render(state.drafts[-1], state)
    if not approved:
        brief = "> ⚠ Reviewer did not approve: " + "; ".join(i.detail for i in state.reviews[-1].issues) + "\n\n" + brief
    state.brief = brief
    open("brief.md", "w").write(brief + "\n")
    open("state.json", "w").write(state.model_dump_json(indent=1))
    trace.log("orchestrator", "done", status="approved" if approved else "not_approved", attempts=state.attempts, words=len(brief.split()))
    print(brief if args.stop_after != "writer" else "")
    if args.stop_after == "writer":
        show(state)


if __name__ == "__main__":
    main()
