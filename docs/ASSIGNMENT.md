# Part A — Cover email

**Subject:** Applied AI Engineer — take-home assignment (≈2 hours)

Hi <Name>,

Thanks for your time so far. The next step is a short take-home, designed to take about two hours. It's attached as a PDF/Markdown along with a small CSV dataset.

A few things up front:

- Please cap yourself at ~2 hours. A half-finished system with a clear note on what you'd do next is a perfectly good submission.
- You're welcome to use any AI tools (Claude, ChatGPT, Cursor, Copilot, etc.) and any framework or LLM provider. We only ask that you can explain every part of what you send.
- Submit by replying to this email with a zip or a repo link by <date>.
- After we review it, we'll schedule a 30-minute call to walk through your design together.

If anything in the brief is unclear, just ask — we'd rather answer a question than have you guess.

Best,
<Your name>

---

# Part B — The assignment (attach this + `channel_weekly.csv`)

# Applied AI Engineer — Take-Home Assignment

**Time budget:** ~2 hours. Please stop at 2 hours even if it isn't perfect. We care more about your judgment than completeness.
**AI tools:** Encouraged. Use whatever you normally use. Just make sure you understand and can defend everything you submit.

## The problem

A marketing team gets a weekly dump of channel performance data (spend, clicks, conversions, revenue per channel) plus a handful of loose notes from the team about what happened. Every Monday someone spends an hour turning that into a short "what happened and what should we do" brief for the head of marketing.

Build a small **multi-agent system** that produces that brief automatically.

You are given:

1. `channel_weekly.csv` — 12 weeks of data for 4 channels (attached).
2. Four short **context notes** (below). These are the only institutional knowledge the system has.

The output should be a Markdown brief (~200–400 words) that a head of marketing could read in two minutes and act on. The brief should cover the most recent week in the context of the full period.

### Context notes (treat these as four separate documents)

**note_01_tracking.md**
> Heads up: the Google Ads conversion tag broke on Aug 7 after the site redeploy. Fixed on Aug 13. Conversions reported for that window are not real — don't read anything into them.
> — Priya, Web

**note_02_tiktok.md**
> We 1.6x'd TikTok budget from the week of Aug 15 to test whether it scales. Agreed with finance that we'd evaluate after 6 weeks and cut if TikTok CPA is more than 1.5x Meta's CPA over the test period.
> — Dan, Growth

**note_03_brand.md**
> Brand awareness campaign went live on Meta the week of Sep 1. It's upper-funnel: expect spend to jump and last-click conversions to look worse for a while. Don't judge it on direct CPA.
> — Maya, Brand

**note_04_targets.md**
> Q3 targets: blended CPA under $65, and weekly revenue above $25k by the end of September.
> — Leadership

## Requirements

**1. At least three agents with distinct responsibilities**, coordinated by something: an orchestrator agent, a graph, or a hand-written control loop. Your choice. A reasonable split:

- An **analyst** that works with the data. It should have an actual tool (e.g. a function that runs pandas or SQL over the CSV), not the CSV pasted into a prompt.
- A **context** agent that retrieves the relevant note(s) for each finding the analyst produces. Full RAG is overkill for four notes; a simple lookup is fine. What matters is that the right note reaches the right finding.
- A **writer** that produces the brief.
- Optional but valued: a **reviewer** that checks the brief's numbers against the data and sends it back if something is wrong. If you include it, it must be able to reject and trigger a retry.

**2. Shared, structured state.** Agents should pass structured state between them (findings, retrieved notes, draft, review verdict), not just free-text blobs. We want to see how you designed that state and why.

**3. A trace of one full run.** A log, JSON, or printed output showing which agent ran, in what order, what tools were called with what inputs, and what was handed off. We should be able to follow the run without executing the code.

**4. A README** (half a page is fine): how to run it, your agent and state design and why, what you'd change with another day, and how you used AI tools while building.

### A hint on what "good" looks like

The data and the notes are meant to be read together. A system that only summarises the numbers will reach some wrong conclusions. A system that connects each number to the reason behind it will not. That connection is what we're testing.

### Not required

- A UI. CLI is fine.
- A specific framework. LangGraph, CrewAI, OpenAI Agents SDK, Claude Agent SDK, AutoGen, or plain Python with API calls are all fine. Pick whatever lets you finish.
- Tests, Docker, deployment, evals. Don't spend time here.
- Polished prompts. A clean architecture with okay prompts beats the reverse.

## Deliverables

A zip or repo link containing:

1. Source code
2. `README.md`
3. `trace.*` — the trace of one full run
4. `brief.md` — the generated output from that run

Any LLM provider. A small, cheap model is fine; we're not grading prose quality.

## How we'll evaluate

| Area | Weight |
|---|---|
| Orchestration design: clear agent boundaries, sensible control flow, explicit state, working review loop | 30% |
| Use of context: the right note attached to the right finding, reflected in the brief | 25% |
| Correctness: numbers match the data, tool use is real | 20% |
| Engineering judgment: scope decisions, README reasoning, use of AI tools | 15% |
| Observability: the trace tells the story of the run | 10% |

## Suggested time split (optional)

- 15 min — read, choose framework and state shape
- 45 min — analyst + tool, orchestrator skeleton, something end to end
- 30 min — context agent, writer, review loop
- 20 min — run, capture trace, write README
- 10 min — buffer

If you hit two hours with a half-working system, send it anyway with a note on where you'd go next. That's a real answer too.
