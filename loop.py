"""Orchestration helpers: run one agent with trace events, and the bounded write -> review -> retry loop."""
import agents.reviewer as reviewer
import agents.writer as writer
import trace
from state import RunState

MAX_RETRIES = 2


def step(agent: str, fn, *args, summary=lambda out: ""):
    """Run one agent with agent_start/agent_end trace events; a failure stops the whole run clearly."""
    trace.log(agent, "agent_start")
    try:
        out = fn(*args)
    except Exception as e:  # a required step failed: record it and stop, never carry on with partial state
        trace.log(agent, "agent_error", detail=f"{type(e).__name__}: {e}")
        trace.log("orchestrator", "done", status="failed", failed_agent=agent)
        raise SystemExit(f"Run stopped: {agent} failed: {e} (see {trace.TRACE_PATH})")
    trace.log(agent, "agent_end", summary=summary(out))
    return out


def handoff(frm: str, to: str, field: str):
    trace.log("orchestrator", "handoff", frm=frm, to=to, changed=[field])


def write_and_review(state: RunState, first_draft: str | None = None) -> bool:
    """Writer -> reviewer, with at most MAX_RETRIES rewrites. Returns True if a draft was approved.
    `first_draft` is only used by the invalid-draft demonstration; the genuine run always asks the writer."""
    issues, previous = [], None
    for attempt in range(1, MAX_RETRIES + 2):
        state.attempts = attempt
        if attempt == 1 and first_draft is not None:
            draft = first_draft
        else:
            draft = step("writer", writer.run, state, issues, previous, summary=lambda d: f"{len(d.split())} words")
        state.drafts.append(draft)
        handoff("writer", "reviewer", "drafts")
        review = step("reviewer", reviewer.run, state, draft, summary=lambda r: f"{r.verdict}, {len(r.issues)} issues")
        state.reviews.append(review)
        trace.log("reviewer", "review_verdict", attempt=attempt, verdict=review.verdict, issues=[i.model_dump() for i in review.issues])
        if review.verdict == "approve":
            return True
        if attempt == MAX_RETRIES + 1:
            break
        issues, previous = review.issues, draft
        trace.log("orchestrator", "retry", attempt=attempt, next_attempt=attempt + 1, issue_count=len(issues))
        handoff("reviewer", "writer", "reviews")
    return False
