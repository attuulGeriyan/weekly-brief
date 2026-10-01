"""Orchestration helpers: run one agent with trace events, and the bounded write -> review -> retry loop."""
import agents.reviewer as reviewer
import agents.writer as writer
import trace
from state import RunState

MAX_RETRIES = 2


def step(agent: str, fn, *args, summary=lambda out: "", info=None):
    """Run one agent with agent_start/agent_end trace events; a failure stops the whole run clearly."""
    trace.log(agent, "agent_start", **(info or {}))
    try:
        out = fn(*args)
    except Exception as e:  # a required step failed: record it and stop, never carry on with partial state
        trace.log(agent, "agent_error", detail=f"{type(e).__name__}: {e}")
        trace.log("orchestrator", "done", status="failed", failed_agent=agent)
        raise SystemExit(f"Run stopped: {agent} failed: {e} (see {trace.TRACE_PATH})")
    trace.log(agent, "agent_end", summary=summary(out))
    return out


def handoff(frm: str, to: str, field: str, **detail):
    """A structured handoff: which state field changed, plus what the receiving agent is being given."""
    trace.log("orchestrator", "handoff", frm=frm, to=to, changed=[field], **detail)


def finding_view(f) -> dict:
    return {"id": f.id, "pass": f.pass_no, "channel": f.channel, "weeks": f.weeks, "metric": f.metric, "value": f.value,
            "aggregation": f.aggregation, "derived_from": f.derived_from}


def attachment_view(a) -> dict:
    return {"finding_id": a.finding_id, "note_id": a.note_id, "implication": a.implication,
            "follow_ups": [{"tool": fu.tool, "args": fu.args} for fu in a.follow_ups]}


def write_and_review(state: RunState, first_draft: str | None = None) -> bool:
    """Writer -> reviewer, with at most MAX_RETRIES rewrites. Returns True if a draft was approved.
    `first_draft` is only used by the invalid-draft demonstration; the genuine run always asks the writer."""
    issues, previous = [], None
    for attempt in range(1, MAX_RETRIES + 2):
        state.attempts = attempt
        if attempt == 1 and first_draft is not None:
            draft, source = first_draft, "supplied by the invalid-draft demonstration"
        else:
            draft = step("writer", writer.run, state, issues, previous, summary=lambda d: f"{len(d.split())} words",
                         info={"attempt": attempt, "fixing_issues": [i.model_dump() for i in issues]})
            source = "writer"
        state.drafts.append(draft)
        trace.log("writer", "draft", attempt=attempt, source=source, words=len(draft.split()), text=draft)  # every draft, in full
        handoff("writer", "reviewer", "drafts", attempt=attempt, words=len(draft.split()))
        review = step("reviewer", reviewer.run, state, draft, summary=lambda r: f"{r.verdict}, {len(r.issues)} issues", info={"attempt": attempt})
        state.reviews.append(review)
        trace.log("reviewer", "review_verdict", attempt=attempt, verdict=review.verdict, issues=[i.model_dump() for i in review.issues])
        if review.verdict == "approve":
            return True
        if attempt == MAX_RETRIES + 1:
            trace.log("orchestrator", "retries_exhausted", attempts=attempt, max_retries=MAX_RETRIES)
            break
        issues, previous = review.issues, draft
        trace.log("orchestrator", "retry", attempt=attempt, next_attempt=attempt + 1, issues=[i.model_dump() for i in issues])
        handoff("reviewer", "writer", "reviews", attempt=attempt, verdict="reject", issues=[i.model_dump() for i in issues])
    return False
