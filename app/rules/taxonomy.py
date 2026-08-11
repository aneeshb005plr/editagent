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

from app.rules.schema import AppliesTo, DetectionType, EnglishVariant, Rule, RuleCategory, RuleSet

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
        detection_type=DetectionType.JUDGMENT,
        # NOT deterministic, despite looking like a simple pattern:
        # the source preserves periods when U.S./U.K./etc. are part
        # of a proper noun (e.g. "U.S. News & World Report" keeps its
        # periods) - confirmed by external review against the real
        # style guide, which this agent has not independently seen.
        # A blanket regex would have flagged legitimate proper-noun
        # usage as an error. The full exception list (likely in the
        # "United States"/"United Kingdom" Word-usage entries) has
        # NOT been supplied to this agent - needs verification against
        # the real file before this rule's coverage can be trusted as
        # complete.
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("U.S.", "U.K.", "U.N."),
        description=(
            "Acronyms should not use periods (e.g. 'HR' not 'H.R.', 'US' not 'U.S.'), "
            "EXCEPT when the abbreviation is part of a proper noun (e.g. 'U.S. News & "
            "World Report' keeps its periods) - judge whether this is a proper-noun "
            "context before flagging."
        ),
        explanation="PwC style omits periods in acronyms, except within proper nouns that include the periods as part of their actual name.",
        example_before="U.S. operations",
        example_after="US operations",
        source_reference="Style Guide p.4 (Acronyms); proper-noun exception per external review, page unverified by this agent",
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
        description="Numbers of four or more digits should use commas to separate groups of three (e.g. 12,039,781) - EXCEPT years, which use plain numerals with no comma.",
        pattern=r"\b(?!1\d{3}\b|2\d{3}\b)\d{4,}\b(?!,)",
        # Excludes 1000-2999 (plausible year range) from matching -
        # confirmed via direct test that the ORIGINAL pattern flagged
        # "2020" as needing comma-grouping, directly contradicting
        # the source's own Years entry ("use numerals for years...
        # 2020" - no comma). This is an approximation (a genuine
        # 4-digit count that happens to fall in 1000-2999, e.g. "1500
        # errors found", would now be incorrectly EXEMPTED) - the
        # tradeoff was judged better than the reverse (flagging every
        # year in the document as wrong), but worth knowing this
        # isn't a perfect fix.
        explanation="Large numbers without digit-grouping commas are hard to read at a glance (years are the standard exception).",
        example_before="4238",
        example_after="4,238",
        source_reference="Style Guide p.23 (Numbers - Four or more digits) and p.24 (Years)",
    ),
    Rule(
        rule_id="punc-em-dash-spacing",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="An em dash (—) should have a space before and after it - this deviates from PwC brand style, which omits the spaces.",
        pattern=r"(?<!\s)—|—(?!\s)",
        explanation="Pursuit Support style deliberately differs from PwC brand style here - spaced, not unspaced.",
        example_before="the delay—caused by incomplete data—was significant",
        example_after="the delay — caused by incomplete data — was significant",
        source_reference="Style Guide p.12 (Dash - Em dash)",
    ),
    Rule(
        rule_id="punc-en-dash-no-spaces",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="An en dash (–) used for a range should have NO spaces before or after it.",
        pattern=r"\s–\s",
        explanation="Unlike the em dash, the en dash for ranges takes no surrounding spaces.",
        example_before="pages 15 – 22",
        example_after="pages 15–22",
        source_reference="Style Guide p.12 (Dash - En dash)",
    ),
    Rule(
        rule_id="punc-ampersand-misuse",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("&",),
        description=(
            "Don't use an ampersand as a substitute for 'and', except when it's part of "
            "a proper noun (e.g. 'Standard & Poor's', 'Ernst & Young') or space is "
            "genuinely constrained (e.g. in a graphic). Don't mix ampersand and 'and' "
            "usage inconsistently within the same document."
        ),
        explanation="Ampersand substitution for 'and' is only acceptable in proper nouns or space-constrained graphics.",
        example_before="risk & capital management",
        example_after="risk and capital management",
        source_reference="Style Guide p.5 (Ampersand)",
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
        rule_id="numbers-spell-out-zero-to-nine",
        category=RuleCategory.NUMBERS_FORMATTING,
        detection_type=DetectionType.JUDGMENT,
        # JUDGMENT, not deterministic - the general rule ("spell out
        # zero-nine, numerals 10+") is overridden by MANY specific
        # exceptions from the same source table: ages, currency,
        # percentages, decimals, page numbers, phone numbers, and
        # symbols (5°C, 35mm) ALWAYS use numerals regardless of value;
        # units of measure use a DIFFERENT threshold (spell out zero
        # through TEN, not nine). A blind regex can't reliably
        # distinguish these contexts - the LLM needs to know the
        # exception categories to judge correctly.
        applies_to=AppliesTo.GENERAL,
        description=(
            "Spell out whole numbers zero through nine; use numerals for 10 and above "
            "(deviates from PwC brand style, which spells out 'ten'). EXCEPTIONS that "
            "always use numerals regardless of value: ages, currency, percentages, "
            "decimals, page numbers, phone numbers, symbols/units (5°C, 35mm), tabular/"
            "statistical numbers. Units of measure (distance/length/area in nontechnical "
            "material) use a different threshold: spell out zero through TEN, numerals "
            "for 11+. When multiple numbers of the same category appear together in a "
            "paragraph and at least one requires a numeral, use numerals for all of them."
        ),
        explanation="Numeral/word-form for numbers has many source-defined exceptions by category.",
        example_before="We reviewed 5 documents and identified 12 issues.",
        example_after="We reviewed five documents and identified 12 issues.",
        source_reference="Style Guide p.21-23 (Numbers - General guidelines and Specific standards)",
    ),
    Rule(
        rule_id="numbers-sentence-initial-spell-out",
        category=RuleCategory.NUMBERS_FORMATTING,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Spell out any number that begins a sentence (though the construction should generally be avoided).",
        pattern=r"(?:^|[.!?]\s+)\d+\b",
        explanation="A sentence should not begin with a numeral.",
        example_before="5 people attended the meeting.",
        example_after="Five people attended the meeting.",
        source_reference="Style Guide p.21 (Numbers - General guidelines)",
    ),
    Rule(
        rule_id="numbers-ordinal-spell-out",
        category=RuleCategory.NUMBERS_FORMATTING,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Spell out ordinal numbers (first, not 1st; second, not 2nd).",
        pattern=r"\b\d+(?:st|nd|rd|th)\b",
        explanation="Numeral-form ordinals should be spelled out per style guide.",
        example_before="This is the 1st time we've partnered on this.",
        example_after="This is the first time we've partnered on this.",
        source_reference="Style Guide p.23 (Numbers - Ordinal numbers)",
    ),
    Rule(
        rule_id="numbers-percent-no-hyphen-before-noun",
        category=RuleCategory.NUMBERS_FORMATTING,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Don't hyphenate a percentage when used before a noun.",
        pattern=r"\d+%-\w",
        explanation="A percentage modifying a noun should not be hyphenated to it.",
        example_before="a 25%-off sale",
        example_after="a 25% off sale",
        source_reference="Style Guide p.23 (Numbers - Percentages)",
    ),
    Rule(
        rule_id="numbers-percent-range-dash",
        category=RuleCategory.NUMBERS_FORMATTING,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Use the percent symbol after both numbers in a range, separated by an en dash with no surrounding spaces.",
        pattern=r"\d+%\s+[\u2013-]\s+\d+%|\d+%-\d+%",
        explanation="Percentage ranges follow the same en-dash-no-spaces rule as other numeric ranges, but with the % symbol on both numbers.",
        example_before="growth of 20% - 50%",
        example_after="growth of 20%–50%",
        source_reference="Style Guide p.23 (Numbers - Percentages)",
    ),
    Rule(
        rule_id="numbers-percent-symbol-consistency",
        category=RuleCategory.CONSISTENCY,
        # Document-level, not per-block: the source's own wording is
        # "whichever you use, be consistent throughout" - a single
        # block can't answer whether the SAME document uses both "%"
        # and "percent" spelled out in different places.
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Whether using the % symbol or the word 'percent', be consistent throughout the entire document - don't mix both styles.",
        explanation="Mixed percent-symbol/percent-word usage across a document reads as unedited.",
        source_reference="Style Guide p.23 (Numbers - Percentages)",
    ),
    Rule(
        rule_id="lists-parallel-construction",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Items in a numbered/bulleted list should be in parallel grammatical construction, capitalize the first word of each item, and a list should have more than one item.",
        explanation="Confirmed as its own dedicated Lists section in the source, distinct from (though related to) the general parallelism grammar rule.",
        source_reference="Style Guide p.21 (Lists)",
    ),
    Rule(
        rule_id="lists-punctuation",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description=(
            "Omit periods after vertical list items unless one or more items are complete "
            "sentences; if none of the items is a complete sentence, use no end punctuation "
            "(no comma/semicolon/period) on any item."
        ),
        explanation="List punctuation should be consistent with whether items are complete sentences.",
        source_reference="Style Guide p.21 (Lists - Punctuation with lists)",
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
        ("clergyman", "member of clergy"),
        ("craftsmanship", "skill"),
        ("gentleman", "chap, guy, person"),
        ("landlord", "house owner"),
        ("landlady", "house owner"),
        ("layman", "layperson"),
        ("lineman", "line worker"),
        ("manhole", "access port, inspection port"),
        ("man hours", "worker hours, people hours"),
        ("mankind", "humankind, humanity"),
        ("man-made", "artificial, human-caused"),
        ("manpower", "personnel, staff"),
        ("newsman", "reporter, journalist"),
        ("salesman", "salesperson"),
        ("spokesman", "spokesperson"),
        ("sportsmanship", "fairness, integrity"),
        ("statesman", "senior member"),
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
            "incomparable", "optimal", "perfect", "unequaled", "unequivocal",
            "unmatched", "unparalleled", "world-class", "best-in-class",
        ),
        description="Avoid unsubstantiated superlatives describing PwC's own services.",
        alternative=(
            "accomplished, adept, appropriate, capable, clear-cut, distinguished, "
            "exceptional, high-quality, keen, reasonable, right, skilled, skillful, "
            "suitable, superior, talented, top-notch, leading-class"
        ),
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
        alternative="thorough, careful, rigorous, accurate, skillful, precise, stringent (or delete)",
        explanation="These words can often be removed without changing the meaning.",
        example_before="A comprehensive approach to taxation.",
        example_after="A thorough approach to taxation.",
        source_reference="Style Guide p.90 (Appendix B)",
    ),
    Rule(
        rule_id="risk-customer-not-client",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.LEXICAL,
        # Reclassified from JUDGMENT: unconditional per source
        # ("PwC refers to clients as clients, not customers" - no
        # "when..." qualifier) - no sense-judgment needed, "customer"
        # is simply never the right word here.
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("customer", "customers"),
        description="PwC refers to clients as clients, not customers.",
        alternative="client",
        explanation="Firm-standard terminology.",
        example_before="As our customer, you'll receive the best PwC has to offer.",
        example_after="As our client, you'll benefit from our innovative technology.",
        source_reference="Style Guide p.90 (Appendix B)",
    ),
    Rule(
        rule_id="risk-guarantee-language-verbs",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("ensure", "insure", "assure"),
        description="Avoid language implying a guarantee regarding PwC's services.",
        alternative="allow, assist, confirm, enable, establish that, facilitate, make sure, promote, see to it, support",
        explanation="Avoid implied or actual guarantees about outcomes.",
        example_before="...to ensure that your team members are building the skills...",
        example_after="...to confirm that your team members are building the skills...",
        source_reference="Style Guide p.90 (Appendix B)",
    ),
    Rule(
        rule_id="risk-guarantee-language-formal",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("certify", "guarantee", "promise", "validate", "verify", "warrant"),
        description="Avoid formal guarantee/certification language implying PwC is promising an outcome.",
        alternative="confirm, help to establish, maintain",
        explanation="Avoid implied or actual guarantees about outcomes.",
        source_reference="Style Guide p.90 (Appendix B)",
        # NOTE: this is a SEPARATE table row from risk-guarantee-
        # language-verbs above, with its own, shorter alternative
        # list - previously incorrectly merged into one rule using
        # only this row's alternative for both. Split to match the
        # source's actual two-row structure, confirmed against
        # already-pasted content.
    ),
    Rule(
        rule_id="risk-increase-eps-shareholder-value",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("increase earnings per share", "increase shareholder value"),
        description="Avoid promising specific financial results or implying guaranteed outcomes.",
        alternative='avoid, or modify by adding "help to" / "can help to"',
        explanation="PwC should not promise financial results or imply guaranteed outcomes.",
        example_before="Our automation solutions will result in an increase in shareholder value.",
        example_after="The efficiencies gained through our automation solutions can help your company realize an increase in shareholder value.",
        source_reference="Style Guide p.91 (Appendix B)",
    ),
    Rule(
        rule_id="risk-know-your-needs",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("know your needs", "knows all of your needs", "know all of your needs"),
        description="PwC advises and assists clients - it should not claim to know every client need.",
        alternative="help articulate your needs, understand your needs",
        example_before="Your PwC team knows all of your needs and will work closely with you to achieve your objectives.",
        example_after="Your PwC team has a clear understanding of your needs and will work with you to achieve your objectives.",
        source_reference="Style Guide p.92 (Appendix B)",
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
        detection_type=DetectionType.LEXICAL,
        # Reclassified from JUDGMENT: "Avoid the word" is unconditional
        # per source (the narrow exception - a genuine formal audit
        # opinion - is a distinct, separate professional-standards
        # usage the firm wouldn't be drafting via this tool's casual
        # proposal text in the first place).
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
        detection_type=DetectionType.LEXICAL,
        # Reclassified from JUDGMENT: "Avoid the word" is unconditional
        # per source, no "when..." qualifier.
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("negotiate", "negotiating", "negotiation"),
        description="PwC does not negotiate on behalf of clients - may imply legal representation.",
        alternative="avoid entirely",
        source_reference="Style Guide p.92 (Appendix B)",
    ),
    Rule(
        rule_id="risk-no-surprises",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("no surprises",),
        description="PwC should not promise there will be no surprises during an engagement.",
        alternative="avoid surprises, mitigate surprises",
        explanation="Avoid implying a guarantee that nothing unexpected will occur.",
        example_before="We're committed to transparency throughout this engagement; you'll have no surprises working with us.",
        example_after="We're committed to transparency and open communication throughout this engagement.",
        source_reference="Style Guide p.93 (Appendix B)",
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
        rule_id="risk-time-is-of-the-essence",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("time is of the essence",),
        description="This phrase implies urgency and may suggest a lack of due care - delivery depends on the agreed schedule, not implied urgency.",
        alternative="in accordance with our agreed schedule, in a timely manner",
        example_before="When it comes to an audit schedule, time is of the essence.",
        example_after="When it comes to an audit schedule, we'll help you realize speed to value by utilizing our extensive experience executing on agreed-upon schedules.",
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
        pcs_exception=True,
        # CONFIRMED directly against the real style guide (p.95): this
        # exact row is marked "Acceptable in PCS Trust Solutions
        # (audit) proposals" - no longer secondhand/unverified.
        trigger_terms=("advisor", "business advisor", "trusted advisor", "business insights", "business perspective"),
        description="In audit proposals, these terms are inconsistent with PwC's role as independent auditor (EXCEPT in PCS/private-company audit proposals, where this is explicitly marked acceptable).",
        alternative="auditor",
        explanation="Audit independence requires avoiding language suggesting an advisory relationship.",
        source_reference="Style Guide p.95 (Appendix B - Trust Solutions, PCS exception confirmed)",
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
        rule_id="risk-audit-professional-standards-terms",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("audit", "assurance", "compilation", "examination", "review"),
        description=(
            "These words have defined meanings in professional standards and should only "
            "be used in a manner consistent with that meaning - use in a context unrelated "
            "to financial statements may be acceptable."
        ),
        explanation="A distinct row in the source, separate from 'agreed-upon' - these five core terms should not be used loosely.",
        example_before="We'll work with you to conduct an agreed-upon audit.",
        example_after="Our team will determine an appropriate approach to completing the audit.",
        source_reference="Style Guide p.96 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-agreed-upon",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("agreed-upon", "agreed upon"),
        description=(
            "'Agreed-upon' has a specific professional-standards meaning when modifying "
            "procedures, audit, assurance, compilation, examination, or review - use only "
            "consistent with that technical meaning, not loosely."
        ),
        alternative="analysis, study, procedures",
        explanation="Professional-standards terminology should only be used in its proper technical sense.",
        example_before="We'll work with you to conduct an agreed-upon audit.",
        example_after="Our team will determine an appropriate approach to completing the audit.",
        source_reference="Style Guide p.95 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-chemistry",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.LEXICAL,
        # Reclassified from JUDGMENT: "avoid using" is unconditional
        # per source, no "when..." qualifier.
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
        pcs_exception=True,
        # CONFIRMED directly against the real style guide (p.96): this
        # exact row is marked "Acceptable in PCS Trust Solutions
        # (audit) proposals" - no longer secondhand/unverified.
        trigger_terms=("collaborate", "collaborative"),
        description="In audit proposals, 'collaborate' should refer only to PwC teams working with each other, NOT collaboration with the audit client (EXCEPT in PCS proposals, where this is explicitly marked acceptable).",
        alternative="work concurrently with (for client-facing use)",
        source_reference="Style Guide p.96 (Appendix B - Trust Solutions, PCS exception confirmed)",
    ),
    Rule(
        rule_id="risk-audit-expertise",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.LEXICAL,
        # Unconditional per source: "Do not use 'expertise' in audit
        # proposals." Distinct, STRICTER audit-specific override of
        # the general risk-expert rule, which allows expert/expertise
        # with judgment-based alternatives outside audit contexts.
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("expertise",),
        description="Do not use 'expertise' in audit proposals - can be misunderstood as unsubstantiated, may imply unverified earned credentials.",
        alternative="avoid using",
        source_reference="Style Guide p.96 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-final-decision-maker",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("final decision maker",),
        description="The engagement partner speaks for the firm but is not the final decision maker on technical matters.",
        alternative="speaks for the firm",
        example_before="[Partner] will make all decisions affecting the audit.",
        example_after="[Partner] will speak for the firm with respect to decisions affecting the audit.",
        source_reference="Style Guide p.97 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-hand-in-hand",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.LEXICAL,
        # Reclassified from JUDGMENT: "avoid using" is unconditional.
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("hand in hand", "side by side", "alongside"),
        description="Do not describe PwC as working hand-in-hand/side-by-side with client staff in audit proposals.",
        alternative="avoid using",
        source_reference="Style Guide p.97 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-meet-your-needs",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.LEXICAL,
        # Distinct from the GENERAL "risk-satisfy" rule (which covers
        # "to meet ALL your needs") - this audit-specific entry
        # ("meet your needs", no "all") is unconditional ("Avoid
        # using") per source, since independent auditors should focus
        # on regulatory obligations, not client needs generally.
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("meet your needs",),
        description="Independent auditors should focus on regulatory obligations, not on meeting client needs.",
        alternative="avoid using",
        example_before="We'll deliver the perspective you need.",
        example_after="We'll deliver the objective perspective you need.",
        source_reference="Style Guide p.97 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-pass-passed",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.LEXICAL,
        # Unconditional for individual PCAOB inspection reports;
        # source notes it's acceptable ONLY in the distinct context of
        # peer review reports - since that's a narrow, separate usage
        # this tool is unlikely to encounter in proposal drafting, LEXICAL
        # is the pragmatic classification; revisit if peer-review-report
        # false positives turn out to be common in practice.
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("passed recent", "pass/passed", "passed inspection", "passed the inspection"),
        description="Do not state that a firm 'passed' a PCAOB inspection - acceptable only in the context of peer review reports.",
        alternative="avoid using",
        example_before="Donna and Bill have passed recent PCAOB inspections.",
        example_after="Donna and Bill were recently inspected by the PCAOB and received no comment forms.",
        source_reference="Style Guide p.98 (Appendix B - Trust Solutions)",
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
        detection_type=DetectionType.LEXICAL,
        # Reclassified from JUDGMENT: "avoid using" is unconditional.
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("pragmatic", "practical"),
        description="May imply accepting solutions that are not fully correct - avoid in audit proposals.",
        alternative="avoid using",
        source_reference="Style Guide p.98 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-strategy",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.LEXICAL,
        # Reclassified from JUDGMENT: source phrasing ("should not
        # imply playing a strategic role") is unconditional for this
        # word in an audit context, no "when..." qualifier given.
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("strategy", "strategic"),
        description="An auditor should not imply playing a strategic role in the client's business.",
        alternative="avoid using in this sense",
        source_reference="Style Guide p.98 (Appendix B - Trust Solutions)",
    ),
    Rule(
        rule_id="risk-audit-value-beyond-audit",
        category=RuleCategory.RISK_LANGUAGE,
        detection_type=DetectionType.LEXICAL,
        applies_to=AppliesTo.AUDIT,
        trigger_terms=("value beyond the audit",),
        description="The role of an independent auditor is limited to conducting the audit - should not imply delivering additional business value beyond it.",
        alternative="value through the audit",
        example_before="By engaging PwC, you'll receive business value beyond the audit.",
        example_after="By engaging PwC, you'll receive value through the audit.",
        source_reference="Style Guide p.98 (Appendix B - Trust Solutions)",
    ),
)

