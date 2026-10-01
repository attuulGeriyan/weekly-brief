"""Reviewer (code, no LLM): checks every cited number against the findings and their evidence, then the note rules."""
import json
import re

import citations as cz
import facts
import tools
from agents.context import affected_records
from agents.writer import render
from state import Issue, Review, RunState

TERMS = {"cpa": ["cpa", "cost per", "acquisition"], "conversions": ["conversion"], "spend_usd": ["spend", "budget"],
         "revenue_usd": ["revenue"], "roas": ["roas", "return on"], "ratio": ["ratio", "times"],
         "ctr": ["ctr", "click-through"], "cvr": ["cvr", "conversion rate"]}
KEEP_RE = re.compile(r"\b(keep|continue|maintain|expand|extend|scale (?:up|further)|(?:increase|raise|grow)\s+(?:\w+\s+){0,2}(?:budget|spend))\b", re.I)
ACTION_RE = re.compile(r"\b(cut|pause|stop|reduce|halt|shut|wind down|scale back)\b", re.I)


def _mentions(text: str, alias: str) -> bool:
    return re.search(rf"\b{re.escape(alias)}", text.lower()) is not None


def _aliases(channel: str) -> list[str]:
    if channel == "blended":
        return ["blended", "overall", "total", "combined", "all channels"]
    return [channel.replace("_", " ")] + ([channel.split("_")[0]] if "_" in channel else [])


def _candidates(f) -> list[dict]:
    """Everything a citation of finding f may legitimately refer to: its own fields and its evidence call's facts."""
    rows = [(f.channel, f.weeks, f.metric, f.value, f.baseline, f.change_pct)]
    if f.evidence.tool in ("compare_channels", "compare_periods", "check_target", "compute_kpi"):
        rows += [(x["channel"], x["weeks"], x["metric"], x["value"], x["baseline"], x["change_pct"])
                 for x in facts.facts_from(f.evidence.tool, f.evidence.args, f.evidence.result)]
    out = []
    for ch, wk, metric, value, base, chg in rows:
        delta = None if f.kind == "target" or base is None else value - base
        common = dict(channel=ch, weeks=sorted(wk), metric=metric, finding=f.id, base_weeks=f.evidence.args.get("baseline_weeks"),
                      excluded=[w for _, w in f.evidence.args.get("exclude", [])],
                      extra=f.evidence.args.get("metric", "cpa") if metric == "ratio" else None)
        for role, expected, d in (("value", value, delta), ("baseline", base, delta), ("change", chg, chg)):
            if expected is not None:
                out.append({**common, "role": role, "expected": expected, "delta": d})
    return out


def _allowed_weeks(sent: str, cands: dict, notes: dict) -> tuple[set, set]:
    """Week sets a sentence may mention (from every finding/note it cites, plus records a cited call excluded),
    and the subset that are the cited findings' own weeks or note windows (their start/end weeks may be mentioned alone)."""
    allowed, own = set(), set()
    for cid in cz.cited_ids(sent):
        for c in cands.get(cid, []):
            own.add(frozenset(c["weeks"]))
            allowed |= {frozenset(c["weeks"])} | ({frozenset(c["base_weeks"])} if c["base_weeks"] else set()) | {frozenset([w]) for w in c["excluded"]}
        if cid in notes:
            ws = {w for _, w in affected_records(notes[cid])}
            own.add(frozenset(ws))
            allowed |= {frozenset(ws)} | {frozenset([w]) for w in ws}
    return allowed, own


