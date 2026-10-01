"""index_notes: one LLM extraction per note file -> NoteMeta (channels, date window, kind, rule)."""
import glob
import os
from typing import Literal

from pydantic import BaseModel

import llm
import tools
import trace
from state import NoteMeta, RunState


class NoteDraft(BaseModel):  # the part the LLM fills; id and verbatim text are added by code
    channels: list[str]
    date_from: str | None
    date_to: str | None
    kind: Literal["data_quality", "experiment", "campaign_change", "target"]
    rule: str | None


SYSTEM = """You index short team notes for a marketing data system. Extract structured metadata from the note.
- channels: values from {channels} only (map names like 'Google Ads' to the closest one), or ["blended"] for notes about the whole business.
- date_from / date_to: ISO dates (YYYY-MM-DD) the note applies from / to. Use null for an open start or an ongoing end.
  A note that says "from the week of X" starts on that week's start date.
  Data weeks and the dates they cover: {weeks}.
- kind: data_quality (the data is wrong), experiment (a test with an evaluation rule), campaign_change (a budget or campaign change), target (goals).
- rule: if the note contains a decision rule or threshold, restate it as one machine-readable line
  (e.g. "cut if cpa(X)/cpa(Y) > 1.5 over the test period"); otherwise null. Do not invent rules."""


def run(state: RunState) -> list[NoteMeta]:
    emit = lambda event, **p: trace.log("index_notes", event, **p)
    system = SYSTEM.format(channels=tools.CHANNELS, weeks="; ".join(f"week {w}: {a} to {b}" for w, (a, b) in
                           ((w, tools.week_window(w)) for w in tools.WEEKS)))
    notes = []
    for path in sorted(glob.glob("notes/*.md")):
        text = open(path).read().strip()
        draft = llm.structured(system, text, NoteDraft, on_event=emit)
        valid = tools.CHANNELS + ["blended"]
        if bad := [c for c in draft.channels if c not in valid]:
            emit("warning", detail=f"dropped unknown channels {bad}")
        channels = [c for c in draft.channels if c in valid] or ["blended"]
        fields = {k: (None if v in ("null", "None", "") else v) for k, v in draft.model_dump().items()}  # Gemini sends "null" strings
        note = NoteMeta(note_id=os.path.splitext(os.path.basename(path))[0], text=text, **{**fields, "channels": channels})
        emit("note_indexed", **note.model_dump(exclude={"text"}))
        notes.append(note)
    return notes
