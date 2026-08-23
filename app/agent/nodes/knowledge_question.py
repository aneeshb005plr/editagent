"""
app/agent/nodes/knowledge_question.py

Option A from the design discussion: answers from RULE_SET directly
(133 curated, cited rules) - no vector store, no embeddings, zero
new infrastructure. See the design discussion for why this is the
right MVP scope vs. a full RAG pipeline over the complete style
guide.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.review.matching import term_matches
from app.rules.schema import Rule
from app.rules.taxonomy import RULE_SET


def _find_candidate_rules(question: str, limit: int = 8) -> list[Rule]:
    """Keyword-matched candidates for a knowledge question - reuses
    the same bounded term-matching already proven for the review
    engine (app.review.matching.term_matches), but applied in the
    OPPOSITE direction: here we're checking which rules' trigger_
    terms appear in the QUESTION, not which rules apply to a
    document block. Rules with no trigger_terms (mostly grammar
    rules, always-candidates during a real review) are excluded here
    deliberately - they're not meaningfully "look-up-able" by
    keyword for a Q&A context the way risk-language/word-usage rules
    are."""

    candidates = []
    for rule in RULE_SET.rules:
        if not rule.trigger_terms:
            continue
        if any(term_matches(term, question) for term in rule.trigger_terms):
            candidates.append(rule)
    return candidates[:limit]


async def handle_knowledge_question_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    genai_client = runtime.context["genai_client"]
    question = state["messages"][-1].content

    candidates = _find_candidate_rules(question)

    if not candidates:
        return {
            "messages": [AIMessage(content=(
                "I don't have a specific rule covering that in what I've been given so "
                "far - the style guide is only partially curated into my rule set right "
                "now. Feel free to ask about something else, or I can note this gap."
            ))]
        }

    rule_context = "\n\n".join(
        f"Rule: {r.rule_id}\nDescription: {r.description}\n"
        f"Alternative: {r.alternative or 'N/A'}\nSource: {r.source_reference}"
        for r in candidates
    )

    response = await genai_client.ainvoke([
        SystemMessage(content=(
            "Answer the user's question using ONLY the rules provided below. Cite the "
            "source_reference. If none of the rules actually answer the question, say so "
            "honestly rather than guessing."
        )),
        HumanMessage(content=f"Rules:\n{rule_context}\n\nQuestion: {question}"),
    ])
    return {"messages": [AIMessage(content=response.content)]}