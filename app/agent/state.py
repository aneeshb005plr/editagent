"""
app/agent/state.py

FIX for external review point 1/2: intake_answers is now a real
PERSISTED state field (checkpointed, survives across turns), not a
local variable inside a while-loop. Combined with the graph-level
loop-back in app/agent/graph.py, this is what makes ONE interrupt()
per node invocation possible - confirmed via direct, isolated test
against our real installed langgraph that this pattern produces
ZERO redundant side-effect execution (2 side effects for 2 real
answers, versus the previous while-loop design's 3-for-2).
"""

from __future__ import annotations
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages

class ChatState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: str | None
    active_job_id: str | None
    pending_upload_id: str | None
    pending_filename: str | None
    pending_file_size_bytes: int | None
    pending_content_type: str | None
    intake_answers: dict | None
    # NEW - persisted across turns (unlike the old local-variable
    # approach), one field per {applies_to, is_pcs, english_variant}.
    # Cleared once a job is created OR intake is cancelled.
    turn_count: int
    consecutive_unclear_count: int