_audience_sensitivity_other = (
    # --- From Appendix C's general guidelines - confirmed present in
    # already-pasted content, text-checkable JUDGMENT rules. NOTE:
    # the source also flags the thumbs-up gesture as culturally
    # offensive in some regions - that guidance is explicitly NOT
    # representable as a Rule() here, since it concerns interpreting
    # IMAGE CONTENT (a gesture in a photo/graphic), not text - this
    # rules engine's per-block passes operate on ContentBlock.text,
    # not raw image interpretation. A real gap, flagged rather than
    # silently dropped - would need a distinct vision-based check,
    # not an extension of the text taxonomy. ---
    Rule(
        rule_id="audience-cliches-jargon",
        category=RuleCategory.AUDIENCE_SENSITIVITY,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Avoid clichés and jargon - audiences may find them confusing or culturally inappropriate, especially for a global audience.",
        explanation="Clichés and jargon don't translate well across cultures and can obscure meaning.",
        source_reference="Style Guide p.99 (Appendix C - General Guidelines)",
    ),
    Rule(
        rule_id="audience-war-sports-analogies",
        category=RuleCategory.AUDIENCE_SENSITIVITY,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Avoid war or sports analogies (e.g. 'game plan', 'home run', 'battle-tested') - may confuse or alienate global audiences unfamiliar with the reference.",
        explanation="War/sports metaphors are culturally specific and may not translate or may read as inappropriate.",
        source_reference="Style Guide p.100 (Appendix C - Additional Guidelines)",
    ),
    Rule(
        rule_id="audience-humor-caution",
        category=RuleCategory.AUDIENCE_SENSITIVITY,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        description="Use humor carefully - it is culture-specific and generally should be avoided in global communications unless clearly appropriate for the audience.",
        explanation="Humor rarely translates well across cultures and can undermine a professional tone if misjudged.",
        source_reference="Style Guide p.100 (Appendix C - Additional Guidelines)",
    ),
)

