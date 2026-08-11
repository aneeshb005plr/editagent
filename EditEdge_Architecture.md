# EditEdge — Production Architecture

**Status:** Living document, updated as we build. Parsing layer (all four formats + image/chart extraction) is built and verified. Rules taxonomy is built and verified. Review engine is next. Channel integration, job infrastructure, and the conversational shell are still ahead — this doc's earlier sections on those remain design-stage, not yet built.

---

## 1. What this is building

A conversational AI agent, exposed through Teams and Microsoft 365 Copilot, that reviews Word/PowerPoint/Excel/PDF pursuit documents (up to ~100MB) for grammar, style, PwC style-guide compliance, and restricted/risky language — returning structured, categorized findings with suggested rewrites. Advisory-only; no automatic edits.

---

## 2. Guiding principles carried forward

1. **Infrastructure is reusable; the workflow is not.** Auth, DB connection, and config patterns copy from the existing reusable-infra layer. The LangGraph itself — state, nodes, routing — is designed fresh for this agent's actual behavior.
2. **The review engine is a capability, not graph logic.** Parsing → rule evaluation → findings is a clean, standalone module (`app/documents/`, `app/rules/`, and the forthcoming `app/review/`), callable independently of the chat shell — so a future Office add-in or other consumer can call the same engine without the conversational layer.
3. **Empirical verification before commitment — this has been the single most consequential discipline in the build so far.** Every non-trivial claim about a library or API has been checked against real, installed code rather than assumed, and this has repeatedly mattered: the async Mongo checkpointer situation, the `pymupdf4llm`-triggers-Tesseract discovery, and — most productively — two full rounds of external code review where roughly half the flagged "bugs" turned out to be incorrect on direct inspection (wrong URI claims, a disproven `Movie subclasses Picture` claim, a `media_type` fix that doesn't actually work in this library version) while the other half were real, reproducible, and got fixed. Every fix in this document was verified against a real fixture, not just reasoned about.
4. **Deterministic before LLM, always.** Every rule category splits into cheap lookup-based checks and LLM-judgment checks. This is no longer a design intention — the taxonomy (Section 6) has been built and every deterministic rule's regex has been tested against real matching/non-matching text (not just confirmed to compile), which itself caught a real bug (a trailing `\b` word-boundary regex that silently failed to match "U.S." — see Section 6).

---

## 3. High-level architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Channels (via Microsoft 365 Agents SDK — one backend, N channels)│
│    Teams (1:1 personal scope)      M365 Copilot chat                │
└───────────────────────────┬───────────────────────────────────────┘
                             │ Activities (messages, attachments)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  EditEdge Agent Service (FastAPI + Microsoft 365 Agents SDK)        │
│  ┌───────────────┐  ┌────────────────────┐  ┌──────────────────┐   │
│  │ Conversational │  │  Review Engine      │  │ Job Orchestration │   │
│  │ Shell           │→│  (own module)        │→│  Client            │   │
│  │ (LangGraph)      │  │  parse→rules→findings│  │  enqueue/status    │   │
│  └───────────────┘  └────────────────────┘  └──────────────────┘   │
└───────────────────────────┬───────────────────────────────────────┘
                             │ job records (Mongo)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Background Review Worker(s) — scalable, independent process(es)    │
│   claim job → parse → chunked/batched rule evaluation → findings    │
│   → write results → proactively notify via stored conversation ref  │
└─────────────────────────────────────────────────────────────────┘
```

**Built so far, inside the "Review Engine" box**: `app/documents/` (all four parsers + dispatcher + image extraction + pipeline) and `app/rules/` (taxonomy schema + curated content). Not yet built: the engine itself (`app/review/`), job orchestration, the LangGraph shell, and channel integration.

---

## 4. Data model (MongoDB) — design stage, not yet built

| Collection | Keyed by | Purpose |
|---|---|---|
| `sessions` | `_id`, owned by `user_id` | Conversation-adjacent metadata |
| `checkpoints` / `checkpoint_writes` | `thread_id` | LangGraph conversational state |
| `review_jobs` | `job_id`, **`user_id`** | Job status, `conversation_reference`, file metadata, timestamps |
| `findings` | `job_id` | Structured output: category, location, original text, explanation, suggested rewrite, `rule_id` |
| `knowledge_chunks` | — | Optional, secondary: vectorized style-guide content for `answer_from_knowledge`-style Q&A only |

**Correction from the original draft**: the rules taxonomy is **not** a MongoDB collection. It's built as static, code-authored Python (`app/rules/schema.py` + `app/rules/taxonomy.py`) — matching the already-confirmed decision that rule curation is static for MVP (code change + redeploy, not admin-managed). See Section 6.

**One active job per user, enforced at the `review_jobs` layer**, checked across all conversations.

---

## 5. LangGraph design — design stage, not yet built

**State includes**, beyond the standard `messages`/`session_id`/`user_id`:
- `intent` — classified every turn, job-state-aware
- `active_job_id` / `job_status` — read from `review_jobs`, not duplicated into the checkpoint
- `document_type` — `"general" | "audit"`, captured once at intake, drives which rules apply (see Section 6 — this is now a concrete, confirmed-real distinction, not a hypothetical)

**Intent taxonomy** (draft): `social | off_topic | knowledge_question | submit_document | check_status | finding_followup | scope_change | new_document | additional_output | unclear`

**Hard safety override, non-negotiable**: while a job is `running`, `submit_document`/`new_document` intents never silently cancel it.

**Two completion paths**: proactive push (primary, via stored `conversation_reference`) and status check (fallback). Verify both work identically across Teams/M365 Copilot before relying on either as primary.

---

## 6. Rules taxonomy — BUILT AND VERIFIED

Lives in `app/rules/`, not MongoDB (see Section 4 correction). Two files:

**`app/rules/schema.py`** — the data model. A `Rule` is a frozen dataclass (same reasoning as `app/documents/base.py`: static, code-authored data, no Pydantic needed at this layer) with:
- `category`: `grammar | punctuation | capitalization | numbers_formatting | risk_language | audience_sensitivity | brand_voice | consistency` — `consistency` is document-level only (terminology drift, duplicate content), handled by a separate pass in the engine, not the per-block passes.
- `detection_type`: `deterministic | judgment`
- `applies_to`: `general | audit` — **confirmed real and conflicting**, not a hypothetical: Appendix B contains an audit/Trust Solutions overlay where words like "assist," "collaborate," and "chemistry" are unrestricted generally but explicitly restricted in audit proposals. Resolved once per review by asking the user directly at intake, not auto-detected.
- `trigger_terms`: a cheap keyword pre-filter for judgment rules keyed to specific words (most of Appendix B) — a block containing none of a rule's trigger terms skips that rule's LLM check entirely; containing one still requires the LLM to judge whether it's used in the restricted *sense*. This is a real cost-control mechanism, not a contradiction of "judgment needs the LLM."
- `source_reference`: a plain citation string (e.g. `"Style Guide p.89 (Appendix B)"`) citing the source document's own **printed** page numbers — deliberately never derived from any parser's `page_number`/`paragraph_index`, since a parser's page count is physical document order and can diverge from what's actually printed on a page (cover pages, TOCs, restarted numbering). This keeps taxonomy citations verifiable by a human against the real document, independent of parser internals.

**`app/rules/taxonomy.py`** — the actual curated content. **56 rules currently**, built from real source material (Grammatical Topics.docx in full, plus the style guide sections received so far: grammar/punctuation, Numbers, Appendix B including the audit overlay, Appendix C gender-neutral terms, and the brand messaging guide's literal-word restriction). Breakdown: 15 deterministic, 41 judgment; 47 general rules, 9 audit-specific additions.

**Deliberately not exhaustive**: Word usage (US) and Global English sections remain thin in the source material received so far. Adding more rules later is purely additive to `taxonomy.py` — it requires no change to the review engine, which consumes whatever rules exist without caring how many there are.

**Verification discipline applied and validated**: every deterministic rule's regex was tested against real matching and non-matching text, not just confirmed to compile. This caught a real bug — `punc-acronym-no-periods`'s original pattern had a trailing `\b` that silently failed to match "U.S." (a period followed by a space has no word-boundary transition, since both characters are non-word characters) — fixed and reverified. This is now the standing convention for any future rule additions: compiling is not sufficient evidence a pattern works.

**Runtime execution** (for the forthcoming review engine): deterministic rules run first, on every block, cheaply. Judgment rules run only for blocks matching at least one `trigger_terms` keyword (where the rule has any), batched into LLM calls rather than one call per rule per block. The `consistency` category and any document-level concern get a separate pass over the whole document's extracted terms after per-block passes complete, not folded into per-block execution.

---

## 7. Document processing — BUILT AND VERIFIED

**Formats supported**: Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`, all tabs), PDF. ODP explicitly out of scope.

