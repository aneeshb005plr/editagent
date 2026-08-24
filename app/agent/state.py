"""
app/agent/state.py

PHASE 2 CHANGES:
- PendingIntake/pending_intake REMOVED entirely. The intake flow now
  uses LangGraph's native interrupt()/Command(resume=...) mechanism
  (see app/agent/nodes/submit_document.py) - the running "which
  answers do we have so far" state lives as a LOCAL variable inside
  that node's own loop, kept alive across turns by LangGraph's
  replay mechanism, not serialized into checkpointed ChatState at
  all. This is a real simplification interrupt() enables, not just
  a rename.
- consecutive_unclear_count ADDED, turn_count's role reduced to
  telemetry only. Per the architecture doc: killing a healthy 30+
  turn conversation is worse than tracking a targeted signal (repeat
  "unclear" classifications) and offering the user a reset only when
  THAT specific pattern repeats. Reset to 0 on any turn that resolves
  to something other than unclear.
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
    # See app/schema/staged_upload.py and app/services/upload_service.py
    # for why raw bytes never appear here (Phase 1) - unchanged in
    # Phase 2, still the small reference only.
    turn_count: int
    # Telemetry only as of Phase 2 - no longer a hard circuit breaker.
    consecutive_unclear_count: int
    # The REAL circuit breaker as of Phase 2 - see app/agent/nodes/
    # unclear.py and classify_intent.py.