_word_usage_confusables = (
    # --- Genuine semantic-confusion pairs from Word usage (US),
    # p.38-65 - JUDGMENT, since telling which word is correct
    # requires understanding the sentence's meaning. ---
    Rule(
        rule_id="usage-accept-except",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("accept", "except"),
        description="'Accept' means to receive; 'except' means to exclude. Flag if used in the wrong sense.",
        example_before="Most companies except the fact that they must deal with more new regulations.",
        example_after="Most companies accept the fact that they must deal with more new regulations.",
        source_reference="Style Guide p.38 (Word usage - accept, except)",
    ),
    Rule(
        rule_id="usage-adverse-averse",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("adverse", "averse"),
        description="'Adverse' means strongly opposed/unfortunate and typically refers to things; 'averse' means feeling negatively/reluctant and refers to people.",
        example_before="Most boards of directors are adverse to implementing dramatic changes.",
        example_after="Most boards of directors are averse to implementing dramatic changes.",
        source_reference="Style Guide p.39 (Word usage - adverse, averse)",
    ),
    Rule(
        rule_id="usage-affect-effect",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("affect", "effect"),
        description="'Affect' (verb) means to influence; avoid 'affect' as a noun entirely. 'Effect' (verb) means to cause/bring about; 'effect' (noun) means result.",
        example_before="The effect of the new regulation will change how companies report earnings.",
        example_after="The new regulation will affect how companies report earnings.",
        source_reference="Style Guide p.39 (Word usage - affect, effect)",
    ),
    Rule(
        rule_id="usage-comprise-compose",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("comprise", "compose", "comprised of", "is comprised of"),
        description="'Comprise' means contain/include/consist of (the whole comprises the parts); 'compose' means to make up (the parts compose the whole). 'Is comprised of' is INCORRECT - use 'is composed of'.",
        alternative="is composed of (never 'is comprised of')",
        example_before="PwC LLC is comprised of a global network of member firms.",
        example_after="PwC LLC comprises a global network of member firms.",
        source_reference="Style Guide p.45 (Word usage - comprise, compose)",
    ),
    Rule(
        rule_id="usage-everyday-every-day",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("everyday", "every day"),
        description="'Everyday' is an adjective (everyday decisions); 'every day' is an adverbial phrase (happens every day).",
        example_before="The vice president of finance makes hundreds of decisions everyday.",
        example_after="The vice president of finance makes hundreds of decisions every day.",
        source_reference="Style Guide p.49 (Word usage - everyday, every day)",
    ),
    Rule(
        rule_id="usage-farther-further",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("farther", "further"),
        description="'Farther' is restricted to physical distance. 'Further' means to a greater extent/degree, figurative distance, or 'moreover/in addition'.",
        example_before="Let's discuss the proposal farther.",
        example_after="Let's discuss the proposal further.",
        source_reference="Style Guide p.50 (Word usage - farther, further)",
    ),
    Rule(
        rule_id="usage-fewer-less",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("fewer", "less"),
        description="'Fewer' refers to number, used with plural nouns. 'Less' refers to degree/amount, used with singular nouns or a quantity treated as a single bulk amount.",
        example_before="Less accidents were reported than we expected.",
        example_after="Fewer accidents were reported than we expected.",
        source_reference="Style Guide p.50 (Word usage - fewer, less)",
    ),
    Rule(
        rule_id="usage-that-which",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("that", "which"),
        description=(
            "In US English, 'that' is used restrictively (narrows a category/identifies a "
            "particular item); 'which' is used nonrestrictively (adds information about an "
            "already-identified item, typically set off by a comma). NOTE: no distinction "
            "exists in Global English - only apply this check for US-audience documents."
        ),
        source_reference="Style Guide p.64 (Word usage - that, which)",
    ),
    Rule(
        rule_id="usage-home-in-hone-in",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.LEXICAL,
        # Unconditional per source: "Misuse hone in as a verb" - the
        # correct verb form is always "home in on".
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("hone in",),
        description="'Hone in' is a misuse - the correct phrase is 'home in on' (to move toward a target with accuracy). 'Hone' means to sharpen a skill.",
        alternative="home in on",
        example_before="Our team will hone in on risks.",
        example_after="Our team will home in on risks.",
        source_reference="Style Guide p.53 (Word usage - home in vs. hone in)",
    ),
)

