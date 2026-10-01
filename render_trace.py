"""trace.jsonl (+ state.json) -> trace.json and trace.md, plus checks that brief, state and trace belong to one run.
Usage: python render_trace.py [dir]   (default: the project root = the genuine run; use demo_invalid_draft for the demonstration)"""
import hashlib
import json
import os
import sys

import citations as cz

BASE = {"seq", "ts", "agent", "event", "run_id", "demo"}


def compact(obj, seq, limit=240) -> str:
    s = json.dumps(obj, separators=(",", ":"), default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit - 15] + f"… [{len(s)} chars; full in trace.json, event {seq}]"


def describe_handoff(extra: dict) -> str:
    """One readable line for the structured payload a handoff carries (the exact payload is in trace.json)."""
    parts = []
    if "notes" in extra:
        parts.append("notes: " + "; ".join(f"{n['note_id']} ({n['kind']}{', ' + str(len(n['targets'])) + ' targets' if n['targets'] else ''})" for n in extra["notes"]))
    for key in ("findings", "new_findings"):
        if key in extra:
            parts.append(f"{key.replace('_', ' ')}: " + ("; ".join(f"{f['id']} {f['channel']} {f['metric']}={f['value']} [{f['aggregation']}]" for f in extra[key]) or "none"))
    if "attachments" in extra:
        parts.append("attachments: " + "; ".join(f"{a['finding_id']}→{a['note_id']} [{a['implication']}, {len(a['follow_ups'])} follow-up(s)]" for a in extra["attachments"]))
    if "issues" in extra:
        parts.append(f"verdict {extra.get('verdict')}, issues: " + "; ".join(i["kind"] for i in extra["issues"]))
    for key in ("attempt", "words", "total_findings"):
        if key in extra:
            parts.append(f"{key.replace('_', ' ')} {extra[key]}")
    return " | ".join(parts)


def render_events(rows) -> list[str]:
    out, n, tally, pending = [], 0, [0, 0, 0, 0], None
    for r in rows:
        e, a, q = r["event"], r["agent"], r["seq"]
        extra = {k: v for k, v in r.items() if k not in BASE}
        if e == "agent_start":
            n, tally = n + 1, [0, 0, 0, 0]
            tag = ", ".join(f"{k} {extra[k]}" for k in ("pass", "attempt") if k in extra)
            out += ["", f"## Step {n}: {a}" + (f" ({tag})" if tag else "")]
            out += [f"- fixing reviewer issue: {i['kind']}: {i['detail']}" for i in extra.get("fixing_issues", [])]
        elif e == "llm_call":
            tally[0] += 1; tally[1] += r["tokens_in"]; tally[2] += r["tokens_out"]; tally[3] += r["stop_reason"] not in ("tool_calls", "stop")
        elif e == "tool_call":
            pending = r
        elif e == "tool_result":
            args = pending["args"] if pending else {}
            shown = f"{len(args.get('findings', []))} findings" if r["tool"] == "submit_findings" else compact(args, q, 200)
            warn = " ⚠" if isinstance(r["result"], dict) and "error" in r["result"] else ""
            out.append(f"- `{r['tool']}({shown})` →{warn} {compact(r['result'], q)}")
        elif e == "note_indexed":
            out.append(f"- note `{r['note_id']}`: {r['kind']}, channels {r['channels']}, {r['date_from']}..{r['date_to']}, rule={r['rule']}, targets={compact(r['targets'], q, 160)}")
        elif e == "finding_recorded":
            out.append(f"- **{r['id']}** recorded ({r['kind']}, pass {r['pass']}): {r['channel']} {r['metric']} {r['value']} [{r['aggregation']}]"
                       f" baseline {r['baseline']} [{r['baseline_aggregation']}] change {r['change_pct']} — \"{r['statement']}\""
                       f" — evidence `{r['evidence']['tool']}({compact(r['evidence']['args'], q, 140)})` call #{r['evidence']['call_id']}"
                       + (f", derived from {r['derived_from']}" if r.get("derived_from") else ""))
        elif e in ("candidates", "attachment", "target_followups", "followup_rejected", "warning", "skip", "error", "agent_error"):
            out.append(f"- {'❌ ' if 'error' in e else ''}**{e}**: {compact(extra, q, 420)}")
        elif e == "agent_end":
            out.append(f"- LLM calls: {tally[0]} (tokens in {tally[1]:,} / out {tally[2]:,}); empty or malformed replies retried: {tally[3]}"
                       if tally[0] else "- LLM calls: none (deterministic code)")
            out.append(f"- **Output:** {compact(r['summary'], q, 300)}")
        elif e == "handoff":
            detail = {k: v for k, v in extra.items() if k not in ("frm", "to", "changed")}
            out += [f"- ➜ **handoff** {r['frm']} → {r['to']} (state changed: {', '.join(r['changed'])}): {describe_handoff(detail)}"
                    f"  _(structured payload: trace.json, event {q})_"]
        elif e == "draft":
            out += ["", f"### Draft, attempt {r['attempt']} ({r['words']} words, source: {r['source']})", "", "```markdown", r["text"], "```"]
        elif e == "review_verdict":
            fault = f" [{r['fault']}]" if "fault" in r else ""
            out.append(f"- **review_verdict**{fault}" + (f" attempt {r['attempt']}" if "attempt" in r else "") + f": **{r['verdict'].upper()}**, {len(r['issues'])} issue(s)")
            out += [f"  {k}. {i['kind']}: {i['detail']}" if isinstance(i, dict) else f"  {k}. {i}" for k, i in enumerate(r["issues"], 1)]
        elif e == "retry":
            out.append(f"- ↩ **retry** {r['attempt']} → {r['next_attempt']}, writer must fix {len(r['issues'])} issue(s)")
        elif e in ("retries_exhausted", "fault_injected", "demo_banner", "invalid_draft"):
            body = r.get("text") and ["", "```markdown", r["text"], "```"]
            out.append(f"- **{e}**: {compact({k: v for k, v in extra.items() if k != 'text'}, q, 420)}")
            out += body or []
        elif e == "done":
            out += ["", "## Outcome", f"- {compact(extra, q, 600)}"]
    return out


