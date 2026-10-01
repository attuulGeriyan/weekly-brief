# SPEC: Weekly brief multi-agent system

## Goal
Read `data/channel_weekly.csv` (12 weeks × 4 channels) and `notes/*.md` (4 short team notes). Produce `brief.md`, a 200–400 word Markdown brief for a head of marketing about the **most recent week, in the context of the full period**. Also produce `trace.jsonl` and `trace.md`.

The design idea: **numbers and notes are joined in code, not left to the writer's prose.** The analyst first finds what's unusual in the numbers without seeing the notes. The context agent attaches the right note to each finding and states what the note requires to be computed next (follow-ups). The analyst then computes those follow-ups. The writer only uses findings and notes, and cites them. The reviewer recomputes the cited numbers and rejects the draft if anything is wrong.

## Control flow (hand-written orchestrator in `main.py`)

```
index_notes → analyst(pass 1: raw) → context → analyst(pass 2: follow-ups) → writer → reviewer
                                                                              ↑          │ reject (issues)
                                                                              └──────────┘ max 2 retries
                                                                                         │ approve / retries exhausted
                                                                                         ▼
                                                                         brief.md, trace.jsonl, trace.md
```
- If retries run out, the last draft is still written, with a top line: `> ⚠ Reviewer did not approve: <issues>`. Never fail silently.
- Model: env `MODEL` (Gemini, via `openai` SDK + `GEMINI_API_KEY`), temperature 0; provider code isolated in `llm.py`.
- CLI: `python main.py [--as-of-week 12] [--inject-error] [--model ...]`
- `--inject-error`: after the first writer draft, the orchestrator multiplies the first `[F#]`-cited number by 1.3 and logs a `fault_injected` trace event. This proves the review loop rejects bad drafts and recovers. Use it for the submitted trace, and say so in the README.

## Shared state (`state.py`, pydantic)

```python
class NoteMeta(BaseModel):          # built by index_notes (LLM extraction, once per note)
    note_id: str                    # filename stem, e.g. "note_01_tracking"
    text: str                       # verbatim note
    channels: list[str]             # values from the CSV's channel column, or ["blended"] for whole-business notes
    date_from: str | None           # ISO date the note applies from
    date_to: str | None             # ISO date it applies to (None = ongoing)
    kind: Literal["data_quality", "experiment", "campaign_change", "target"]
    rule: str | None                # machine-readable rule if the note contains one, e.g. "cut if cpa(tiktok)/cpa(meta) > 1.5 over test period"

class Evidence(BaseModel):
    tool: str; args: dict; result: dict      # exact call, so the reviewer can re-run it

class Finding(BaseModel):
    id: str                          # "F1", "F2", … unique across both passes
    pass_no: Literal[1, 2]
    kind: Literal["anomaly", "trend", "test", "target", "summary"]
    channel: str                     # a channel name or "blended"
    weeks: list[int]
    metric: str                      # spend_usd | conversions | revenue_usd | cpa | roas | ratio | …
    value: float
    baseline: float | None = None
    change_pct: float | None = None
    statement: str                   # one factual sentence, numbers only, no interpretation
    evidence: Evidence
    derived_from: str | None = None  # pass-2 findings point at the pass-1 finding or note they answer

class FollowUp(BaseModel):
    tool: str; args: dict; why: str

class Attachment(BaseModel):
    finding_id: str
    note_id: str
    relevance: str                   # why this note explains this finding
    implication: Literal["treat_as_invalid", "evaluate_rule", "do_not_judge_on_metric", "compare_to_target", "context_only"]
    follow_ups: list[FollowUp] = []

class Issue(BaseModel):
    kind: Literal["number_mismatch", "uncited_number", "missing_note_caveat", "rule_outcome_mismatch", "length"]
    detail: str; expected: str | None = None; got: str | None = None

class Review(BaseModel):
    verdict: Literal["approve", "reject"]; issues: list[Issue] = []

class RunState(BaseModel):
    run_id: str; as_of_week: int; model: str
    notes: list[NoteMeta] = []
    findings: list[Finding] = []            # pass 1 and pass 2
    attachments: list[Attachment] = []
    drafts: list[str] = []                  # every draft, with [F#]/[N#] citations
    reviews: list[Review] = []
    attempts: int = 0
    brief: str | None = None                # final, citations rendered
```
State is the only thing passed between agents. Each agent gets the state (or a slice of it) and returns typed output that the orchestrator merges in. **Don't** pass free-text blobs between agents.

Why (for the README):
- Findings have IDs, so notes and claims can point at them.
- `evidence` stores the exact tool call, so the reviewer can re-run it.
- `follow_ups` make the context agent's output drive further computation instead of being text to read.
- Typed state makes the trace readable.

## Tools (`tools.py`, pure pandas, deterministic, return dicts)
Derived metrics: `cpa = spend/conversions`, `roas = revenue/spend`, `ctr = clicks/impressions`, `cvr = conversions/clicks`. "Blended" means summed across all channels and then divided (not an average of ratios). Round to 2 dp in results.