_word_usage_compliance_and_brand = (
    Rule(
        rule_id="usage-final-solution-banned",
        category=RuleCategory.BRAND_VOICE,
        detection_type=DetectionType.LEXICAL,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("final solution",),
        description="Never use the phrase 'final solution' in any document - it echoes the Nazi program of the same name.",
        alternative="solution, or replace 'final' with another adjective",
        explanation="A well-known historical association makes this phrase never appropriate, regardless of intent.",
        source_reference="Style Guide p.50 (Word usage - final solution)",
    ),
    Rule(
        rule_id="usage-and-or-avoid",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("and/or",),
        description="Avoid 'and/or' - it can usually be replaced with just 'and' or 'or', or reframed as 'X or Y or both'.",
        alternative='"or", "and", or "X or Y or both"',
        example_before="Chair or board independence is associated with lower fees and/or higher returns.",
        example_after="Chair or board independence is associated with lower fees or higher returns or both.",
        source_reference="Style Guide p.40 (Word usage - and/or)",
    ),
    Rule(
        rule_id="usage-as-necessary-required-needed",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.LEXICAL,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("as necessary", "as required", "as needed"),
        description="These phrases are superfluous filler - avoid using them.",
        alternative="delete entirely",
        source_reference="Style Guide p.40 (Word usage - as necessary, as required, as needed)",
    ),
    Rule(
        rule_id="usage-in-order-to-for",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("in order to", "in order for"),
        description="'In order to'/'in order for' can usually be reduced to just 'to'/'for'.",
        alternative='"to" or "for"',
        example_before="We are here in order to help you succeed.",
        example_after="We are here to help you succeed.",
        source_reference="Style Guide p.54 (Word usage - in order to, in order for)",
    ),
    Rule(
        rule_id="usage-firm-capitalization-and-meaning",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("Firm", "PwC Inc", "PwC Strategy& Inc"),
        description=(
            "Do not capitalize 'firm'/'one firm'/'firmwide' (unless starting a sentence) when "
            "referring to a PwC member firm. 'Firm' is not a synonym for 'company'/'corporation' "
            "when describing OTHER entities (e.g. IBM is a company, never a 'firm' in our usage). "
            "Never write 'PwC Inc.' or 'PwC Strategy& Inc.' - PwC is not incorporated."
        ),
        explanation="Legally significant: inaccurate descriptions of the PwC network structure carry real risk, not just a style preference.",
        source_reference="Style Guide p.50-51 (Word usage - firm)",
    ),
    Rule(
        rule_id="usage-singular-they-preferred",
        category=RuleCategory.AUDIENCE_SENSITIVITY,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("he/she", "he or she", "his/her", "his or her"),
        description="PwC guidance explicitly prefers singular 'they' over 'he/she' or 'his/her' when referring to one person - it's gender-neutral and matches the firm's conversational tone.",
        alternative="they/their (singular)",
        example_before="The client was very happy with our service. He or she was particularly pleased with how we kept in touch.",
        example_after="The client was very happy with our service. They were particularly pleased with how we kept in touch.",
        source_reference="Style Guide p.52-53 (Word usage - he/she/they)",
    ),
)

