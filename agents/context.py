"""Context agent: code proposes (finding, note) candidates; one LLM call confirms them and writes follow-ups."""
from pydantic import BaseModel

import llm
import tools
import trace
from state import Attachment, Finding, NoteMeta, RunState


class ContextOut(BaseModel):
    attachments: list[Attachment]


SYSTEM = """You connect numeric findings to the team notes that explain or constrain them.
You get candidate (finding, note) pairs. Confirm only pairs where the note directly bears on that finding's own channel and
metric; skip loosely related pairs.
For each confirmed pair return an attachment: finding_id, note_id, relevance (why), implication, follow_ups.
implication is one of:
- treat_as_invalid: the note says the data for that channel and period is wrong.
- evaluate_rule: the note contains a decision rule that must now be evaluated with numbers.
- do_not_judge_on_metric: the note says the finding's metric is not a fair way to judge this activity.
- compare_to_target: the note sets a target the numbers should be compared with.
- context_only: relevant background, nothing further to compute.
follow_ups are tool calls that compute what the note requires. Rules:
- treat_as_invalid: recompute the finding's metric with the invalid channel-weeks left out (use the exclude argument).
- evaluate_rule: compute the quantity the rule compares, over the weeks the note's window covers.
- compare_to_target: one check_target call per distinct target in the note (each metric separately), for the latest week.
- do_not_judge_on_metric: a call that gives a fairer view, e.g. the same period with the affected channel-weeks excluded; otherwise none.
- context_only: none.
Use ONLY these tools:
{tools}
Week numbers and their dates: {weeks}. Map the note's dates to week numbers with that table.
Use channel names exactly as given ({channels}, or "blended"). Exclusions are [channel, week] pairs.
Each follow-up's `why` says which number it produces and why the note needs it. Do not compute numbers yourself."""


def _overlap(f: Finding, n: NoteMeta) -> bool:
    """Do the finding's weeks overlap the note's date window? (None dates are open-ended.)"""
    lo, hi = n.date_from or "0000", n.date_to or "9999"
    return any(tools.week_window(w)[0] <= hi and tools.week_window(w)[1] >= lo for w in f.weeks)


def candidates(state: RunState) -> list[tuple[str, str]]:
    out = []
    for f in state.findings:
        for n in state.notes:
            chan = f.channel == "blended" or "blended" in n.channels or f.channel in n.channels
            wide_target = n.kind == "target" and (f.channel == "blended" or f.kind == "summary")
            if (chan and _overlap(f, n)) or wide_target:
                out.append((f.id, n.note_id))
    return out


def run(state: RunState) -> list[Attachment]:
    emit = lambda event, **p: trace.log("context", event, **p)
    pairs = candidates(state)
    emit("candidates", pairs=[f"{f}->{n}" for f, n in pairs])
    if not pairs:
        return []
    findings = {f.id: f for f in state.findings}
    notes = {n.note_id: n for n in state.notes}
    listing = "\n\n".join(
        f"PAIR {f_id} <-> {n_id}\n finding: [{findings[f_id].channel}, weeks {findings[f_id].weeks}] {findings[f_id].statement}\n"
        f" note ({notes[n_id].kind}, rule={notes[n_id].rule}): {notes[n_id].text}" for f_id, n_id in pairs)
    system = SYSTEM.format(
        tools="\n".join(f"- {n}: {t['description']} args={list(t['parameters']['properties'])}" for n, t in tools.TOOLS.items()),
        weeks="; ".join(f"{w}: {tools.week_window(w)[0]} to {tools.week_window(w)[1]}" for w in tools.WEEKS),
        channels=tools.CHANNELS)
    out = llm.structured(system, listing, ContextOut, on_event=emit)
    attachments = []
    for a in out.attachments:
        if (a.finding_id, a.note_id) not in pairs:
            emit("warning", detail=f"dropped non-candidate pair {a.finding_id}->{a.note_id}")
            continue
        a.follow_ups = [fu for fu in a.follow_ups if fu.tool in tools.TOOLS]
        emit("attachment", **a.model_dump())
        attachments.append(a)
    return attachments