- `get_weekly(channels: list[str] | None, weeks: list[int] | None, metrics: list[str])` returns rows.
- `compute_kpi(channel: str, weeks: list[int], exclude: list[[channel, week]] = [])` returns `{spend, conversions, revenue, cpa, roas, weeks_used, excluded}`. `channel` can be `"blended"`.
- `scan_changes(metrics: list[str] = ["spend_usd","conversions","cpa"], z: float = 2.0)` flags per-channel weeks where a metric deviates from that channel's median by more than z × MAD, and sustained level shifts (mean of a later block vs earlier block > 20%). Returns candidates `{channel, week(s), metric, value, baseline, change_pct, type}`. This is a deterministic "where to look" list for the analyst.
- `compare_channels(channel_a, channel_b, weeks, metric="cpa", exclude=[])` returns `{a, b, ratio}`.
- `check_target(metric: "cpa"|"revenue_usd", week: int, op: "<"|">", threshold: float, channel="blended", exclude=[])` returns `{value, threshold, met}`.

Every tool validates its inputs (unknown channel or week → `{"error": ..., "valid": [...]}`) and never raises into the LLM loop.

## Agents (`agents/*.py`). Each is one function: `run(state) -> typed output`

**index_notes** (LLM, structured output): for each note file, extract `NoteMeta`. Give it the list of valid channel names and the data's date range so it maps "Google Ads" to `google_search`, "the week of Aug 15" to a date, and so on.

**analyst**: an LLM with the tools above, using an OpenAI-format function-calling loop (Gemini via its OpenAI-compatible endpoint) of at most 8 steps.
- *Pass 1*: it sees only the data schema, `as_of_week`, and `scan_changes` output on request. **It does not see the notes.** Goal: 5–8 findings covering the latest week vs the period, notable anomalies, trend breaks, and blended totals. It returns `list[Finding]` via a final `submit_findings` tool call. Each finding's `evidence` is taken from an actual tool call made in the loop. The harness fills `evidence` from the recorded call, so the model can't invent it.
- *Pass 2*: it gets the attachments' `follow_ups`. It runs each one (it may adjust args, e.g. a fuller week range) and submits pass-2 findings with `derived_from` set. This is where "exclude the broken week", "evaluate the test rule" and "check targets" become numbers.

**context**: candidate matching in **code** by (channel overlap, or "blended") AND (finding weeks overlap the note's date window, mapping weeks to dates via `week_start`), plus always offering `target` notes for blended/summary findings. Then **one LLM call** confirms or rejects each candidate pair, picks `implication`, and writes `follow_ups` using only the tool names above. It returns `list[Attachment]`. A finding may have 0..n notes, and a note may attach to several findings. Log the candidates and the confirmed matches separately in the trace.

**writer** (LLM): gets the findings (both passes), the attachments and the note texts, but no raw CSV. It writes 200–400 words with these sections:
1. **Headline** (2 sentences)
2. **Last week vs the period**
3. **What the numbers mean** (each anomaly with its note)
4. **Decisions needed** (e.g. test rules that are due)
5. **Recommended actions** (3 bullets max)
6. **Data caveats**

Every number must carry a citation `[F#]`, or `[N#]` for numbers that come from a note (targets, thresholds). It must not compute new numbers. On a retry it gets the reviewer's issues and must fix exactly those.

**reviewer** (code first, then rules; no LLM required):
1. *Number check*: parse each number with its citation. For `[F#]`, the number must match that finding's `value`, `baseline` or `change_pct` within the displayed rounding (handle `$`, `%`, `k`, `x`). Re-run the finding's `evidence` call and confirm the stored value is unchanged. For `[N#]`, the number must appear in that note's text.
2. *Uncited numbers*: any number without a citation is an issue. Ignore week numbers, dates, years and section numbering.
3. *Note caveats*: for every attachment with implication `treat_as_invalid` or `do_not_judge_on_metric`, if the draft cites that finding, the same paragraph must also cite the attachment's note.
4. *Rule outcomes*: for every pass-2 finding derived from an `evaluate_rule` attachment, the draft's recommendation must agree with it (e.g. if the ratio exceeds the rule's threshold, the draft must not say "keep/scale"). Implement this generically from the `rule` and the finding's `value`, not with channel names.
5. *Length*: 150–450 words.

It returns `Review`. Any issue means `reject`.

## Trace
`trace.py` exposes `log(agent, event, **payload)`, appending one JSON line to `trace.jsonl`:
```json
{"seq": 12, "ts": "...", "agent": "analyst", "event": "tool_call", "pass": 1, "tool": "compute_kpi", "args": {...}}
```
Events: `agent_start`, `agent_end` (with a short summary of output IDs), `llm_call` (model, tokens in/out, stop reason; not full prompts), `tool_call`, `tool_result` (truncated to ~500 chars), `handoff` (from, to, what state fields changed), `candidates`, `attachment`, `fault_injected`, `review_verdict`, `retry`, `done`.

`render_trace.py` turns `trace.jsonl` into `trace.md`:
- one section per agent step, in order
- tool calls as compact bullet lines
- the review verdict and issues
- a final summary table: findings → notes → where cited in the brief

A reader must be able to follow the run without executing the code.

## Final rendering
`brief.md` is the approved draft with citations rendered:
- `[F#]` tags are removed from the prose.
- `[N#]` tags become `(note_0X)`.
- A small footer lists `Sources: note_01_tracking, …` for the notes that were used.

The fully cited draft stays in `RunState.drafts` and in the trace.