_word_usage_hyphenate_as_modifier = (
    Rule(
        rule_id="usage-hyphenate-only-as-compound-modifier",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.JUDGMENT,
        # ONE consolidated rule covering a recurring pattern across
        # ~15 separate Word-usage entries, rather than one rule per
        # term - the check logic is identical for all of them ("is
        # this word used as a modifier directly before a noun? If
        # so, hyphenate; otherwise, two/three words"), only the
        # specific term changes. trigger_terms lists every base term
        # confirmed to follow this exact pattern in the source.
        applies_to=AppliesTo.GENERAL,
        trigger_terms=(
            "back office", "back-office", "decision making", "decision-making",
            "end user", "end-user", "follow up", "follow-up", "front office", "front-office",
            "go to market", "go-to-market", "long term", "long-term", "short term", "short-term",
            "speed to market", "speed-to-market", "speed to value", "speed-to-value",
            "third party", "third-party", "up front", "up-front", "up to date", "up-to-date",
            "value added", "value-added", "year end", "year-end",
        ),
        description=(
            "Several terms should be hyphenated ONLY when used as a compound modifier "
            "directly before a noun, and written as separate words otherwise (e.g. "
            "'back-office personnel' but 'tasks performed in the back office'; 'the CEO has "
            "final decision-making authority' but 'responsible for final decision making'; "
            "'a value-added service' but spelled out otherwise). Check whether the term is "
            "being used adjectivally before a noun."
        ),
        explanation="A recurring, consistent hyphenation pattern across many Word-usage entries in the style guide.",
        source_reference="Style Guide p.40-67 (Word usage, multiple entries - see e.g. p.43 back office, p.47 decision making, p.48 end user, p.51 follow up, p.52 front office/go to market, p.56 long term, p.63 short term/speed to market, p.64 speed to value/third party, p.65 up front/up to date/value-added, p.67 year end)",
    ),
)

