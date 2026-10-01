"""Hand-written orchestrator: runs the agents in order and merges their typed output into RunState."""
import argparse
import hashlib
from datetime import datetime

import demo
import llm
import render_trace
import tools
import trace
from agents import analyst, context, index_notes, writer
from loop import attachment_view, finding_view, handoff, step, write_and_review
from state import RunState


def show(state: RunState):
    """Print the state built so far (used by --stop-after)."""
    for f in state.findings:
        print(f"{f.id} p{f.pass_no} [{f.kind}] {f.channel} wk{f.weeks} {f.metric}={f.value} [{f.aggregation}] (baseline {f.baseline}, {f.change_pct}%)"
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

    state = RunState(run_id=datetime.now().strftime("%Y%m%d-%H%M%S"), as_of_week=args.as_of_week, model=llm.MODEL or "")
    trace.start("trace.jsonl", run_id=state.run_id)  # every row of this run carries the run id
    trace.log("orchestrator", "run_start", as_of_week=state.as_of_week, model_requested=state.model, argv=vars(args))

    state.notes = step("index_notes", index_notes.run, state, summary=lambda ns: [n.note_id for n in ns])
    state.model_resolved = llm.RESOLVED
    handoff("index_notes", "analyst", "notes", notes=[{"note_id": n.note_id, "kind": n.kind, "channels": n.channels, "date_from": n.date_from,
                                                      "date_to": n.date_to, "rule": n.rule, "targets": [t.model_dump() for t in n.targets]} for n in state.notes])
    state.findings += step("analyst", analyst.run, state, 1, summary=lambda fs: [f.id for f in fs], info={"pass": 1})
    handoff("analyst", "context", "findings", findings=[finding_view(f) for f in state.findings])
    if args.stop_after == "analyst1":
        return show(state)

    state.attachments = step("context", context.run, state, summary=lambda a: [f"{x.finding_id}->{x.note_id}" for x in a])
    handoff("context", "analyst", "attachments", attachments=[attachment_view(a) for a in state.attachments])
    new = []
    if any(a.follow_ups for a in state.attachments):
        new = step("analyst", analyst.run, state, 2, summary=lambda fs: [f.id for f in fs], info={"pass": 2})
        state.findings += new
    else:
        trace.log("orchestrator", "skip", detail="no follow-ups from context, so analyst pass 2 is not needed")
    handoff("analyst", "writer", "findings", new_findings=[finding_view(f) for f in new], total_findings=len(state.findings))
    if args.stop_after == "analyst2":
        return show(state)

    approved = write_and_review(state)
    brief = writer.render(state.drafts[-1], state)
    if not approved:
        brief = "> ⚠ Reviewer did not approve: " + "; ".join(i.detail for i in state.reviews[-1].issues) + "\n\n" + brief
    state.brief = brief + f"\n\n<!-- run_id: {state.run_id} -->\n"
    open("brief.md", "w").write(state.brief)
    open("state.json", "w").write(state.model_dump_json(indent=1))
    trace.log("orchestrator", "done", status="approved" if approved else "not_approved", attempts=state.attempts, brief_words=len(brief.split()),
              brief_sha256=hashlib.sha256(state.brief.encode()).hexdigest(), model_requested=state.model, model_resolved=state.model_resolved)
    if args.stop_after == "writer":
        show(state)
    checks = render_trace.write_all(".")  # trace.md + trace.json, and cross-checks that brief, state and trace are one run
    print(state.brief)
    print("\n".join(("OK   " if ok else "FAIL ") + msg for ok, msg in checks))


if __name__ == "__main__":
    main()
