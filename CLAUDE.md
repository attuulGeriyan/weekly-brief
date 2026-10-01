# Project: Weekly Marketing Brief — multi-agent system

Take-home for an Applied AI Engineer role. Hard time budget: ~2 hours total. Read these before writing code:

1. `docs/ASSIGNMENT.md`: the brief as given. It is the source of truth for requirements and grading.
2. `docs/SPEC.md`: the architecture we chose. Follow it.
3. `docs/BUILD_PLAN.md`: build order, with a check after each phase.

## Rules

- **Plain Python and the Anthropic SDK.** No agent framework, no MCP, no tests or Docker (the assignment says not to spend time on those). Python 3.11+, with `anthropic`, `pandas` and `pydantic` only.
- **Keep it small and readable.** The candidate must be able to explain every line in a 30-minute call. Prefer obvious code to clever code, and keep every file under ~200 lines.
- **Never hardcode findings or note logic.** No `if week == 6`, no `"google_search"` special cases, no expected numbers in code or prompts. Everything the system concludes must come from:
  - running the tools on `data/channel_weekly.csv`
  - the four files in `notes/`

  The grader is testing whether the system connects numbers to notes by itself.
- **All numbers come from tools.** The LLM never calculates. Every number in the brief must trace back to a tool result recorded in the state.
- **Every step is traced.** Each agent start or end, LLM call, tool call or result, handoff, verdict and retry is appended to the trace (see SPEC §Trace).
- Model comes from env `MODEL` (default `claude-sonnet-5-5`), temperature 0. The API key comes from `ANTHROPIC_API_KEY`.
- **Never read or reference anything in `_private/`.** It is the candidate's own notes.
- After each phase in BUILD_PLAN: run the check command, show the output, `git commit` with a short message, then stop and summarise in 3 lines before moving on.
