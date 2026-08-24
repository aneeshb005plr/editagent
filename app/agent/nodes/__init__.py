"""
app/agent/nodes/__init__.py

Re-exports every node function (and route_by_intent) so callers -
specifically app/agent/graph.py - can do
`from app.agent.nodes import classify_intent_node, handle_social_node, ...`
exactly as before, without needing to know this is now a package of
one-node-per-file rather than a single flat module. That property is
deliberate: splitting nodes.py into a package should be invisible to
everything that imports FROM nodes, not just to nodes' own internals.
"""

from app.agent.nodes.additional_output import handle_additional_output_node
from app.agent.nodes.check_status import handle_check_status_node
from app.agent.nodes.classify_intent import classify_intent_node, route_by_intent
from app.agent.nodes.finding_followup import handle_finding_followup_node
from app.agent.nodes.knowledge_question import handle_knowledge_question_node
from app.agent.nodes.off_topic import handle_off_topic_node
from app.agent.nodes.scope_change import handle_scope_change_node
from app.agent.nodes.social import handle_social_node
from app.agent.nodes.submit_document import handle_submit_document_node
from app.agent.nodes.unclear import handle_unclear_node
from app.agent.nodes.create_review_job import create_review_job_node

__all__ = [
    "classify_intent_node",
    "route_by_intent",
    "handle_social_node",
    "handle_off_topic_node",
    "handle_knowledge_question_node",
    "handle_submit_document_node",
    "handle_check_status_node",
    "handle_finding_followup_node",
    "handle_scope_change_node",
    "handle_additional_output_node",
    "handle_unclear_node",
    "create_review_job_node"
]