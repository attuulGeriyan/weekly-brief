# BUILD PLAN: ~2 hours. Commit after every phase.

At each checkpoint, run the check, show the output, commit, and give a 3-line summary. If a phase runs more than 10 minutes over its estimate, take the cut listed for it and move on.

## Phase 0: setup (5 min)
`git init`, venv, `pip install -r requirements.txt`, then confirm `ANTHROPIC_API_KEY` is set and one tiny API call works.
**Check:** `python -c "import anthropic,pandas,pydantic; print('ok')"`

## Phase 1: state and tools (20 min)
Build `state.py` and `tools.py` as specified in SPEC, plus `trace.py` (`log()` only).
**Check:** a `python -c` snippet that prints:
- `compute_kpi('blended', [12])`
- `compare_channels('tiktok','meta', list(range(7,13)))`
- the first 8 rows of `scan_changes()`

Sanity-check the numbers against a quick manual pandas groupby in the same snippet.

## Phase 2: llm helper and analyst pass 1 (25 min)
`llm.py` provides one function, `run_tool_loop(system, user, tools, max_steps, on_event)`, plus `structured(system, user, schema)` for JSON output via a single forced tool call.
`agents/analyst.py`, pass 1 only. `main.py` runs index_notes and analyst pass 1, then prints the findings.
**Check:** `python main.py --stop-after analyst1` prints 5–8 findings, each with evidence from a real tool call.
**Cut if late:** cap the analyst at 5 steps.

## Phase 3: context and analyst pass 2 (25 min)
`agents/index_notes.py`, `agents/context.py`, and analyst pass 2.
**Check:** `python main.py --stop-after analyst2` prints:
- every attachment (finding → note, implication, follow-ups)
- the pass-2 findings

Confirm that every note attached to something, and that each note's follow-ups produced numbers.
**Cut if late:** drop the LLM confirmation in context and use code matching plus a fixed implication taken from `NoteMeta.kind`.

## Phase 4: writer, reviewer and loop (25 min)
`agents/writer.py`, `agents/reviewer.py`, the full orchestrator loop with max 2 retries, `--inject-error`, and `brief.md` rendering.
**Check:** run `python main.py --inject-error`. The trace must show `fault_injected`, then `review_verdict: reject` with a `number_mismatch`, then `retry`, then `approve`. `brief.md` must be 200–400 words.
**Cut if late:** reviewer checks 1, 2 and 5 only. Write down 3 and 4 as "next".

## Phase 5: trace render and final run (15 min)
`render_trace.py`. Do one clean full run with `--inject-error`, then commit `brief.md`, `trace.jsonl` and `trace.md`.
**Check:** open `trace.md` and confirm it reads top to bottom as a story.

## Phase 6: README (10 min). The candidate writes the "how I used AI" section
`README.md`, about half a page:
- how to run it
- the agent and state design, and why (take the "Why" bullets from SPEC)
- the control flow diagram
- assumptions (data quirks: irregular `week_start` gaps; revenue appears to be derived from conversions)
- what I'd change with another day
- how I used AI tools (leave a `TODO(candidate)` placeholder; the candidate writes this)

"Another day" ideas:
- Separate Meta brand spend from performance spend.
- Add confidence levels to findings.
- Write eval cases for each note–finding join, run across models.
- Put the tools behind an MCP server.
- Support new notes arriving each week.
- Add an LLM-judge check, calibrated on labelled briefs, for whether the narrative uses the notes correctly.