**Module layout** (`app/documents/`):
- `base.py` — common representation (`ParsedDocument`, `ContentBlock`, `Location`, `UnsupportedItem`) every parser produces. Plain dataclasses, not Pydantic — high-volume, hot-path objects (a 100MB document can produce thousands of instances), validated only where they cross a real boundary later.
- `docx_parser.py`, `pptx_parser.py`, `xlsx_parser.py`, `pdf_parser.py` — one parser per format, verified against real library source and real generated fixtures, not assumed.
- `dispatcher.py` — single entry point, routes by extension, enforces `MAX_FILE_SIZE_MB` centrally.
- `image_extraction.py` — turns flagged images into reviewable text via a vision-capable LLM call (LangChain `ChatOpenAI`, not the raw `openai` SDK — matches the rest of the codebase's LLM-call convention).
- `pipeline.py` — the async orchestration layer connecting the sync dispatcher to the async image-extraction step (`parse_and_extract()`); this is the actual entry point downstream consumers should use, not `dispatcher.parse_document()` directly, unless deliberately skipping image review.

**Images and charts are reviewed, not just flagged** — this supersedes the original draft's "unreviewable content, explicitly flagged as not reviewed" framing. Concretely:
- **Images**: text embedded in an image is extracted via a vision LLM call and reviewed like any other text, across all four formats. A generic fallback icon (PowerPoint's default "media loudspeaker" placeholder for audio/video with no real poster frame) is detected by direct byte comparison against the library's own known constant and excluded from review rather than sent through vision extraction as if it were meaningful content.
- **Charts**: labels (title, series names, category labels) are extracted directly via each format's own chart API where available (PowerPoint has this; Word and Excel do not, flagged-only for chart *content* in those formats) and reviewed as text. The underlying chart *data/visual layout* remains out of scope in all formats.
- **SmartArt**: still flagged-only, still unverified by a real fixture in PowerPoint (the library has no API to create SmartArt for testing) — must be confirmed against a real SmartArt-containing file before being trusted in production.

**Real bugs found and fixed across two rounds of review** (verified via direct reproduction in every case, not accepted on the reviewer's word — several claimed "bugs" were disproven the same way):
- **docx**: nested tables (a table inside a table cell) were completely invisible via `doc.tables` — fixed by refactoring to `iter_inner_content()`, which also fixed paragraph/table interleaving order as a side effect. Merged cells were then found to be processed multiple times by the refactor (row.cells repeats the same underlying element per grid position spanned) — fixed via identity-based dedup. Floating/wrapped images (`wp:anchor`, not just `wp:inline`) were invisible — fixed. Images/OLE objects inside table cells were invisible — fixed. EMF/WMF images crashed the parser (python-docx's own image-format exceptions aren't `AttributeError` subclasses) — fixed with a raw-bytes fallback. Linked (not embedded) pictures were misreported as parse failures — now correctly identified. `mc:AlternateContent` (a real Word backward-compatibility pattern) could double-count the same image — deduped by relationship ID.
- **pptx**: populated picture placeholders (e.g. the "Picture with Caption" layout) were **silently dropped entirely** — `shape_type` reports `PLACEHOLDER`, not `PICTURE`, even when populated; fixed via an `isinstance(shape, Picture)` check, reproduced and verified against a real layout. A final "unhandled shape" safety net was added so nothing (movies, connectors, any future shape type) is ever silently dropped again. Movie/audio media was confirmed to already reach that safety net correctly (an initial review claim that it would crash or be mis-extracted was disproven by reading the actual library source — `Movie` and `Picture` are siblings, not parent/child) but was being mislabeled and discarding a real, extractable poster-frame image; fixed with a dedicated media branch.
- **xlsx**: `read_only=True` mode (required for memory efficiency at scale) doesn't expose charts/images at all — solved by reading the `.xlsx` zip container's relationship chain directly (`workbook.xml` → sheet rels → drawing rels → media), verified against real generated fixtures, not assumed from documentation. Hardened against a rich-text cell value crash and malformed relationship files.
- **pdf**: `page.get_images()` reports images merely *referenced* in a page's (possibly inherited) `/Resources` dictionary, not necessarily images actually *displayed* on that page — confirmed via pymupdf maintainer guidance, this could cause false "scanned page" flags and duplicate/mislocated image items. Fixed by switching to `page.get_image_info(xrefs=True)`, which reports only actually-rendered images with a real bounding box.

**Known, explicitly documented limitations** (not silently missing — see each parser's own docstring for the full list): headers/footers/footnotes/textboxes (docx), tracked-change deletions (docx), chartsheets and cell comments (xlsx), annotation-embedded images and vector-graphic charts (pdf), cross-page image deduplication (deferred to the future extraction/orchestration layer, not the parser).

**Page/location numbering caveat, worth stating plainly**: a parser's `page_number`/`paragraph_index` reflects physical document order as the parser counts it — it is not guaranteed to match a document's own printed page labels (a PDF with a cover page and TOC will have physical page 5 show a printed "Page 3," for instance). Findings should be understood as "document order," not a promise of matching a visible printed number.

**Not yet load-tested at 100MB** — every fixture used in verification so far has been small, synthetically generated. This remains the single largest unverified risk in the parsing layer and needs a real large-file spike before production commitment.

---

## 8. Channel integration — design stage, not yet built

**Framework**: Microsoft 365 Agents SDK for Python, custom engine agent tier (own orchestrator, SDK as channel layer only).

**Confirmed, real platform constraints**:
- Teams: file attachments work reliably, personal 1:1 scope only; can't be tested via local Playground/dev tunnel.
- M365 Copilot app channel: documented, currently-open known issue where file attachments can silently fail to reach a custom engine agent's backend — must be spiked against the real tenant before committing to it as primary.
- Fallback if confirmed broken: SharePoint/OneDrive link-based intake for that channel specifically.

**Azure Bot auth**: Managed Identity is the target for production (no stored secret); local dev/Teams-channel testing needs a temporary Client Secret + devtunnel, since Managed Identity only works when actually hosted on Azure infrastructure with the identity attached.

Consider `microsoft-agents-a365` for production observability/notifications.

---

## 9. Explicitly deferred (Phase 2+)

- **Marked-up/annotated output files, auto-apply edits** — the review engine returns structured findings independent of any output renderer; additive later.
- **Accept/reject tracking** — needs a `decision` field on findings; additive.
- **Admin-managed / self-service rule updates** — `app/rules/taxonomy.py` is already the right shape conceptually; Phase 2 would add an authoring UI/storage backend on top, not change the schema.
- **Region/LoS/proposal-type rule variation beyond general/audit** — `AppliesTo` already generalizes; extending it is additive.
- **Scanned-page OCR** — distinct from the image-text extraction already built (Section 7): a scanned page's *entire content* being run through OCR is still parked, flagged `NOT_APPLICABLE` for now pending an explicit decision.
- **Vector-graphic chart detection in PDFs** — flagged as a hard, unreliable heuristic problem in Section 7; not attempted.
- **Multi-document handling** — genuinely undecided even in concept; don't let it silently default to "blend everything."

---

## 10. Tech stack — versions confirmed against real installed packages, not memory

| Layer | Choice | Note |
|---|---|---|
| Python | 3.13 | Confirmed compatible with every dependency below via wheel-availability checks (all pure-Python or `abi3`/stable-ABI wheels) |
| API framework | FastAPI | — |
| Orchestration | LangGraph 1.2.10 | — |
| Checkpointer | `langgraph-checkpoint-mongodb` 0.4.0, `langgraph.checkpoint.mongodb.MongoDBSaver` (sync client + async wrapper methods) | Confirmed via direct package introspection: no dedicated async-native saver class exists in this release, despite some external docs implying otherwise — verified by installing and inspecting, not trusted from search results |
| DB driver | PyMongo native async (`AsyncMongoClient`) | Motor deprecated/EOL |
| GenAI client | `langchain-openai` 1.4.2 (`ChatOpenAI`) + `langchain-core` 1.5.3 | One shared client per app instance (`app/llm.py`), constructed once at startup and reused via `.bind()` for per-use-case parameters (e.g. image extraction's `temperature=0`) — NOT a fresh client per call, since `ChatOpenAI` wraps a real pooled HTTP connection. Confirmed current multimodal content-block format (`HumanMessage(content_blocks=[...])`) rather than the legacy OpenAI-style `image_url` dict, which this LangChain version has moved past |
| Document parsing | `python-docx` 1.2.0, `python-pptx` 1.0.2, `openpyxl` 3.1.5, `pymupdf` 1.28.2 | All confirmed current; `pymupdf4llm` present in requirements but deliberately unused in the core parser — triggers undeclared Tesseract OCR as a side effect, confirmed during verification |
| XML parsing | `lxml` 6.1.1 | Used directly for xlsx's zip-container relationship-chain reading and docx's raw graphic-element inspection |
| Teams/Copilot channel layer | Microsoft 365 Agents SDK for Python | Not yet integrated |
| Background job execution | Not yet decided | Candidates: Celery/arq/taskiq, or a bespoke poll-loop matching `knowledge-sync-worker`'s proven pattern |

---

## 11. Production-grade concerns to build in from day one

- **Idempotency on job processing** — not yet built (job infrastructure is ahead).
- **Per-user rate/concurrency limiting** against the shared GenAI service — real, still-open concern; `image_extraction.py`'s `max_concurrent` parameter (default 5, explicitly flagged as an untuned guess) is the first place this will need real tuning once cost/latency data exists.
- **Cost observability** — not yet built.
- **Verification-before-trust as a coding discipline** — this has become a de facto production-grade practice over the course of this build: every library API claim gets checked against the real installed package (via sandbox introspection or fixture testing) before code is written against it, and every fix proposed by external review gets independently reproduced before being accepted. This has caught real bugs that would otherwise have shipped, and also prevented several unnecessary "fixes" for claims that didn't hold up under direct testing.

---

## 12. Still-open items

1. Real ~100MB test files (Word-heavy, PPT-heavy, Excel-heavy) — parser and worker behavior still completely unverified at scale. Highest-priority open item.
2. The real GenAI endpoint's vision-call behavior — `image_extraction.py`'s logic is verified via mocking only; the actual `GENAI_BASE_URL`/`GENAI_API_KEY` endpoint has never been called.
3. File-upload spike: Teams vs. M365 Copilot custom-engine-agent attachment delivery.
4. Proactive-messaging spike across both channels.
5. Acceptable turnaround-time target for a full large-file review.
6. Word usage (US) and Global English sections — still thin; affects taxonomy completeness, not the engine's design.
7. SmartArt detection (pptx) — implemented per documented namespace spec, never confirmed against a real SmartArt-containing file.
8. Job-queue library decision — deferred to when job infrastructure is actually built.
9. Excel chart/image detection during the large-file spike specifically — the zip-relationship-chain approach was verified on small fixtures only.

---

**Next step:** the review engine (`app/review/`) — models, deterministic rule runner, judgment/LLM rule runner, and the orchestrating engine that ties the taxonomy (Section 6) to parsed documents (Section 7) and produces `Finding` objects.
