"""Writer (LLM): turns findings + attached notes into the brief. It never sees the CSV and never computes numbers."""
import re

from pydantic import BaseModel

import facts
import llm
import trace
from state import Issue, RunState


class Draft(BaseModel):
    markdown: str


SYSTEM = """You write the weekly marketing brief for a head of marketing from verified findings and team notes.
You never calculate, round further, or invent numbers. A code checker will verify every number against the data.

Length: aim for 300 words; HARD LIMIT 380 words in total (a checker rejects more than 400 words). Spend the words on decisions:
leave out small findings that change no decision, and do not repeat a baseline when the percentage change already conveys it. Be terse and pick only the findings that matter for decisions; do not cover every finding.
Format: Markdown, exactly these sections as "## " headings, in order:
Headline (2 sentences) / Last week vs the period / What the numbers mean (each anomaly with its note) /
Decisions needed / Recommended actions (max 3 bullets, each on its own line starting with "- ") / Data caveats

Citation rules:
- Each finding lists ready-made display strings (value=..., baseline=..., change=...). Use them verbatim.
- EVERY number gets its own tag right after it, including each percentage and each baseline:
  "CPA was $79.97 [F3], 12.7% higher [F3] than the $70.94 [F3] baseline". Numbers quoted from a note (targets,
  thresholds) take that note's tag: "$65 [N4]". No other numbers, except week numbers ("week 12", "weeks 7-12"; write a span as 7-12, never as a comma list) and dates.
  Do not write counts as digits ("three actions" is fine, "3 actions" is not).
- Write units the way the metric needs: $ for spend, revenue and CPA; x for ratios and ROAS ("1.92x [F9]", "0.78x [F3]");
  % for a change_pct, written as a positive size with a direction word ("12.7% higher [F3]").
- For spend, conversions and revenue, the finding shows how the number was aggregated in [brackets]. Say it in the sentence:
  "averaged $4,788 per week", "a weekly average of 107", "a total of $28,727", "in week 12". NEVER present a weekly
  average as a total for the period.
- CPA and ROAS are period figures (total spend / total conversions), so never say they were "averaged".
- In the SAME sentence as each cited number, name the channel (or "blended"), the week(s) it covers, and the metric,
  and use direction words (higher/lower, up/down) that match the data.
- When a finding has a note attachment with implication treat_as_invalid or do_not_judge_on_metric, cite that note's tag in
  the same bullet or paragraph and say what it means for reading the number. For treat_as_invalid, name every affected
  record week listed in the attachment and say the weekly data cannot isolate the affected days. Never call a number
  computed with excluded records "corrected" or "true"; say "excluding records ...".
- For a decision-rule finding, state the outcome the rule implies against the threshold from the note, and make the
  recommendation follow it. Report EVERY target finding: the latest-week value, the target (note tag) and whether it was met. If an attachment says the
  target period is incomplete, say so ("the month is incomplete: data ends ...").
- Use "latest week" only for week {as_of}. Each paragraph or bullet on its own line.
- "Last week vs the period": at most TWO sentences on blended results (the headline movers, e.g. CPA and revenue).
- Do not explain causes that no note supports."""


def _fmt(metric: str, v: float) -> str:
    """Deterministic display of a tool number (formatting only, no arithmetic)."""
    n = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return {"spend_usd": "$" + n, "revenue_usd": "$" + n, "cpa": "$" + n, "roas": n + "x", "ratio": n + "x"}.get(metric, n)


def _span(weeks: list[int]) -> str:
    """[7, 8, 9, 10, 11, 12] -> "7-12" (consecutive weeks written as a span)."""
    return f"{weeks[0]}-{weeks[-1]}" if len(weeks) > 1 and weeks == list(range(weeks[0], weeks[-1] + 1)) else ", ".join(map(str, weeks))


def _display(f) -> str:
    """value/baseline/change with ready-made strings; for spend, conversions and revenue also how they were aggregated."""
    tag = lambda agg: f" [{facts.AGG_LABEL[agg]}]" if f.metric in facts.ADDITIVE and agg in facts.AGG_LABEL else ""
    parts = [f"value={_fmt(f.metric, f.value)}{tag(f.aggregation)}"]
    if f.baseline is not None:
        parts.append(f"baseline={_fmt(f.metric, f.baseline)}{tag(f.baseline_aggregation)}")
    if f.change_pct is not None:
        parts.append(f"change={abs(f.change_pct):g}% {'higher' if f.change_pct > 0 else 'lower'}")
    return ", ".join(parts)


def _listing(state: RunState) -> str:
    notes = "\n".join(f"[N{i + 1}] {n.note_id} ({n.kind}): {n.text}" for i, n in enumerate(state.notes))
    ids = {n.note_id: f"N{i + 1}" for i, n in enumerate(state.notes)}
    findings = "\n".join(
        f"[{f.id}] pass{f.pass_no} {f.kind} channel={f.channel.replace('_', ' ')} weeks={_span(f.weeks)} metric={f.metric}: {_display(f)}"
        + (f" derived_from={f.derived_from}" if f.derived_from else "")
        + f"\n      {f.statement}" for f in state.findings)
    atts = "\n".join(f"{a.finding_id} <-> [{ids[a.note_id]}] {a.implication}: {a.relevance}" for a in state.attachments)
    return f"NOTES\n{notes}\n\nFINDINGS (cite as [F#])\n{findings}\n\nATTACHMENTS (finding <-> note)\n{atts}"


def run(state: RunState, issues: list[Issue] | None = None, previous: str | None = None) -> str:
    user = f"Latest week: {state.as_of_week}\n\n{_listing(state)}"
    if issues:  # retry: fix exactly what the reviewer found
        user += ("\n\nYOUR PREVIOUS DRAFT WAS REJECTED.\nPrevious draft:\n" + (previous or "") + "\n\nReviewer issues (fix exactly these, "
                 "keep everything else):\n" + "\n".join(f"- {i.kind}: {i.detail}" + (f" (expected {i.expected})" if i.expected else "") for i in issues))
    emit = lambda event, **p: trace.log("writer", event, **p)
    return llm.structured(SYSTEM.format(as_of=state.as_of_week), user, Draft, on_event=emit).markdown.strip()


def render(draft: str, state: RunState) -> str:
    """Final brief: [F#] tags removed, [N#] become (note_id), plus a Sources footer."""
    names = {f"N{i + 1}": n.note_id for i, n in enumerate(state.notes)}
    used = []

    def sub(m):
        notes = [names[i] for i in re.findall(r"N\d+", m.group(1)) if i in names]
        used.extend(n for n in notes if n not in used)
        return f" ({', '.join(notes)})" if notes else ""

    text = re.sub(r"[ \t]*\[([FN]\d+(?:\s*,\s*[FN]\d+)*)\]", sub, draft)
    return text + (f"\n\n---\nSources: {', '.join(used)}" if used else "")