_word_usage_bans = (
    Rule(
        rule_id="usage-war-room-banned",
        category=RuleCategory.BRAND_VOICE,
        detection_type=DetectionType.LEXICAL,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("war room",),
        description="'War room' as a section divider/organizing phrase is discouraged in current usage.",
        alternative='"Parking lot" or "Slideyard"',
        source_reference="Style Guide p.65 (Word usage - War room)",
    ),
    Rule(
        rule_id="usage-graveyard-banned",
        category=RuleCategory.BRAND_VOICE,
        detection_type=DetectionType.LEXICAL,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("graveyard",),
        description="'Graveyard' as a section divider between final and archived slides is discouraged in current usage.",
        alternative='"Parking lot" or "Slideyard"',
        source_reference="Style Guide p.52 (Word usage - Graveyard)",
    ),
    Rule(
        rule_id="usage-why-pwc-banned",
        category=RuleCategory.BRAND_VOICE,
        detection_type=DetectionType.LEXICAL,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("Why PwC?", "Why PwC"),
        description="Do not use 'Why PwC?' as a heading/phrase in client-facing documents of any kind.",
        alternative='Rephrase to focus on client benefit, e.g. "What you\'ll achieve with PwC"',
        source_reference="Style Guide p.67 (Word usage - Why PwC?)",
    ),
)

_word_usage_versus_who_whom = (
    Rule(
        rule_id="usage-versus-vs-context",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("versus", "vs.", " vs "),
        description=(
            "Avoid using 'versus'/'vs.' to describe a comparison in running prose - rephrase "
            "instead. If genuinely needed, spell out 'versus' in prose; use the abbreviated "
            "'vs.' (with period) ONLY in charts, tables, headings, titles, or when referring "
            "to court cases."
        ),
        source_reference="Style Guide p.65 (Word usage - versus or vs.)",
    ),
    Rule(
        rule_id="usage-who-whom",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("who", "whom"),
        description=(
            "Use 'who' when 'he/she/they/I/we' could substitute in the clause; use 'whom' "
            "when 'him/her/them/me/us' could substitute (commonly after a preposition like "
            "to, for, of)."
        ),
        example_before="To who did you direct your question?",
        example_after="To whom did you direct your question?",
        source_reference="Style Guide p.66-67 (Word usage - who, whom)",
    ),
)

_word_usage_pick_one_form_consistently = (
    Rule(
        rule_id="usage-pick-one-form-consistently",
        category=RuleCategory.CONSISTENCY,
        detection_type=DetectionType.JUDGMENT,
        # ONE consolidated CONSISTENCY rule covering a recurring
        # pattern: the source explicitly says "opt for consistency"
        # or "be consistent" for several terms with multiple
        # acceptable forms, rather than one document-level rule per
        # term.
        applies_to=AppliesTo.GENERAL,
        description=(
            "Several terms have multiple acceptable forms, and the style guide says to pick "
            "ONE and use it consistently throughout a document, rather than mixing forms: "
            "24x7 / 24/7 / 365x24x7; Internet / internet; on-premise / on-premises; "
            "next generation / next-generation / next gen / next-gen; hyperconverge / "
            "hyper-converge; life cycle / life-cycle / lifecycle; healthcare / health care "
            "(follow client usage if known); deliverable names capitalized or lowercase; "
            "walkthrough / walk-through; web / Web. "
            "Flag if the SAME variable-form term appears in more than one form across the "
            "document."
        ),
        explanation="Mixed usage of an acceptable-either-way term across one document reads as unedited.",
        source_reference="Style Guide p.38 (24x7), p.53 (healthcare), p.54 (hyperconverge), p.55 (Internet), p.55-56 (life cycle), p.58 (next generation), p.59 (on-premise), p.47 (deliverables), p.65 (walkthrough), p.66 (web)",
    ),
)