def tag_sections(draft: str) -> dict:
    """Which section of the draft cites each [F#]/[N#] tag."""
    sec, where = "(top)", {}
    for line in draft.split("\n"):
        if line.startswith("#"):
            sec = line.lstrip("# ").strip()
        for t in cz.cited_ids(line):
            where.setdefault(t, [])
            if sec not in where[t]:
                where[t].append(sec)
    return where


def trace_table(state: dict) -> list[str]:
    where = tag_sections(state["drafts"][-1])
    att = {}
    for a in state["attachments"]:
        att.setdefault(a["finding_id"], []).append(f"{a['note_id']} ({a['implication']})")
    tags = {n["note_id"]: f"N{i + 1}" for i, n in enumerate(state["notes"])}
    out = ["", "## Summary: findings → notes → where cited in the brief", "",
           "| Finding | Claim | Evidence | Notes attached | Cited in brief |", "|---|---|---|---|---|"]
    for f in state["findings"]:
        notes = att.get(f["id"], []) + ([f"via {f['derived_from']}"] if f["derived_from"] else [])
        claim = f"{f['channel']} {f['metric']} {f['value']} ({f['aggregation']})" + (f", baseline {f['baseline']}" if f["baseline"] is not None else "")
        args = json.dumps(f["evidence"]["args"], separators=(",", ":"))
        ev = f"{f['evidence']['tool']}({args if len(args) <= 80 else args[:77] + '...'})"
        out.append(f"| {f['id']} | {claim} | `{ev}` | {'; '.join(notes) or '—'} | {', '.join(where.get(f['id'], [])) or '—'} |")
    out += ["", "| Note | Tag | Findings it is attached to | Cited in brief |", "|---|---|---|---|"]
    for n in state["notes"]:
        fs = sorted({a["finding_id"] for a in state["attachments"] if a["note_id"] == n["note_id"]})
        out.append(f"| {n['note_id']} | {tags[n['note_id']]} | {', '.join(fs) or '—'} | {', '.join(where.get(tags[n['note_id']], [])) or '—'} |")
    return out


