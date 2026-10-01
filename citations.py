"""Parsing helpers for cited drafts: numbers with their citations, sentences, week mentions, blocks."""
import re
from dataclasses import dataclass

NUM_RE = re.compile(r"(?<![\w.])(\$)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s?%|x\b|k\b|K\b|M\b|\s+times\b)?")
CITE_RE = re.compile(r"(?:-[A-Za-z]+)?[\s)]*(?:[A-Za-z]+\s+){0,4}?((?:\[[FN]\d+(?:\s*,\s*[FN]\d+)*\][ \t]*)+)")
BRACKET_RE = re.compile(r"\[([FN]\d+(?:\s*,\s*[FN]\d+)*)\]")
_SEP = r"(?:[-–—]|to)"
_BASE = re.compile(rf"\b[Ww]eeks?\s+(\d+)(?:\s*{_SEP}\s*(\d+))?")
_MORE = re.compile(rf"\s*(?:,|and|&)\s*(\d{{1,2}})(?:\s*{_SEP}\s*(\d{{1,2}}))?(?![\d%$]|\.\d)")
_LATEST = re.compile(r"\b(?:latest|last|most recent|this) week\b")
SKIP_RE = re.compile(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:\s*[-–—]\s*\d{1,2})?"
                     r"|\b(?:19|20)\d\d(?:-\d\d-\d\d)?\b|^[ \t]*(?:#+[ \t]*)?\d+[.)]", re.M)
SCALE = {"k": 1e3, "M": 1e6}


def week_mentions(text: str) -> list[tuple[int, int, object]]:
    """(start, end, weeks) for each week reference: a frozenset of week numbers, or 'LATEST' for 'last/latest week'.
    Handles "week 12", "weeks 7-12", "weeks 7 to 12", "weeks 5 and 6" and "weeks 7, 8, 9" (a list continues only while it counts up)."""
    out = [(m.start(), m.end(), "LATEST") for m in _LATEST.finditer(text)]
    for m in _BASE.finditer(text):
        last = int(m.group(2) or m.group(1))
        weeks, end = set(range(int(m.group(1)), last + 1)), m.end()
        while (n := _MORE.match(text, end)) and last < int(n.group(1)) <= last + 3:
            last = int(n.group(2) or n.group(1))
            weeks |= set(range(int(n.group(1)), last + 1))
            end = n.end()
        out.append((m.start(), end, frozenset(weeks)))
    return out


@dataclass
class Num:
    text: str
    start: int
    end: int
    value: float
    dec: int  # decimals as displayed
    dollar: bool
    unit: str  # "", "%", "x", "k", "M"
    cites: list  # e.g. ["F3", "N2"]
    ignorable: bool  # week numbers, dates, years, section numbering
    weekish: bool = False  # inside a "week N" mention or a month-day date: never a data claim, even if tagged

    @property
    def tol(self) -> float:
        """Half a unit of the last displayed digit (so $17.9k matches 17,856)."""
        return 0.5 * 10 ** -self.dec * SCALE.get(self.unit, 1) * 1.01 + 1e-9


def numbers(text: str) -> list[Num]:
    weekspans = [(a, b) for a, b, _ in week_mentions(text)] + [m.span() for m in SKIP_RE.finditer(text) if not m.group().strip().startswith(("19", "20"))]
    skip = weekspans + [m.span() for m in SKIP_RE.finditer(text)]
    out = []
    for m in NUM_RE.finditer(text):
        dollar, whole, frac, suffix = m.groups()
        unit = (suffix or "").strip()
        unit = "x" if unit == "times" else unit
        value = float(whole.replace(",", "") + (frac or "")) * SCALE.get(unit, 1)
        c = CITE_RE.match(text, m.end())
        cites = re.findall(r"[FN]\d+", c.group(1)) if c else []
        ignorable = any(a <= m.start() < b for a, b in skip)
        weekish = any(a <= m.start() < b for a, b in weekspans)
        out.append(Num(m.group(), m.start(), m.end(), value, len(frac) - 1 if frac else 0, bool(dollar), unit, cites, ignorable, weekish))
    return out


def sentences(text: str) -> list[tuple[int, int]]:
    out, pos = [], 0
    for line in text.split("\n"):
        out += [(pos + m.start(), pos + m.end()) for m in re.finditer(r".+?(?:[.!?](?=\s|$)|$)", line) if m.group().strip()]
        pos += len(line) + 1
    return out


def sentence_at(text: str, i: int) -> tuple[int, int]:
    return next(((s, e) for s, e in sentences(text) if s <= i <= e), (0, len(text)))


def weeks_in(sentence: str) -> list:
    return [w for _, _, w in week_mentions(sentence)]


def blocks(text: str) -> list[str]:
    """Paragraph-like blocks: each bullet and each heading alone; other lines grouped until a blank line."""
    out, cur = [], None
    for line in text.split("\n"):
        bullet = re.match(r"\s*([-*•]|\d+[.)])\s", line)
        if not line.strip():
            cur = None
        elif line.lstrip().startswith("#") or bullet or cur is None:
            out.append(line)
            cur = None if line.lstrip().startswith("#") or bullet else len(out) - 1
        else:
            out[cur] += "\n" + line
    return out


def cited_ids(text: str) -> set[str]:
    return {i for m in BRACKET_RE.finditer(text) for i in re.findall(r"[FN]\d+", m.group(1))}
