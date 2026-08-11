"""
app/rules/taxonomy.py

The actual curated rule set, built from real source material shared
during discovery: Grammatical Topics.docx (all 12 items) and the
style guide excerpts received so far (Grammar and punctuation
section, Numbers section, Appendix B risk words - including the
audit-specific overlay, Appendix C audience sensitivities, and the
brand messaging guide).

DELIBERATELY NOT EXHAUSTIVE. Word usage (US) and Global English
sections remain thin (one entry seen total across both, per
discovery notes) - this taxonomy covers what we have real source
content for. Adding more rules later means adding more Rule()
entries to RULE_SET below - it does NOT require touching the review
engine, which is built to consume whatever rules exist without
caring how many there are. This separation is deliberate (see
architecture doc).

CURATION IS STATIC FOR MVP, confirmed decision: this file IS the
"rules maintained technically" answer for September - updating a
rule means a code change and redeploy, not an admin UI. See
conversation history for why this was accepted as a deliberate MVP
simplification, not an oversight.

source_reference on every rule cites the style guide's OWN printed
page numbers (e.g. "p.89") - NOT derived from any of our parsers'
page_number/paragraph_index output, which counts physical document
order and can diverge from what's actually printed on a page (see
schema.py's Rule.source_reference docstring for the full reasoning).
"""

from __future__ import annotations

from app.rules.schema import AppliesTo, DetectionType, Rule, RuleCategory, RuleSet