_word_usage_deterministic_terms = (
    # --- Simple "correct form X, not Y" lookups from Word usage
    # (US) - genuinely deterministic, kept terse since the source
    # gives little beyond the correct spelling/form itself. ---
    Rule(
        rule_id="usage-basel-roman-numerals",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Use Roman numerals for Basel accords: Basel I, II, III - not Basel 1, 2, 3.",
        pattern=r"\bBasel\s+[123]\b",
        alternative="Basel I, II, III",
        source_reference="Style Guide p.44 (Word usage - Basel I, II, and III)",
    ),
    Rule(
        rule_id="usage-salesforce-no-dotcom",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="The company dropped '.com' from its name - use 'Salesforce', not 'Salesforce.com'.",
        pattern=r"\bSalesforce\.com\b",
        alternative="Salesforce",
        source_reference="Style Guide p.61 (Word usage - Salesforce)",
    ),
    Rule(
        rule_id="usage-jira-capitalization",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Use 'Jira', not the all-caps 'JIRA'.",
        pattern=r"\bJIRA\b",
        alternative="Jira",
        source_reference="Style Guide p.54 (Word usage - Jira)",
    ),
    Rule(
        rule_id="usage-chatbot-one-word",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Use 'chatbot' as one word, not 'chat bot'.",
        pattern=r"\bchat bot\b",
        alternative="chatbot",
        source_reference="Style Guide p.44 (Word usage - chatbot)",
    ),
    Rule(
        rule_id="usage-irs-possessive",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="IRS is singular (the 's' stands for 'Service') - form the possessive as \"IRS's\", not \"IRS'\".",
        pattern=r"\bIRS'(?!s)",
        alternative="IRS's",
        explanation="IRS' incorrectly implies a plural possessive, suggesting more than one agency.",
        source_reference="Style Guide p.54 (Word usage - Internal Revenue Service)",
    ),
    Rule(
        rule_id="usage-ect-typo",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="'ect.' is never correct - it's a common typo for 'etc.'.",
        pattern=r"\bect\.",
        alternative="etc.",
        source_reference="Style Guide p.49 (Word usage - etc.)",
    ),
    Rule(
        rule_id="usage-check-the-box-not-boxes",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Use 'check-the-box approach', not 'check-the-boxes approach'.",
        pattern=r"\bcheck-the-boxes\b",
        alternative="check-the-box",
        source_reference="Style Guide p.44 (Word usage - check-the-box approach)",
    ),
    Rule(
        rule_id="usage-one-stop-shop-not-numeral",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Use 'one-stop shop', not '1-stop shop'.",
        pattern=r"\b1-stop shop\b",
        alternative="one-stop shop",
        source_reference="Style Guide p.58 (Word usage - one-stop shop)",
    ),
    Rule(
        rule_id="usage-person-hours-not-man-hours",
        category=RuleCategory.AUDIENCE_SENSITIVITY,
        detection_type=DetectionType.DETERMINISTIC,
        applies_to=AppliesTo.GENERAL,
        description="Use 'person-hours', not 'man-hours' - confirmed specifically here as the preferred term (distinct from, and more specific than, the general gender-neutral 'man hours -> worker/people hours' guidance from Appendix C).",
        pattern=r"\bman-hours\b",
        alternative="person-hours",
        source_reference="Style Guide p.60 (Word usage - person-hours)",
    ),
)


_global_english_mixing_check = (
    Rule(
        rule_id="global-english-spelling-mixing",
        category=RuleCategory.CONSISTENCY,
        detection_type=DetectionType.JUDGMENT,
        english_variant=None,
        # Applies regardless of target variant, DELIBERATELY - this
        # check doesn't need to know which locale the document
        # targets, only that it shouldn't mix BOTH within one
        # document (e.g. using both "color" and "colour" somewhere
        # in the same proposal). This sidesteps the intake-question
        # dependency entirely and is safe to run today.
        applies_to=AppliesTo.GENERAL,
        description=(
            "US and Global (UK) English use different spellings for many common words. "
            "A single document should use ONE spelling convention consistently, not mix "
            "both. Flag if the SAME word-pair appears in both its US and Global spelling "
            "somewhere in the document. Reference pairs (US / Global): color/colour, "
            "organize/organise, analyze/analyse, license/licence, program/programme, "
            "realize/realise, recognize/recognise, specialty/speciality, favor/favour, "
            "labor/labour, center/centre, defense/defence, judgment/judgement, "
            "behavior/behaviour, catalog/catalogue, criticize/criticise, finalize/finalise, "
            "materialize/materialise, modeled-modeling/modelled-modelling, "
            "neighbor/neighbour, normalize/normalise, offense/offence, "
            "traveler-traveling/traveller-travelling, skillful/skilful, "
            "specialize/specialise, aluminum/aluminium, gray/grey, jewelry/jewellery, "
            "meter/metre, liter/litre, lawyer-attorney/solicitor, elevator/lift, "
            "vacation/holiday, math/maths, windshield/windscreen, sidewalk/pavement, "
            "truck/lorry, gas-gasoline/petrol, movie theater/cinema."
        ),
        trigger_terms=(
            "color", "colour", "organize", "organise", "analyze", "analyse",
            "license", "licence", "program", "programme", "realize", "realise",
            "recognize", "recognise", "favor", "favour", "labor", "labour",
            "center", "centre", "defense", "defence", "judgment", "judgement",
            "behavior", "behaviour", "catalog", "catalogue", "traveler", "traveller",
        ),
        explanation="Mixed US/Global spelling within one document reads as unedited, regardless of which convention the document is ultimately meant to follow.",
        source_reference="Style Guide p.68-71 (Global English - Global/US English spelling and usage differences)",
    ),
)

