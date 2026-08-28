"""
app/agent/state.py

PHASE 3B/3C ARCHITECTURAL CHANGE - READ THIS FIRST:

interrupt()-based intake (the accepted Phase 1/2 design) is REMOVED
in this slice. This deviates from the explicit instruction to
preserve "one interrupt per invocation" - done deliberately, with
direct empirical evidence, because the two requirements are
provably incompatible:

Confirmed via direct test against our real installed langgraph: a
FRESH (non-resumed) invoke that reaches a node calling interrupt()
pauses immediately, EVERY time - any local state computed in that
function call BEFORE reaching interrupt() (e.g. a freshly-merged
intake answer) is discarded, because the function never reaches its
own return statement. The ONLY way to make interrupt()'s local state
"stick" is Command(resume=...) targeting that EXACT paused call -
which always re-enters the SAME node, with no path to route
elsewhere in between. This means an interrupted node cannot be
"left" for a detour and correctly resumed with progress intact -
proven by direct reproduction (a fresh invoke passing a real answer
value into the paused node discarded the answer and re-asked the
same question), not just reasoned about.

Given suspension/detour correctness is explicitly the top priority
for this slice, intake is now a NORMAL, non-pausing node
(app/agent/nodes/submit_document.py): every turn is a plain
graph invocation through classify_intent_node, which decides EVERY
time whether to route into the intake node or elsewhere - state
fields below (not any interrupt mechanism) are what make the
intake's progress durable across turns and across detours.

NEW FIELDS this slice adds (Phase 3B):
- focused_job_id: RENAMED from active_job_id - same concept ("the
  review the current discussion refers to"), clearer name matching
  the terminology this phase introduces. Not a duplicate field.
- focused_finding_id: not yet meaningfully used (Phase 3D's job),
  present now so the state shape doesn't need another migration then.
- last_submitted_job_id: distinct from focused_job_id - tracks the
  most recently CREATED job specifically, independent of whatever's
  currently focused (submitting doesn't force focus to stay there
  forever if the user later opens something else).

NEW FIELDS this slice adds (Phase 3C):
- new_upload_id/new_filename/new_file_size_bytes/new_content_type:
  a PER-TURN-ONLY signal. chat_service.py explicitly sets these
  every single turn (to a freshly-staged upload's info, or to None
  if no attachment this turn) - never left stale from a previous
  turn. This is what lets classify_intent_node distinguish "a NEW
  attachment arrived THIS turn" from "pending_upload_id is just
  sitting there from before" - the exact distinction this phase
  requires.
- pending_upload_id and friends: UNCHANGED concept from Phase 1/2 -
  the ONGOING, across-turn reference to whatever's mid-intake.
- conflicting_upload_id/conflicting_filename/
  conflicting_file_size_bytes/conflicting_content_type: holds a NEW
  attachment's info while the user resolves a "replace pending B
  with new C, or keep B" choice - kept separate from pending_upload_id
  so B's real state is never touched until the user actually chooses.
- pending_action_signal: transient, classify_intent_node's
  interpretation of what THIS turn means (an intake answer, continue,
  cancel, a conflict-resolution choice) - read once by whichever
  node runs next, not meaningfully used afterward.
"""

from __future__ import annotations
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages

class ChatState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: str | None

    focused_job_id: str | None
    focused_finding_id: str | None
    last_submitted_job_id: str | None

    new_upload_id: str | None
    new_filename: str | None
    new_file_size_bytes: int | None
    new_content_type: str | None

    pending_upload_id: str | None
    pending_filename: str | None
    pending_file_size_bytes: int | None
    pending_content_type: str | None
    intake_answers: dict | None

    conflicting_upload_id: str | None
    conflicting_filename: str | None
    conflicting_file_size_bytes: int | None
    conflicting_content_type: str | None

    pending_action_signal: dict | None

    turn_count: int
    consecutive_unclear_count: int