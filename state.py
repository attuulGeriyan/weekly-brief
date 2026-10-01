"""Shared run state. Agents only talk to each other through these typed objects."""
from typing import Literal
from pydantic import BaseModel


class NoteMeta(BaseModel):  # built by index_notes (LLM extraction, once per note)
    note_id: str  # filename stem, e.g. "note_01_tracking"
    text: str  # verbatim note
    channels: list[str]  # channel values from the CSV, or ["blended"]
    date_from: str | None  # ISO date the note applies from
    date_to: str | None  # ISO date it applies to (None = ongoing)
    kind: Literal["data_quality", "experiment", "campaign_change", "target"]
    rule: str | None  # machine-readable rule, if the note contains one


class Evidence(BaseModel):
    tool: str
    args: dict
    result: dict  # exact call, so the reviewer can re-run it


class Finding(BaseModel):
    id: str  # "F1", "F2", ... unique across both passes
    pass_no: Literal[1, 2]
    kind: Literal["anomaly", "trend", "test", "target", "summary"]
    channel: str  # a channel name or "blended"
    weeks: list[int]
    metric: str  # spend_usd | conversions | revenue_usd | cpa | roas | ratio | ...
    value: float
    baseline: float | None = None
    change_pct: float | None = None
    statement: str  # one factual sentence, numbers only, no interpretation
    evidence: Evidence
    derived_from: str | None = None  # pass-2 finding or note this one answers


class FollowUp(BaseModel):
    tool: str
    args: dict
    why: str


class Attachment(BaseModel):
    finding_id: str
    note_id: str
    relevance: str  # why this note explains this finding
    implication: Literal[
        "treat_as_invalid", "evaluate_rule", "do_not_judge_on_metric", "compare_to_target", "context_only"
    ]
    follow_ups: list[FollowUp] = []


class Issue(BaseModel):
    kind: Literal["number_mismatch", "uncited_number", "missing_note_caveat", "rule_outcome_mismatch", "length"]
    detail: str
    expected: str | None = None
    got: str | None = None


class Review(BaseModel):
    verdict: Literal["approve", "reject"]
    issues: list[Issue] = []


class RunState(BaseModel):
    run_id: str
    as_of_week: int
    model: str
    notes: list[NoteMeta] = []
    findings: list[Finding] = []  # pass 1 and pass 2
    attachments: list[Attachment] = []
    drafts: list[str] = []  # every draft, with [F#]/[N#] citations
    reviews: list[Review] = []
    attempts: int = 0
    brief: str | None = None  # final, citations rendered