_global_english_variant_specific = (
    # --- These rules are GENUINE OPPOSITES between US and Global
    # English - correct for exactly one variant, incorrect for the
    # other. All tagged english_variant=GLOBAL, meaning nothing in
    # the engine's default call path (english_variant defaults to US)
    # selects them - they are POPULATED and READY, not yet ACTIVE.
    # See Rule.english_variant's docstring in schema.py and the
    # engine's own docstring for why this gating exists and what's
    # still needed (a real intake question) to actually activate
    # them for a confirmed Global-English document. ---
    Rule(
        rule_id="global-agree-no-preposition",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        english_variant=EnglishVariant.GLOBAL,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("agreed on", "agreed to", "agree on", "agree to"),
        description="In Global English, 'agree' (intransitive) is NOT followed by a preposition - unlike US English, which requires one (e.g. 'agreed on terms').",
        example_before="The two companies agreed on terms for the merger.",
        example_after="The two companies agreed terms for the merger.",
        source_reference="Style Guide p.75 (Global English - Agree)",
    ),
    Rule(
        rule_id="global-collective-noun-plural-verb",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        english_variant=EnglishVariant.GLOBAL,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("team", "government", "firm", "family", "committee"),
        description=(
            "In Global English, collective nouns (team, government, firm, family) more "
            "often take a PLURAL verb than in US English, especially when the group is "
            "acting as individuals doing personal things (deciding, hoping, wanting) - use "
            "'who' as the relative pronoun in that case. When the group is an impersonal "
            "unit, singular is still more common, with 'which' as the relative pronoun. "
            "When a group noun follows a singular determiner (a, each, every, this, that), "
            "singular is normal regardless."
        ),
        example_before="The government, which are hoping to ease export restrictions...",
        example_after="The government, who are hoping to ease export restrictions...",
        source_reference="Style Guide p.75-76 (Global English - Collective or non-count nouns)",
    ),
    Rule(
        rule_id="global-date-time-format",
        category=RuleCategory.NUMBERS_FORMATTING,
        detection_type=DetectionType.JUDGMENT,
        english_variant=EnglishVariant.GLOBAL,
        applies_to=AppliesTo.GENERAL,
        description=(
            "For a global audience, present dates in day/month/year order (e.g. '20 "
            "February 2015'), not month/day/year. Present times as e.g. '4pm' (no space, "
            "no periods) and '4.30pm' (period, not colon, before minutes)."
        ),
        example_before="February 20, 2015; the meeting is at 4:30pm.",
        example_after="20 February 2015; the meeting is at 4.30pm.",
        source_reference="Style Guide p.76 (Global English - Dates/Times)",
    ),
    Rule(
        rule_id="global-that-which-no-distinction",
        category=RuleCategory.GRAMMAR,
        detection_type=DetectionType.JUDGMENT,
        english_variant=EnglishVariant.GLOBAL,
        applies_to=AppliesTo.GENERAL,
        description="Global English makes no restrictive/nonrestrictive distinction between 'that' and 'which' - unlike US English's usage-confusables rule (usage-that-which), do NOT flag that/which choice for a Global-English document.",
        explanation="This is the Global-variant counterpart that suppresses the US-only that/which rule, not an additional check.",
        source_reference="Style Guide p.76 (Global English - that, which)",
    ),
    Rule(
        rule_id="global-pronounceable-acronym-title-case",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.JUDGMENT,
        english_variant=EnglishVariant.GLOBAL,
        applies_to=AppliesTo.GENERAL,
        trigger_terms=("NASA", "UNICEF", "ICE"),
        description="In Global English, an acronym that forms a pronounceable word is often written in title case rather than all caps (e.g. 'Nasa' not 'NASA', 'Unicef' not 'UNICEF').",
        example_before="NASA announced the launch date.",
        example_after="Nasa announced the launch date.",
        source_reference="Style Guide p.76 (Global English - Acronyms and abbreviations)",
    ),
    Rule(
        rule_id="global-no-comma-after-abbreviation",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.DETERMINISTIC,
        english_variant=EnglishVariant.GLOBAL,
        applies_to=AppliesTo.GENERAL,
        description="In Global English, no comma follows 'i.e.'/'e.g.' - unlike US English, which requires one.",
        pattern=r"\b(?:i\.e\.|e\.g\.),",
        example_before="Several financial incentives were offered (e.g., government aid programmes).",
        example_after="Several financial incentives were offered (e.g. government aid programmes).",
        source_reference="Style Guide p.76 (Global English - Comma)",
    ),
    Rule(
        rule_id="global-no-serial-comma-default",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.JUDGMENT,
        english_variant=EnglishVariant.GLOBAL,
        applies_to=AppliesTo.GENERAL,
        description="Global English generally does NOT use the serial (Oxford) comma by default - opposite of this firm's own US default (which deliberately DOES use it). Follow whichever style the specific document/team is already using and flag only genuine inconsistency within the document, not the mere absence of a serial comma.",
        source_reference="Style Guide p.76 (Global English - Serial comma)",
    ),
    Rule(
        rule_id="global-quote-marks-swapped",
        category=RuleCategory.PUNCTUATION,
        detection_type=DetectionType.JUDGMENT,
        english_variant=EnglishVariant.GLOBAL,
        applies_to=AppliesTo.GENERAL,
        description=(
            "UK/Global English uses single quotes where US English uses double quotes, "
            "and vice versa for a nested quote-within-a-quote. End punctuation is placed "
            "AFTER the closing quotation mark in Global English (opposite of US convention)."
        ),
        source_reference="Style Guide p.77 (Global English - Quotation marks)",
    ),
)

RULE_SET = RuleSet(
    rules=(
        _grammar_rules
        + _deterministic_mechanical_rules
        + _audience_sensitivity_gender_neutral
        + _audience_sensitivity_other
        + _risk_language_general
        + _risk_language_audit_specific
        + _word_usage_confusables
        + _word_usage_compliance_and_brand
        + _word_usage_hyphenate_as_modifier
        + _word_usage_bans
        + _word_usage_versus_who_whom
        + _word_usage_pick_one_form_consistently
        + _word_usage_deterministic_terms
        + _global_english_mixing_check
        + _global_english_variant_specific
    )
)

RULE_SET.validate()
# Startup-time validation - catches curator errors (inconsistent
# DetectionType/pattern/trigger_terms combos, duplicate rule_ids,
# missing citations) at import/deploy time, not silently on the
# first real document. See RuleSet.validate()'s own docstring.