def verify(rows, state, brief_text) -> list[tuple[bool, str]]:
    """Do the brief, saved state and trace all describe the same run?"""
    done = next((r for r in rows if r["event"] == "done"), {})
    ids = {r.get("run_id") for r in rows}
    drafts = [r for r in rows if r["event"] == "draft"]
    resolved = {r["model_resolved"] for r in rows if r["event"] == "llm_call"}
    rec = {r["id"]: r for r in rows if r["event"] == "finding_recorded"}
    cited = {t for t in cz.cited_ids(state["drafts"][-1]) if t.startswith("F")}
    return [
        (ids == {state["run_id"]}, f"every trace row carries run_id {state['run_id']} (found {sorted(map(str, ids))})"),
        (brief_text == state["brief"], "brief.md is byte-identical to state.json's brief"),
        (f"run_id: {state['run_id']}" in brief_text, "brief.md names the same run_id"),
        (hashlib.sha256(brief_text.encode()).hexdigest() == done.get("brief_sha256"), "brief.md sha256 equals the one logged in the trace's done event"),
        (len(drafts) == len(state["drafts"]) == state["attempts"] == len(state["reviews"]), f"{len(drafts)} draft events = {len(state['drafts'])} saved drafts = {state['attempts']} attempts = {len(state['reviews'])} reviews"),
        ([d["text"] for d in drafts] == state["drafts"], "every draft in the trace equals the saved draft, in order"),
        (done.get("status") == ("approved" if state["reviews"][-1]["verdict"] == "approve" else "not_approved"), f"trace status '{done.get('status')}' matches the last review verdict"),
        (set(rec) == {f["id"] for f in state["findings"]}, "finding_recorded events match the saved findings"),
        (cited <= set(rec), "every finding cited in the final draft has a trace record"),
        (len(resolved) == 1 and state["model_resolved"] in resolved and state["model"] == done.get("model_requested"),
         f"model requested '{state['model']}', resolved '{state['model_resolved']}' (one id across {sum(r['event'] == 'llm_call' for r in rows)} LLM calls)"),
    ]


def write_all(directory: str = ".") -> list[tuple[bool, str]]:
    path = lambda name: os.path.join(directory, name)
    rows = [json.loads(line) for line in open(path("trace.jsonl")) if line.strip()]
    demo = "demo" in rows[0]
    state = None if demo else json.load(open(path("state.json")))
    checks = [(all("demo" in r for r in rows), "every row is labelled as the invalid-draft demonstration")] if demo else verify(rows, state, open(path("brief.md")).read())
    run_start = next(r for r in rows if r["event"] == "run_start") if not demo else {}
    llm_rows = [r for r in rows if r["event"] == "llm_call"]
    head = {"run_id": rows[0].get("run_id"), "model_requested": run_start.get("model_requested") or (llm_rows[0]["model_requested"] if llm_rows else None),
            "model_resolved": llm_rows[0]["model_resolved"] if llm_rows else None, "as_of_week": run_start.get("as_of_week"),
            "status": next((r.get("status") for r in rows if r["event"] == "done"), None), "started": rows[0]["ts"], "finished": rows[-1]["ts"],
            "event_count": len(rows), "demonstration": rows[0].get("demo") if demo else None}
    json.dump({**head, "checks": [{"ok": ok, "check": m} for ok, m in checks], "events": rows}, open(path("trace.json"), "w"), indent=1, ensure_ascii=False)
    md = [f"# Trace of run `{head['run_id']}`"]
    if demo:
        md += ["", f"> **{rows[0]['demo']}**", "> Nothing below is the genuine run's output; the genuine trace is `trace.md` in the project root."]
    tokens = (sum(r["tokens_in"] for r in llm_rows), sum(r["tokens_out"] for r in llm_rows))
    md += ["", "| | |", "|---|---|", f"| run id | `{head['run_id']}` |", f"| as-of week | {head['as_of_week']} |",
           f"| model requested / resolved | `{head['model_requested']}` / `{head['model_resolved']}` |", f"| status | {head['status']} |",
           f"| started → finished | {head['started']} → {head['finished']} |", f"| events | {len(rows)} (full data in trace.json / trace.jsonl) |",
           f"| LLM calls | {len(llm_rows)} (tokens in {tokens[0]:,} / out {tokens[1]:,}) |", "",
           "Reading guide: each step is one agent run; tool calls show their arguments and result (long results are shortened here; "
           "trace.json has them in full). Drafts, review issues and retries are shown in order."]
    md += ["", "## Consistency checks", ""] + [f"- {'✅' if ok else '❌'} {m}" for ok, m in checks]
    md += render_events(rows)
    if state:
        md += trace_table(state)
    open(path("trace.md"), "w").write("\n".join(md) + "\n")
    return checks


if __name__ == "__main__":
    results = write_all(sys.argv[1] if len(sys.argv) > 1 else ".")
    print("\n".join(("OK   " if ok else "FAIL ") + m for ok, m in results))
    sys.exit(0 if all(ok for ok, _ in results) else 1)