_grammar_rules = (
    # --- From Grammatical Topics.docx (all judgment - these require
    # real sentence-level understanding, not pattern matching) ---
    Rule(
        rule_id="gram-subject-verb-agreement",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description=(
            "The verb must agree in number with its subject, even when "
            "words intervene between them (e.g. 'a number of', 'each of', "
            "'along with', 'as well as' do not make the verb plural)."
        ),
        explanation="Subject-verb mismatches are common in long sentences and undermine polish.",
        example_before="The team of consultants are prepared.",
        example_after="The team of consultants is prepared.",
        source_reference="Grammatical Topics.docx, PBI 782913; Style Guide p.29",
    ),
    Rule(
        rule_id="gram-verb-tense-consistency",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Proposals should not drift between past, present, and future tense without reason.",
        explanation="Inconsistent tense reads as unpolished and can confuse what's done vs. planned.",
        example_before="We delivered the framework and will analyze the data.",
        example_after="We delivered the framework and analyzed the data.",
        source_reference="Grammatical Topics.docx, PBI 782913",
    ),
    Rule(
        rule_id="gram-pronoun-ambiguity",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Flag ambiguous pronouns ('this', 'it', 'they') where the referent is unclear.",
        explanation="An unclear 'this' or 'it' forces the reader to guess what's being referenced.",
        example_before="This will improve efficiency.",
        example_after="This approach will improve efficiency.",
        source_reference="Grammatical Topics.docx, PBI 782913",
    ),
    Rule(
        rule_id="gram-fragment-inconsistency",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description=(
            "Sentence fragments in bullets are acceptable only if used "
            "consistently and intentionally throughout the list."
        ),
        explanation="Inconsistent fragment usage across bullets reads as sloppy.",
        example_before="Ensuring alignment with stakeholders.",
        example_after="We will ensure alignment with stakeholders.",
        source_reference="Grammatical Topics.docx, PBI 782913",
    ),
    Rule(
        rule_id="gram-run-on-comma-splice",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Flag run-on sentences and comma splices (two independent clauses joined only by a comma).",
        explanation="Run-ons and splices make ideas harder to follow.",
        example_before="The solution is scalable, it also reduces risk.",
        example_after="The solution is scalable, and it also reduces risk.",
        source_reference="Grammatical Topics.docx, PBI 782913",
    ),
    Rule(
        rule_id="gram-apostrophe-misuse",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Flag incorrect apostrophe use, especially plural vs. possessive (e.g. its/it's) and single vs. multiple clients.",
        explanation="Apostrophe errors are small but visible credibility issues.",
        example_before="Client's needs (referring to multiple clients)",
        example_after="Clients' needs",
        source_reference="Grammatical Topics.docx, PBI 782913; Style Guide p.25 (Possessives)",
    ),
    Rule(
        rule_id="gram-parallelism",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Items in a list or bulleted set should follow the same grammatical structure.",
        explanation="Broken parallelism is one of the most common, most visible issues in proposal bullets.",
        example_before="Our goals are improving efficiency, reduced costs, and to enhance quality.",
        example_after="Our goals are improving efficiency, reducing costs, and enhancing quality.",
        source_reference="Grammatical Topics.docx, PBI 782913; Style Guide p.22 (Lists)",
    ),
    Rule(
        rule_id="gram-capitalization-consistency",
        category=RuleCategory.CONSISTENCY,
        # NOT RuleCategory.CAPITALIZATION - this checks whether the
        # SAME term is capitalized consistently ACROSS the document,
        # which a single block cannot answer on its own. Caught while
        # designing the review engine's document-level routing (see
        # architecture doc Section 6) - a per-block judgment pass is
        # structurally unable to answer a cross-block question.
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description=(
            "Flag inconsistent capitalization of the same role/phase/deliverable term "
            "across the document (e.g. 'Project Manager' vs 'project manager')."
        ),
        explanation="Inconsistent capitalization of the same term looks unpolished.",
        example_before="Project manager, Project Manager (used interchangeably)",
        example_after="Pick one form and use it consistently.",
        source_reference="Grammatical Topics.docx, PBI 782913; Style Guide p.5-9 (Capitalization)",
    ),
    Rule(
        rule_id="gram-passive-voice",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Prefer active voice; flag passive constructions that obscure who performs the action.",
        explanation="Active voice gives reviewers clarity on who is accountable for what.",
        example_before="The analysis will be completed.",
        example_after="The team will complete the analysis.",
        source_reference="Grammatical Topics.docx, PBI 782918; Style Guide p.32 (Verb tense, voice, agreement)",
    ),
    Rule(
        rule_id="gram-intro-clause-comma",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="An introductory clause should be followed by a comma before the main clause.",
        explanation="Missing commas after introductory clauses slow the reader down.",
        example_before="To ensure success the team will...",
        example_after="To ensure success, the team will...",
        source_reference="Grammatical Topics.docx, PBI 782913",
    ),
    Rule(
        rule_id="gram-inconsistent-terminology",
        category=RuleCategory.CONSISTENCY,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description=(
            "Flag the same concept referred to by different terms across the document "
            "(e.g. 'client', 'customer', 'end user' for the same group)."
        ),
        explanation="Inconsistent terminology reads as unedited and can confuse the reader about whether different terms mean different things.",
        example_before="client, customer, end user (used for the same group)",
        example_after="Choose one term and standardize it throughout.",
        source_reference="Grammatical Topics.docx, PBI 790639",
    ),
    Rule(
        rule_id="gram-typos-headings-tables",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Typos in section headers, table titles, and figure captions are especially visible and damaging to credibility.",
        explanation="Errors in headings/tables are seen even by skimming readers.",
        source_reference="Grammatical Topics.docx (general note)",
    ),
    Rule(
        rule_id="gram-collective-noun-agreement",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description=(
            "A collective noun (team, staff, management) takes a singular verb when the "
            "group acts as a unit, plural when members act separately - judge from context."
        ),
        explanation="Getting this wrong reads as a basic grammar error even though the rule itself has real nuance.",
        example_before="The committee are not in agreement.",
        example_after="The committee members are not in agreement.",
        source_reference="Style Guide p.8-9 (Collective or non-count nouns)",
    ),
    Rule(
        rule_id="gram-split-infinitive",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description=(
            "Splitting an infinitive is not incorrect, but avoid it when moving the adverb "
            "produces a smoother sentence - don't force awkward rewrites just to avoid it."
        ),
        explanation="A split infinitive isn't wrong, but an awkward one is worth smoothing.",
        example_before="I need to quickly make the decision.",
        example_after="I need to make the decision quickly.",
        source_reference="Style Guide p.16, p.29-30 (Split infinitives)",
    ),
)

_deterministic_mechanical_rules = (
    # --- Cheap, regex/lookup-based - confirmed genuinely deterministic
    # from the real style guide content, no LLM judgment needed ---
    Rule(
        rule_id="punc-acronym-no-periods",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Acronyms should not use periods (e.g. 'HR' not 'H.R.', 'US' not 'U.S.').",
        pattern=r"(?:\b[A-Z]\.){2,}[A-Z]?\.?",
        # NOTE: no trailing \b - verified by direct test that a \b
        # immediately after a period followed by a space DOES NOT
        # match (both '.' and ' ' are non-word chars, and \b only
        # fires at a word/non-word transition) - the original pattern
        # with a trailing \b silently failed to match "U.S." followed
        # by a space, caught by testing the regex against real text,
        # not just confirming it compiles.
        explanation="PwC style omits periods in acronyms.",
        example_before="U.S. operations",
        example_after="US operations",
        source_reference="Style Guide p.4 (Acronyms)",
    ),
    Rule(
        rule_id="punc-double-space-after-period",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Use one space, not two, after a period between sentences.",
        pattern=r"\.\s{2,}[A-Z]",
        explanation="Double spacing after periods is outdated typing convention, not current style.",
        source_reference="Style Guide p.25 (Periods)",
    ),
    Rule(
        rule_id="numbers-four-plus-digits-need-comma",
        category=RuleCategory.NUMBERS_FORMATTING,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Numbers of four or more digits should use commas to separate groups of three (e.g. 12,039,781).",
        pattern=r"\b\d{4,}\b(?!,)",
        explanation="Large numbers without digit-grouping commas are hard to read at a glance.",
        example_before="4238",
        example_after="4,238",
        source_reference="Style Guide p.23 (Numbers - Four or more digits)",
    ),
    Rule(
        rule_id="numbers-range-should-use-en-dash",
        category=RuleCategory.NUMBERS_FORMATTING,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Use an en dash (–), not a hyphen (-), for numeric ranges, with no surrounding spaces.",
        pattern=r"\b\d+\s*-\s*\d+\b",
        explanation="A hyphen instead of an en dash for a range is a common, easily-fixed style deviation.",
        example_before="pages 15-22",
        example_after="pages 15–22",
        source_reference="Style Guide p.24 (Ranges of numbers)",
    ),
    Rule(
        rule_id="brand-no-catalyst-literal",
        category=RuleCategory.BRAND_VOICE,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Never use the literal word 'catalyst' or the phrase 'catalyst for momentum' in writing - it's the brand positioning, not writing to reference directly.",
        pattern=r"\bcatalyst\b",
        explanation="Brand positioning language is meant to be embodied implicitly, not stated literally.",
        source_reference="Style Guide p.34 (New brand messaging guide)",
    ),
)

_audience_sensitivity_gender_neutral = tuple(
    Rule(
        rule_id=f"audience-gender-neutral-{term.replace(' ', '-')}",
        category=RuleCategory.AUDIENCE_SENSITIVITY,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description=f"Prefer a gender-neutral alternative to '{term}'.",
        pattern=rf"\b{term}\b",
        alternative=alt,
        explanation="Gender-specific occupational/role terms should use neutral alternatives per firm guidance.",
        source_reference="Style Guide p.99-100 (Appendix C - Gender-neutral alternatives)",
    )
    for term, alt in [
        ("actress", "actor"),
        ("businessman", "businessperson"),
        ("chairman", "chairperson"),
        ("layman", "layperson"),
        ("salesman", "salesperson"),
        ("spokesman", "spokesperson"),
        ("mankind", "humankind, humanity"),
        ("man-made", "artificial, human-caused"),
        ("manpower", "personnel, staff"),
        ("workman", "worker"),
    ]
)

_risk_language_general = (
    # --- From Appendix B - confirmed almost entirely JUDGMENT-based:
    # these are common English words restricted only in the SPECIFIC
    # sense of describing PwC's own services with unsubstantiated/
    # guarantee-implying language. trigger_terms is a cheap keyword
    # pre-filter (skip the LLM check entirely if none present), NOT a
    # substitute for judgment - the word appearing doesn't mean it's
    # used in the restricted sense. ---
    Rule(
        rule_id="risk-absolutes-all-any-every",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("all", "any", "every"),
        description="Avoid implying services are all-inclusive or guaranteed via 'all', 'any', or 'every' - usually can be deleted.",
        alternative="each, or delete entirely",
        explanation="Avoid implying all-inclusive coverage of PwC's services.",
        example_before="Every member of our dedicated PCS group will address all of your needs.",
        example_after="Our dedicated PCS group will address your needs.",
        source_reference="Style Guide p.89 (Appendix B)",
    ),
    Rule(
        rule_id="risk-unsubstantiated-superlatives",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=(
            "best", "best possible", "greatest", "highest", "ideal",
            "incomparable", "optimal", "perfect", "unequaled",
            "unmatched", "unparalleled", "world-class", "best-in-class",
        ),
        description="Avoid unsubstantiated superlatives describing PwC's own services.",
        alternative="accomplished, exceptional, high-quality, skilled, superior, top-notch",
        explanation="Superlatives about our own services are ambiguous and often unsubstantiated claims.",
        example_before="Our world-class tax team will deliver the highest-quality service possible.",
        example_after="Our skilled tax team will deliver exceptional service.",
        source_reference="Style Guide p.89 (Appendix B)",
    ),
    Rule(
        rule_id="risk-best-practices",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("best practices",),
        description="'Best practices' is an ambiguous, unsubstantiated claim.",
        alternative="standard industry practices, leading practices",
        explanation="Avoid ambiguous or unsubstantiated claims about practice quality.",
        source_reference="Style Guide p.89 (Appendix B)",
    ),
    Rule(
        rule_id="risk-comprehensive-complete",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("comprehensive", "complete"),
        description="Avoid implying our team/solution is all-encompassing via 'comprehensive'/'complete'.",
        alternative="thorough, careful, rigorous, accurate, precise (or delete)",
        explanation="These words can often be removed without changing the meaning.",
        example_before="A comprehensive approach to taxation.",
        example_after="A thorough approach to taxation.",
        source_reference="Style Guide p.90 (Appendix B)",
    ),
    Rule(
        rule_id="risk-customer-not-client",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("customer",),
        description="PwC refers to clients as clients, not customers.",
        alternative="client",
        explanation="Firm-standard terminology.",
        example_before="As our customer, you'll receive the best PwC has to offer.",
        example_after="As our client, you'll benefit from our innovative technology.",
        source_reference="Style Guide p.90 (Appendix B)",
    ),
    Rule(
        rule_id="risk-guarantee-language",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=(
            "ensure", "insure", "assure", "certify", "guarantee",
            "promise", "validate", "verify", "warrant",
        ),
        description="Avoid language implying a guarantee regarding PwC's services.",
        alternative="confirm, help to establish, maintain, support",
        explanation="Avoid implied or actual guarantees about outcomes.",
        example_before="...to ensure that your team members are building the skills...",
        example_after="...to confirm that your team members are building the skills...",
        source_reference="Style Guide p.90 (Appendix B)",
    ),
    Rule(
        rule_id="risk-expert",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("expert", "experts", "expertise"),
        description="'Expert' may imply unverified credentials.",
        alternative="professional, specialist (noun); seasoned, experienced, adept (adjective)",
        explanation="Use more specific, supportable language when describing skill.",
        source_reference="Style Guide p.90 (Appendix B)",
    ),
    Rule(
        rule_id="risk-full-fully",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("full", "fully"),
        description="Avoid implying our services are all-inclusive via 'full'/'fully' - usually can be deleted.",
        alternative="delete, or specify precisely what is meant",
        explanation="We make no implied guarantee that services are all-inclusive.",
        example_before="You'll have access to the full resources of the firm.",
        example_after="You'll have access to firm resources with specialized skills.",
        source_reference="Style Guide p.91 (Appendix B)",
    ),
    Rule(
        rule_id="risk-implement",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("implement", "implemented", "implementing"),
        description="'Implement' may wrongly suggest PwC owns implementation decisions - the client owns implementation, PwC advises/assists (acceptable when it's literally part of the approved engagement solution).",
        alternative="assist, provide professional services in connection with implementation",
        explanation="PwC's role is advisory unless implementation is explicitly the engagement scope.",
        source_reference="Style Guide p.91 (Appendix B)",
    ),
    Rule(
        rule_id="risk-opinion",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("opinion", "opinions", "in our opinion"),
        description="Avoid taking a position on behalf of the firm unless the engagement supports issuing a formal opinion (e.g. audit opinion). PwC does not opine on legal/political matters.",
        alternative="avoid, or reframe as identifying trends/supporting decisions",
        source_reference="Style Guide p.92 (Appendix B)",
    ),
    Rule(
        rule_id="risk-maximize-minimize-optimize",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("maximize", "optimize", "minimize"),
        description="Avoid absolute terms implying guaranteed outcomes - may be acceptable describing a CLIENT's objective, not PwC's promise.",
        alternative="increase, improve, enhance (for maximize/optimize); decrease, reduce, limit, mitigate (for minimize)",
        source_reference="Style Guide p.92 (Appendix B)",
    ),
    Rule(
        rule_id="risk-negotiate",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("negotiate", "negotiating", "negotiation"),
        description="PwC does not negotiate on behalf of clients - may imply legal representation.",
        alternative="avoid entirely",
        source_reference="Style Guide p.92 (Appendix B)",
    ),
    Rule(
        rule_id="risk-partner-alliance",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("partner", "partnering", "partnership", "alliance"),
        description="'Partner'/'alliance' as a noun, or 'partnering'/'partnership' as a verb form describing the client relationship, may imply a legal relationship or independence concerns.",
        alternative="advisor, provider (noun); work/working with, team/teaming with, collaborate (verb)",
        explanation="Note: PwC's own use of 'partner' as a job title is unrelated and not restricted.",
        example_before="As your partner in this important project, we'll work alongside you.",
        example_after="As your advisor for this important project, we'll work alongside you.",
        source_reference="Style Guide p.93 (Appendix B)",
    ),
    Rule(
        rule_id="risk-product-names",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="PwC should remain brand-agnostic and avoid naming specific software products, unless the named technology is specifically required by the engagement.",
        alternative="services, deliverables, solutions",
        example_before="Our use of Tableau and Alteryx will enable us to increase efficiency.",
        example_after="Our use of data analytics and data visualization tools will enable us to increase efficiency.",
        source_reference="Style Guide p.93 (Appendix B)",
    ),
    Rule(
        rule_id="risk-satisfy",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("satisfy", "to client's satisfaction", "meet all your needs"),
        description="PwC should not promise results via 'satisfy'/'meet all your needs'.",
        alternative="address, meet your needs",
        source_reference="Style Guide p.94 (Appendix B)",
    ),
    Rule(
        rule_id="risk-state-of-the-art",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("state-of-the-art", "cutting-edge"),
        description="Ambiguous, unsubstantiated, guarantee-implying technology descriptors.",
        alternative="leading, advanced",
        source_reference="Style Guide p.94 (Appendix B)",
    ),
    Rule(
        rule_id="risk-turnkey",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("turnkey",),
        description="'Turnkey' implies a complete, ready-for-immediate-use solution PwC alone is responsible for.",
        alternative="ready-to-use, equipped, assist/provide professional services",
        source_reference="Style Guide p.94 (Appendix B)",
    ),
    Rule(
        rule_id="risk-unsubstantiated-claims",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Flag claims that can't be substantiated, especially competitive/superiority claims not attributable to an independent source (per R&Q/OGC guidance).",
        example_before="We have the best credentials of any firm providing these services.",
        example_after="Attribute the claim to an independent source, or remove it.",
        source_reference="Style Guide p.33 (Unsubstantiated claims)",
    ),
)

_risk_language_audit_specific = (
    # --- AUDIT-ONLY overlay from Appendix B (p.95-98) - CONFIRMED
    # REAL AND CONFLICTING with general rules above (e.g. assist/
    # collaborate are fine generally, restricted here). Only applied
    # when the user confirms the document is an audit/assurance
    # proposal at intake - see RuleSet.for_applies_to(). ---
    Rule(
        rule_id="risk-audit-advisor-terms",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("advisor", "business advisor", "trusted advisor", "business insights", "business perspective"),
        description="In audit proposals, these terms are inconsistent with PwC's role as independent auditor.",
        alternative="auditor",
        explanation="Audit independence requires avoiding language suggesting an advisory relationship.",
        source_reference="Style Guide p.95 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-assist-help-support",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("assist", "help", "support"),
        description="In audit proposals, 'assist'/'help'/'support' may imply the audit isn't objective and independent. NOTE: these same words are UNRESTRICTED in general (non-audit) proposals - this rule applies only when the document is confirmed as audit/assurance.",
        alternative="advise, provide insights, make observations",
        source_reference="Style Guide p.96 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-chemistry",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("chemistry",),
        description="'Chemistry' with the client may suggest lack of auditor independence.",
        alternative="avoid using",
        source_reference="Style Guide p.96 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-collaborate",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("collaborate", "collaborative"),
        description="In audit proposals, 'collaborate' should refer only to PwC teams working with each other, NOT collaboration with the audit client.",
        alternative="work concurrently with (for client-facing use)",
        source_reference="Style Guide p.96 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-hand-in-hand",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("hand in hand", "side by side", "alongside"),
        description="Do not describe PwC as working hand-in-hand/side-by-side with client staff in audit proposals.",
        alternative="avoid using",
        source_reference="Style Guide p.97 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-relationship-duration",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("long-term audit relationship", "long-standing audit relationship"),
        description="Do not specify how many years PwC has served as the company's auditor.",
        alternative="lasting audit relationship, long-standing professional services relationship",
        source_reference="Style Guide p.97 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-trust-mutual-trust",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("mutual trust", "trust"),
        description="Emphasize objectivity/independence over trust in audit proposals.",
        alternative="objective, independent",
        source_reference="Style Guide p.97 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-pragmatic-practical",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("pragmatic", "practical"),
        description="May imply accepting solutions that are not fully correct - avoid in audit proposals.",
        alternative="avoid using",
        source_reference="Style Guide p.98 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-strategy",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("strategy", "strategic"),
        description="An auditor should not imply playing a strategic role in the client's business.",
        alternative="avoid using in this sense",
        source_reference="Style Guide p.98 (Appendix B - Trust Solutions)",
    ),
)

RULE_SET = RuleSet(
    rules=(
        _grammar_rules
        + _deterministic_mechanical_rules
        + _audience_sensitivity_gender_neutral
        + _risk_language_general
        + _risk_language_audit_specific
    )
)