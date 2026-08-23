"""
app/agent/state.py

Checkpointed, persisted-per-thread state schema for the conversational
shell. See app/agent/context.py for the separate, NOT-checkpointed
Runtime context schema.

Confirmed against our real installed langgraph==1.2.10 directly (not
assumed from documentation): StateGraph(state_schema=..., context_
schema=...) + a node receiving `runtime: Runtime[Context]` as a
second parameter is the current, complete pattern - verified with a
real compiled graph, not just read about. See app/agent/graph.py.

TypedDict for STATE (not Pydantic) - deliberate, matches this
project's established distinction: Pydantic at real boundaries where
untrusted data needs validation (app/review/models.py's Finding,
app/jobs/schema.py's ReviewJob), TypedDict/dataclasses for the
hot-path object every node touches on every turn, where the
project's own code is the only thing writing to it, not an LLM or an
HTTP client (LLM OUTPUT does get validated - see models.py's
Literal-typed structured-output schemas, which followed a direct
2026 LangGraph best-practice finding: route on a Literal type, never
on a raw LLM string).
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

IntakeStage = Literal[
    "not_started",
    "awaiting_answers",
    "complete",
]


class PendingIntake(TypedDict, total=False):
    """Tracks in-progress collection of the three real intake
    questions (applies_to/is_pcs/english_variant) - see
    app/review/engine.py's review_document() for why each of these
    must be a real answered question, never auto-detected. total=
    False since fields fill in incrementally as the user answers."""

    stage: IntakeStage
    filename: str
    applies_to: str | None
    is_pcs: bool | None
    english_variant: str | None


class ChatState(TypedDict):
    messages: Annotated[list, add_messages]
    # add_messages reducer - confirmed current standard pattern
    # (langgraph.graph.message.add_messages), accumulates rather than
    # overwrites, same as every current LangGraph example found.

    intent: str | None
    # Last classified intent - a plain string field here (not
    # Literal), since TypedDict field types aren't enforced at
    # runtime the way Pydantic's are anyway - the REAL enforcement
    # happens where it matters, in the structured-output schema the
    # classifier LLM call is bound to (see models.py's
    # IntentClassification, which IS Literal-typed and IS validated
    # by Pydantic at that boundary).

    active_job_id: str | None
    # The job most relevant to the CURRENT thread - not a full job
    # history (see list_jobs_for_user() in repository.py for
    # resolving "which document do you mean" ambiguity on demand,
    # rather than duplicating a job list into checkpointed state that
    # could go stale).

    pending_intake: PendingIntake | None
    # Set while mid-intake (a file was submitted, questions are being
    # collected) - cleared once the job is actually created.

    pending_file_bytes: bytes | None
    pending_filename: str | None
    # Set by the calling service layer (app/services/chat_service.py)
    # for a turn where a file was attached - NOT parsed out of
    # message content. Cleared once consumed by the submit_document
    # flow. Deliberately kept OUT of the message content itself -
    # messages should stay clean, LLM-consumable text; a file
    # attachment is turn metadata, not conversation text.

    turn_count: int
    # A real circuit breaker - confirmed necessary from current
    # LangGraph production-practice research: an unbounded
    # clarification loop (unclear intent -> ask -> still unclear ->
    # ask again...) is a documented real failure mode ("the most
    # expensive mistake in LangGraph production" per current 2026
    # guidance) if nothing bounds it. See nodes/unclear.py.