def _problems(c: dict, t: cz.Num, text: str, sent: str, rel: int, weeks_ok: tuple, as_of: int) -> list[str]:
    out = []
    m = c["metric"]  # unit
    money = m in ("spend_usd", "revenue_usd", "cpa")
    if c["role"] == "change":
        if t.unit != "%":
            out.append("[unit] a percentage change must be written with %")
    elif t.unit == "%":
        out.append(f"[unit] % used for a {m} value")
    elif money != t.dollar:
        out.append(f"[unit] {m} {'must' if money else 'must not'} carry $")
    elif (m == "ratio") != (t.unit == "x") and m != "roas":
        out.append(f"[unit] {m} {'must' if m == 'ratio' else 'must not'} be written with x")
    words = TERMS.get(m, []) + (TERMS.get(c["extra"], []) if c["extra"] else [])  # metric
    if not any(w in sent.lower() for w in words) and not (m == "ratio" and t.unit == "x"):
        out.append(f"[metric] sentence does not mention {m} ({'/'.join(words)})")
    own = any(_mentions(sent, a) for a in _aliases(c["channel"]))  # channel: the whole sentence, and the words just before the number
    others = [ch for ch in tools.CHANNELS if ch != c["channel"] and any(_mentions(sent, a) for a in _aliases(ch))]
    clause = re.split(r"[.;:\]]", sent[:rel])[-1]
    named_before = [ch for ch in tools.CHANNELS if ch != c["channel"] and any(_mentions(clause, a) for a in _aliases(ch))]
    if named_before and not any(_mentions(clause, a) for a in _aliases(c["channel"])):
        out.append(f"[channel] the number is introduced as {named_before} but the finding is about '{c['channel']}'")
    elif not own and (c["channel"] != "blended" or others):
        out.append(f"[channel] expected '{c['channel']}' in the sentence" + (f", found {others}" if others else ""))
    mentions = cz.weeks_in(sent)  # period
    if mentions:
        allowed, own = weeks_ok  # a value must be placed in its own weeks; only a baseline may name the baseline weeks instead
        mine = [frozenset(c["weeks"])] + ([frozenset(c["base_weeks"])] if c["role"] == "baseline" and c["base_weeks"] else [])
        mentions = [frozenset([as_of]) if w == "LATEST" else w for w in mentions]
        ends = {min(a) for a in own if a} | {max(a) for a in own if a}  # "from week 7" / "through week 12" are fine
        fits = lambda w: w in allowed or (len(w) == 1 and next(iter(w)) in ends)
        if not any(w in mine for w in mentions) or not all(fits(w) for w in mentions):
            out.append(f"[period] sentence says {[sorted(w) for w in mentions]} but the finding covers weeks {c['weeks']}"
                       + (f" vs baseline weeks {c['base_weeks']}" if c["base_weeks"] else ""))
    if c["delta"]:  # direction: percent changes look at the words next to the number, others at the sentence
        near = text[max(0, t.start - 30):t.end + 30].lower() if c["role"] == "change" else sent.lower()
        w = set(re.findall(r"[a-z]+", near))
        up, down = bool(w & facts.UP), bool(w & facts.DOWN)
        if (up and not down and c["delta"] < 0) or (down and not up and c["delta"] > 0):
            out.append(f"[direction] text says {'up' if up else 'down'} but the data went {'up' if c['delta'] > 0 else 'down'}")
    return out


def _hint(t: cz.Num, refs: dict) -> str:
    """Which finding(s) hold this number, so the writer knows what to cite."""
    hits = [f"{f.id} ({c['role']} of {c['metric']})" for f in refs.values() for c in _candidates(f) if abs(abs(c["expected"]) - t.value) <= t.tol]
    return f" - it matches {', '.join(hits[:3])}; add that tag right after it" if hits else " - no finding has this number; remove it"


def _check_numbers(state: RunState, draft: str) -> list[Issue]:
    refs = {f.id: f for f in state.findings}
    notes = {f"N{i + 1}": n for i, n in enumerate(state.notes)}
    cands = {fid: _candidates(f) for fid, f in refs.items()}
    notes_by_tag = notes
    toks = cz.numbers(draft)
    issues = []
    for t in toks:
        if t.weekish:  # week numbers and dates are not data claims
            continue
        if not t.cites:
            if not t.ignorable:
                issues.append(Issue(kind="uncited_number", detail=f"'{t.text}' in \"{draft[max(0, t.start - 35):t.end + 12].strip()}\" has no [F#]/[N#] citation"
                                    + _hint(t, refs), got=t.text))
            continue
        s = cz.sentence_at(draft, t.start)
        sent = draft[s[0]:s[1]]
        best, note_ok = None, False
        for cid in t.cites:
            if cid.startswith("N"):  # a number quoted from a note must literally appear in it
                note = notes.get(cid)
                note_ok |= note is not None and any(abs(n.value - t.value) <= t.tol for n in cz.numbers(note.text))
            elif cid not in refs:
                issues.append(Issue(kind="number_mismatch", detail=f"unknown citation {cid}", got=t.text))
                note_ok = True  # already reported
            else:
                for c in cands[cid]:
                    if abs(abs(c["expected"]) - t.value) <= t.tol:
                        p = _problems(c, t, draft, sent, t.start - s[0], _allowed_weeks(sent, cands, notes_by_tag), state.as_of_week)
                        if best is None or len(p) < len(best):
                            best = p
        if note_ok:
            continue
        if best is None:
            what = "does not appear in the cited note" if t.cites[0].startswith("N") else "does not match any number of that finding"
            f0 = refs.get(t.cites[0])
            issues.append(Issue(kind="number_mismatch", detail=f"[value] {t.text} {t.cites} {what}",
                                expected=str(f0.value) if f0 else None, got=t.text))
        issues += [Issue(kind="number_mismatch", detail=f"{t.text} {t.cites}: {p}", got=t.text) for p in best or []]
    cited = {i for i in cz.cited_ids(draft) if i in refs}
    for fid in sorted(cited):  # re-run each cited finding's evidence call: stored result must be unchanged
        ev = refs[fid].evidence
        again = tools.TOOLS[ev.tool]["fn"](**ev.args)
        if json.loads(json.dumps(again)) != json.loads(json.dumps(ev.result)):
            issues.append(Issue(kind="number_mismatch", detail=f"evidence of {fid} no longer reproduces", expected=str(ev.result)[:80], got=str(again)[:80]))
    return issues


