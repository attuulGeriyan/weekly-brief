# Weekly marketing brief: multi-agent system

Reads `data/channel_weekly.csv` (12 weeks x 4 channels) and four team notes in `notes/`, and writes a ~370-word brief for the head of marketing (`brief.md`) plus a full trace of how it got there. Plain Python, no agent framework. The LLM is Gemini (`gemini-2.5-flash`) through its OpenAI-compatible endpoint; all provider code is in `llm.py` (see `DECISIONS.md`: the original spec assumed the Anthropic SDK, but the key provided was Gemini).

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
printf 'GEMINI_API_KEY=...\nMODEL=gemini-2.5-flash\n' > .env        # .env is git-ignored
.venv/bin/python main.py                  # genuine run -> brief.md, trace.md, trace.json, trace.jsonl, state.json
.venv/bin/python main.py --inject-error   # SEPARATE demo -> demo_invalid_draft/ (never touches the files above)
.venv/bin/python render_trace.py [dir]    # re-render trace.md / trace.json and re-check the run
# options: --as-of-week N, --model NAME, --stop-after analyst1|analyst2|writer
```

The committed `trace.md` is one genuine run: three writer attempts, two of them rejected by the reviewer for real reasons. `demo_invalid_draft/` corrupts the approved draft one dimension at a time (value, unit, channel, period, direction, metric), shows the reviewer reject each, then shows one corrupted draft go reject -> retry -> approve. It is labelled as a demonstration on every row.

## Design

```
index_notes -> analyst (pass 1: numbers only) -> context -> analyst (pass 2: follow-ups) -> writer -> reviewer
                                                                                              ^          | reject (issues), max 2 retries
                                                                                              +----------+
                                                                                  approve, or retries exhausted (brief carries a warning line)
```

`main.py` / `loop.py` are the hand-written orchestrator; agents only exchange the typed `RunState` (`state.py`, pydantic).

- **index_notes** (LLM, structured output): each note becomes `NoteMeta`: channels, date window, kind, any decision rule, and a list of numeric targets.
- **analyst** (LLM + real pandas tools): pass 1 never sees the notes; it finds what is unusual in the numbers. The harness records every tool call, and a finding is accepted only if the cited call actually supports its channel, weeks, metric, value, baseline and direction (`facts.py`). The LLM never calculates a number.
- **context**: code proposes (finding, note) pairs by channel and date overlap; one LLM call confirms them and says what each note *requires*. Code then builds the follow-up calls (one `check_target` per target, exclusions for invalid data) and dry-runs them. Pass 2 of the analyst turns those into numbers.
- **writer** (LLM): sees findings and notes, never the CSV. Every number carries a tag (`[F3]` finding, `[N2]` note).
- **reviewer** (code, no LLM): re-checks each tagged number against its finding and evidence call: value, unit, metric, channel, period, aggregation (weekly average vs total) and direction. It also checks uncited numbers, note caveats, the TikTok rule outcome, that every target is reported, and length.

Why this shape: the join between a number and its reason is made in code, not left to the writer's prose; findings have ids so notes and claims can point at them; follow-ups make a note drive further computation instead of being text to read; and typed state makes the trace readable. `trace.md` ends with a table: finding -> note attached -> where cited in the brief.

## Assumptions and data quirks

- `week_start` is irregular (10-day gaps before weeks 5 and 9). A "week" is the 7 days from `week_start`, and "six weeks" in the TikTok note means six records (weeks 7-12).
- The tracking outage (Aug 7-13) overlaps two records (weeks 5 and 6) and the weekly data cannot isolate the days, so both are flagged as uncertain. Numbers computed without them are labelled "excluding records", never "corrected".
- Revenue is exactly $62 x conversions in every row, so revenue and ROAS carry no information beyond conversions and spend.
- The latest record ends Sep 28 but the targets are due Sep 30, so target results are reported as incomplete.
- "Blended" is sum-then-divide. Changes are computed from unrounded numbers; spend/conversions/revenue say whether a figure is a total, a weekly average or a single week.
- Brand spend is not separated from other Meta spend, so the brand campaign is only flagged ("do not judge on direct CPA"), not measured.

## Known limits

Runs are not deterministic even at temperature 0; retries differ run to run, and a run stops with a clear error if a required agent cannot produce valid output. The reviewer works by sentence proximity and keyword lists, so unusual phrasing can be rejected unfairly or, rarely, slip through. `agents/reviewer.py` is over the ~200-line guideline.

## With another day

- Separate Meta brand spend from performance spend.
- Add confidence levels to findings.
- Write eval cases for each note-finding join and run them across models.
- Put the tools behind an MCP server.
- Support new notes arriving each week.
- Add an LLM-judge check, calibrated on labelled briefs, for whether the narrative uses the notes correctly.
- Split the reviewer into number checks and note/target/rule checks, and make pass-1 coverage deterministic instead of prompt-enforced.

## How I used AI tools

TODO(candidate): describe how you used AI tools while building this (what you delegated, what you checked or changed yourself, and what you can defend line by line).
