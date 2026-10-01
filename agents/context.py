"""Context agent: code proposes (finding, note) candidates; one LLM call confirms them and writes follow-ups."""
import re

from pydantic import BaseModel

import llm
import tools
import trace
from state import Attachment, Finding, FollowUp, NoteMeta, RunState


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
- compare_to_target: leave follow_ups empty (code adds one check_target per target).
- do_not_judge_on_metric: none. The caveat itself is the output; do not invent a workaround.
- context_only: none.
"N weeks" in a note means N reporting records (rows of the data), not calendar weeks; week_start dates are irregular.
Use ONLY these tools:
{tools}
Week numbers and their dates: {weeks}. Map the note's dates to week numbers with that table.
Use channel names exactly as given ({channels}, or "blended"). Exclusions are [channel, week] pairs.
Each follow-up's `why` says which number it produces and why the note needs it. Do not compute numbers yourself."""


def _overlap(f: Finding, n: NoteMeta) -> bool:
    """Do the finding's weeks overlap the note's date window? (None dates are open-ended.)"""
    lo, hi = n.date_from or "0000", n.date_to or "9999"
    return any(tools.week_window(w)[0] <= hi and tools.week_window(w)[1] >= lo for w in f.weeks)


def _ints(v):
    """LLMs send weeks as 12, "7, 8, 9" or [7, 8, 9]; return a list of ints."""
    if isinstance(v, (int, str)):
        v = re.findall(r"\d+", str(v))
    return [int(x) for x in v]


def _coerce(args: dict) -> dict:
    """Repair argument types the tools require (lists of ints, [channel, week] pairs, numbers)."""
    out = dict(args)
    for key in ("weeks", "baseline_weeks"):
        if key in out:
            out[key] = _ints(out[key])
    if "week" in out:
        out["week"] = _ints(out["week"])[0]
    if "threshold" in out:
        out["threshold"] = float(out["threshold"])
    if isinstance(out.get("exclude"), str):  # "google_search, 6" -> [["google_search", 6]]
        out["exclude"] = [[c, int(w)] for c, w in re.findall(r"([a-z_]+)\W+(\d+)", out["exclude"])]
    return out


def affected_records(note: NoteMeta) -> list[list]:
    """[channel, week] pairs whose 7-day window overlaps the note's dates (the weekly data can't isolate the days)."""
    chans = tools.CHANNELS if "blended" in note.channels else note.channels
    lo, hi = note.date_from or "0000", note.date_to or "9999"
    return [[c, w] for c in chans for w in tools.WEEKS if tools.week_window(w)[0] <= hi and tools.week_window(w)[1] >= lo]


def data_end(as_of: int) -> str:
    return tools.week_window(as_of)[1]


def open_targets(note: NoteMeta, as_of: int) -> list:
    """Targets due after the latest record ends: the latest week is not their final measurement."""
    return [t for t in note.targets if t.by and t.by > data_end(as_of)]


def _target_attachments(state: RunState, attachments: list[Attachment], emit) -> list[Attachment]:
    """Every target in a target note gets its own check_target follow-up, built in code so none can be forgotten."""
    for n in state.notes:
        if not n.targets:
            continue
        by_id = {f.id: f for f in state.findings}
        mine = [a for a in attachments if a.note_id == n.note_id and by_id[a.finding_id].channel in {t.channel for t in n.targets}]
        attachments[:] = [a for a in attachments if a.note_id != n.note_id or a in mine]  # a target only bears on findings of its own channel
        if not mine:  # the LLM confirmed no pair: attach to a blended finding so the targets are still checked
            f = next((f for f in state.findings if f.channel == "blended"), state.findings[0])
            mine = [Attachment(finding_id=f.id, note_id=n.note_id, implication="compare_to_target",
                               relevance="The note sets numeric targets that must be checked against the latest week.")]
            attachments += mine
        late = open_targets(n, state.as_of_week)
        for a in mine:
            a.implication = "compare_to_target"
            a.follow_ups = [FollowUp(tool="check_target", why=f"target {t.metric} {t.op} {t.threshold} from {n.note_id}",
                                     args={"metric": t.metric, "week": state.as_of_week, "op": t.op, "threshold": t.threshold, "channel": t.channel})
                            for t in n.targets]
            a.follow_ups = [fu for fu in a.follow_ups if "error" not in tools.check_target(**fu.args)]
            if late:
                a.relevance += (f" The latest record ends {data_end(state.as_of_week)}, but target(s) are due {sorted({t.by for t in late})}:"
                                " the target period is incomplete and the latest week is not the final measurement.")
        emit("target_followups", note_id=n.note_id, targets=[t.model_dump() for t in n.targets], incomplete=[t.model_dump() for t in late])
    return attachments


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
        note = notes[a.note_id]
        if a.implication in ("context_only", "do_not_judge_on_metric"):
            a.follow_ups = []  # the caveat is the output; no workaround numbers
        bad = affected_records(note) if a.implication == "treat_as_invalid" else []
        if bad:
            chans, wks = sorted({c for c, _ in bad}), sorted({w for _, w in bad})
            a.relevance += (f" The note's dates ({note.date_from} to {note.date_to}) overlap the weekly records for weeks {', '.join(map(str, wks))}"
                            f" ({', '.join(chans)}); the weekly data cannot isolate the affected days, so every one of those records is uncertain.")
        kept = []
        for fu in a.follow_ups:
            if fu.tool not in tools.TOOLS:
                emit("followup_rejected", finding_id=a.finding_id, tool=fu.tool, reason="unknown tool")
                continue
            try:
                fu.args = _coerce(fu.args)
                if bad and "exclude" in tools.TOOLS[fu.tool]["parameters"]["properties"]:
                    fu.args["exclude"] = [e for e in bad if fu.args.get("channel", e[0]) in ("blended", e[0])]
                check = tools.TOOLS[fu.tool]["fn"](**fu.args)  # dry run: reject requests the tool cannot answer
            except (TypeError, ValueError, KeyError) as e:
                check = {"error": f"{type(e).__name__}: {e}"}
            if "error" in check:
                emit("followup_rejected", finding_id=a.finding_id, tool=fu.tool, args=fu.args, reason=check["error"])
            else:
                kept.append(fu)
        a.follow_ups = kept
        attachments.append(a)
    attachments = _target_attachments(state, attachments, emit)
    for a in attachments:
        emit("attachment", **a.model_dump())
    return attachments