def _check_notes(state: RunState, draft: str) -> list[Issue]:
    ids = {n.note_id: f"N{i + 1}" for i, n in enumerate(state.notes)}
    notes = {n.note_id: n for n in state.notes}
    issues = []
    for a in state.attachments:
        if a.implication not in ("treat_as_invalid", "do_not_judge_on_metric"):
            continue
        related = {a.finding_id} | {f.id for f in state.findings if f.derived_from == a.finding_id}
        users = [b for b in cz.blocks(draft) if related & cz.cited_ids(b)]
        for b in users:
            if ids[a.note_id] not in cz.cited_ids(b):
                issues.append(Issue(kind="missing_note_caveat", detail=f"block cites {sorted(related & cz.cited_ids(b))} but not {ids[a.note_id]} ({a.note_id}, {a.implication}): {b.strip()[:60]}"))
        if users and a.implication == "treat_as_invalid":  # somewhere the note is cited, every affected record week must be named
            affected = {w for _, w in affected_records(notes[a.note_id])}
            said = set().union(*[w for b in cz.blocks(draft) if ids[a.note_id] in cz.cited_ids(b) for w in cz.weeks_in(b) if w != "LATEST"] or [set()])
            if affected - said:
                issues.append(Issue(kind="missing_note_caveat", detail=f"{a.note_id} is used for {sorted(related & cz.cited_ids(draft))} but the paragraph citing it does not name affected record week(s) {sorted(affected - said)}"))
    return issues


def _check_rules(state: RunState, draft: str) -> list[Issue]:
    ids = {n.note_id: f"N{i + 1}" for i, n in enumerate(state.notes)}
    notes = {n.note_id: n for n in state.notes}
    issues = []
    for a in state.attachments:
        rule = notes[a.note_id].rule
        m = re.search(r"(?P<act>[a-z]+)\s+if\s+.*?(?P<op>>=|<=|>|<)\s*(?P<thr>\d+(?:\.\d+)?)", rule or "", re.I)
        if a.implication != "evaluate_rule" or not m:
            continue
        for f in state.findings:
            about_rule = (f.metric == "ratio") if "/" in rule else (f.metric in rule)
            if f.pass_no != 2 or f.derived_from != a.finding_id or not about_rule or f.channel not in rule:
                continue
            thr = float(m["thr"])
            fired = {">": f.value > thr, ">=": f.value >= thr, "<": f.value < thr, "<=": f.value <= thr}[m["op"]]
            mine = " ".join(b for b in cz.blocks(draft) if f.id in cz.cited_ids(b) or ids[a.note_id] in cz.cited_ids(b))
            keeps, acts = KEEP_RE.findall(mine), ACTION_RE.findall(mine)
            if fired and keeps:
                issues.append(Issue(kind="rule_outcome_mismatch", detail=f"rule '{rule}' fires (value {f.value} {m['op']} {thr}) but the draft says {keeps}"))
            if fired and not acts:
                issues.append(Issue(kind="rule_outcome_mismatch", detail=f"rule '{rule}' fires (value {f.value}) but no '{m['act']}' decision is stated near {f.id}/{ids[a.note_id]}"))
            if not fired and acts:
                issues.append(Issue(kind="rule_outcome_mismatch", detail=f"rule '{rule}' does not fire (value {f.value}) but the draft recommends {acts}"))
    return issues


def run(state: RunState, draft: str) -> Review:
    issues = _check_numbers(state, draft) + _check_notes(state, draft) + _check_rules(state, draft)
    words = len(re.findall(r"[A-Za-z0-9$]\S*", render(draft, state)))  # the rendered brief is what gets delivered
    if not 200 <= words <= 400:
        issues.append(Issue(kind="length", detail=f"{words} words in the rendered brief; must be 200-400"
                            + (f" (cut about {words - 380} words)" if words > 400 else ""), got=str(words)))
    issues = list({(i.kind, i.detail): i for i in issues}.values())  # same problem reported twice counts once
    return Review(verdict="reject" if issues else "approve", issues=issues)
