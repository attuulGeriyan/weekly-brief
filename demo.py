"""INVALID-DRAFT DEMONSTRATION (`python main.py --inject-error`). Separate from the genuine run.
Takes the genuine approved draft saved in state.json, corrupts it one dimension at a time, shows the reviewer reject each,
then shows one corrupted draft going through the real reject -> retry (writer) -> approve loop.
Writes only to demo_invalid_draft/ (trace.jsonl, trace.json, trace.md, summary.md, brief_after_retry.md); nothing here touches the genuine files."""
import os
import re

import agents.reviewer as reviewer
import agents.writer as writer
import citations as cz
import render_trace
import tools
import trace
from loop import write_and_review
from state import RunState

DIR = "demo_invalid_draft"
BANNER = "INVALID-DRAFT DEMONSTRATION: deliberately corrupted copies of the genuine draft. This is NOT the genuine run."
SWAPS = {"higher": "lower", "lower": "higher", "increased": "decreased", "above": "below", "exceeding": "trailing", "up": "down"}
METRIC_SWAPS = {"CPA": "ROAS", "ROAS": "CPA", "spend_usd": "revenue_usd", "revenue": "spend", "spend": "revenue", "conversions": "revenue"}


def _cited(draft: str) -> list:
    return [t for t in cz.numbers(draft) if t.cites and t.cites[0].startswith("F") and not t.weekish]


def _replace(draft: str, a: int, b: int, new: str) -> str:
    return draft[:a] + new + draft[b:]


def fault_value(draft, state):  # first cited number x 1.3, same display style
    t = _cited(draft)[0]
    new = f"{t.value / cz.SCALE.get(t.unit, 1) * 1.3:,.{t.dec}f}"
    return _replace(draft, t.start, t.end, ("$" if t.dollar else "") + new + t.text[len(t.text) - len(t.unit):] * bool(t.unit))


def fault_unit(draft, state):  # drop the $ from a money value
    t = next((t for t in _cited(draft) if t.dollar), None)
    return t and _replace(draft, t.start, t.start + 1, "")


def fault_channel(draft, state):  # name a different channel in a channel-specific sentence
    refs = {f.id: f for f in state.findings}
    for t in _cited(draft):
        ch = refs[t.cites[0]].channel
        a, b = cz.sentence_at(draft, t.start)
        alias = ch.replace("_", " ")
        m = re.search(alias, draft[a:b], re.I) or re.search(alias.split()[0], draft[a:b], re.I)
        if ch != "blended" and m:
            other = next(c for c in tools.CHANNELS if c != ch).replace("_", " ").title()
            return _replace(draft, a + m.start(), a + m.end(), other)


def fault_period(draft, state):  # move a week reference back by one
    for t in _cited(draft):
        a, b = cz.sentence_at(draft, t.start)
        m = re.search(r"[Ww]eeks?\s+(?:\d+\s*[-–—to]*\s*)*?(\d+)(?!\d)", draft[a:b])
        if m and "[N" not in draft[a:b]:
            return _replace(draft, a + m.start(1), a + m.end(1), str(int(m.group(1)) - 1))


def fault_direction(draft, state):  # flip higher/lower etc. next to a cited number
    for t in _cited(draft):
        a, b = cz.sentence_at(draft, t.start)
        m = re.search(r"\b(" + "|".join(SWAPS) + r")\b", draft[a:b])
        if m:
            return _replace(draft, a + m.start(), a + m.end(), SWAPS[m.group(1)])


def fault_metric(draft, state):  # name a different metric
    for t in _cited(draft):
        a, b = cz.sentence_at(draft, t.start)
        m = re.search(r"\b(" + "|".join(METRIC_SWAPS) + r")\b", draft[a:b])
        if m:
            return _replace(draft, a + m.start(), a + m.end(), METRIC_SWAPS[m.group(1)])


FAULTS = [("value (x1.3)", "[value]", fault_value), ("unit ($ removed)", "[unit]", fault_unit),
          ("channel (wrong name)", "[channel]", fault_channel), ("period (week moved)", "[period]", fault_period),
          ("direction (higher<->lower)", "[direction]", fault_direction), ("metric (CPA<->ROAS etc.)", "[metric]", fault_metric)]


def _diff(a: str, b: str) -> str:
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), 0)
    return f'"{a[max(0, i - 25):i + 25].strip()}" -> "{b[max(0, i - 25):i + 25].strip()}"'.replace("\n", " ")


def run():
    if not os.path.exists("state.json"):
        raise SystemExit("state.json not found: run the genuine pipeline first (python main.py)")
    genuine = RunState.model_validate_json(open("state.json").read())
    if not genuine.reviews or genuine.reviews[-1].verdict != "approve":
        raise SystemExit("the saved genuine run was not approved, so there is no valid draft to corrupt: rerun python main.py")
    good = genuine.drafts[-1]
    os.makedirs(DIR, exist_ok=True)
    trace.start(f"{DIR}/trace.jsonl", demo=BANNER, run_id=f"demo-of-{genuine.run_id}")
    trace.log("demo", "demo_banner", detail=BANNER, source="draft taken from state.json (genuine run " + genuine.run_id + ")")
    lines = [f"# {BANNER}", "", f"Source: approved draft of genuine run `{genuine.run_id}`. Each row corrupts ONE thing in that draft.", "",
             "| corruption | change | verdict | caught as |", "|---|---|---|---|"]
    control = reviewer.run(genuine, good)
    trace.log("reviewer", "review_verdict", fault="none (control: unmodified genuine draft)", verdict=control.verdict, issues=[i.detail for i in control.issues])
    lines.append(f"| none (control) | unmodified genuine draft | {control.verdict} | {len(control.issues)} issues |")
    first_bad = None
    for name, tag, fn in FAULTS:
        bad = fn(good, genuine)
        if bad is None:
            lines.append(f"| {name} | could not be applied to this draft | - | - |")
            continue
        review = reviewer.run(genuine, bad)
        trace.log("demo", "fault_injected", fault=name, change=_diff(good, bad))
        trace.log("demo", "invalid_draft", fault=name, text=bad)  # the full corrupted draft that is about to be reviewed
        trace.log("reviewer", "review_verdict", fault=name, verdict=review.verdict, issues=[i.detail for i in review.issues])
        caught = [i.detail[:90] for i in review.issues if tag in i.detail] or [f"NOT caught as {tag}: " + "; ".join(i.kind for i in review.issues)]
        lines.append(f"| {name} | {_diff(good, bad)} | {review.verdict} | {caught[0]} |")
        first_bad = first_bad or (name, bad)
    lines += ["", "## Retry demonstration", ""]
    if first_bad:
        name, bad = first_bad
        state = genuine.model_copy(update={"drafts": [], "reviews": [], "attempts": 0, "brief": None})
        trace.log("demo", "fault_injected", fault=name, change=_diff(good, bad), note="this corrupted draft is used as writer draft #1 below")
        ok = write_and_review(state, first_draft=bad)
        trace.log("orchestrator", "done", status="approved" if ok else "not_approved", attempts=state.attempts)
        verdicts = " -> ".join(r.verdict for r in state.reviews)
        lines.append(f"Corrupted draft #1 ({name}) -> reviewer verdicts: {verdicts} (attempts: {state.attempts}).")
        open(f"{DIR}/brief_after_retry.md", "w").write(f"> {BANNER}\n\n" + writer.render(state.drafts[-1], state) + "\n")
    open(f"{DIR}/summary.md", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\n".join(("OK   " if ok else "FAIL ") + m for ok, m in render_trace.write_all(DIR)))
