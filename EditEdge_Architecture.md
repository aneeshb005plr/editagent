# EditEdge — Production Architecture

**Status:** Living document — single source of truth for this app, alongside `EditEdge_Testing.md`. Built and verified: document parsing (all 4 formats + image/chart extraction), rules taxonomy (133 rules), review engine (4 detection passes), job infrastructure (durable, concurrent), REST API layer, dual-mode auth, Streamlit dev client. Not yet built: the LangGraph conversational shell, Teams/M365 Copilot channel integration, 100MB-scale validation.

---

## 1. What this is building

A conversational AI agent, exposed through Teams and Microsoft 365 Copilot, that reviews Word/PowerPoint/Excel/PDF pursuit documents (up to 100MB) for grammar, style, PwC style-guide compliance, and restricted/risky language — returning structured, categorized findings with suggested rewrites. Advisory-only; no automatic edits.

**Confirmed important scope boundary**: the REST API described in Section 10 is a **dev/testing surface** (used by the Streamlit client and direct API testing), not the production interface. The real client is Teams, which will get its own endpoint later (Bot Framework Activity handler via the Microsoft 365 Agents SDK) that calls the same underlying `app/jobs/service.py` functions **directly, in-process** — not by round-tripping through this REST API. This is exactly why the real logic lives in `app/jobs/service.py` and not in route handlers: both surfaces can call the same functions without duplicating logic.

---

## 2. Guiding principles carried forward

1. **Infrastructure patterns are reusable; the workflow is not.** Job-queue patterns (poll loop, heartbeat, stale-job requeue) are adapted from `knowledge-sync-worker`'s proven design — but EditEdge's job system is meaningfully **simpler**, not a smaller version of the same complexity: no agent registry, no per-agent DB routing, no admin API, because EditEdge is single-agent where `knowledge-sync-worker` is deliberately multi-agent shared infrastructure.
2. **The review engine is a capability, not graph logic.** `app/documents/` → `app/rules/` → `app/review/` is a clean, standalone pipeline, callable independently of any conversational layer — confirmed by the job worker (`app/jobs/worker.py`) calling it directly with zero LangGraph dependency.
3. **Empirical verification before commitment — the single most consequential discipline in this build.** Every non-trivial library/API claim has been checked against real, installed code or real request/response behavior, never assumed. This has repeatedly mattered — see `EditEdge_Testing.md` for the full, specific list of real bugs this caught, including several found only after the code was run against the real GenAI endpoint and real MongoDB-shaped test data, not just unit tests.
4. **Deterministic/lexical before LLM, always.** Rules split into three execution tiers by cost: `DETERMINISTIC` (regex) and `LEXICAL` (bounded term match) run first, free, always; `JUDGMENT` (LLM) runs only where a cheap pattern can't decide. `CONSISTENCY`-category rules get a fourth, structurally separate document-level pass. All four are real, running code — not a design intention.
5. **Don't build ahead of confirmed need.** Concrete instance: the consistency pass's extract-then-adjudicate redesign (suggested during external review) was deliberately deferred — it trades real context away for a scaling problem that hasn't been confirmed real yet (no 100MB test file exists). Revisit only with real evidence the current design is actually a bottleneck.

---

## 3. High-level architecture

```
Dev/testing clients: Streamlit (streamlit_app/app.py), direct API
Future production client: Teams (separate endpoint, not yet built)
        |
        | HTTP (multipart upload, status polling)
        v
FastAPI app
  app/auth/ (dual-mode)  ->  app/api/v1/documents.py (thin routes)  ->  app/jobs/service.py
        |
        | review_jobs (Mongo) + GridFS (raw file bytes)
        v
app/jobs/worker.py — N concurrent "slots" (settings.MAX_CONCURRENT_JOBS)
  each slot: claim (lock-serialized) -> process -> repeat, independently
  claim excludes users with an already-RUNNING job (real, tested)
        |
        v
app/documents/pipeline.py (parse_and_extract)  ->  app/review/engine.py (review_document)
  4 parsers + vision OCR                            deterministic -> lexical -> judgment -> consistency
        |
        v
findings (Mongo) + review_jobs status update
```

---

## 4. Data model (MongoDB) — REAL, implemented

| Collection | Keyed by | Purpose |
|---|---|---|
| `review_jobs` | `_id`, `user_id` | Job status, applies_to/is_pcs/english_variant intake answers, timestamps, heartbeat, `gridfs_file_id` reference |
| `findings` | `job_id` | One document per `Finding` — category, location, original text, explanation, suggested rewrite, `rule_id`, `source_reference` |
| GridFS bucket `review_uploads` | ObjectId | Raw uploaded file bytes — durable, NOT request-scoped memory (see Section 9's reasoning on why this matters for an in-process worker) |
| `sessions`, `checkpoints`/`checkpoint_writes` | — | Still design-stage — belong to the not-yet-built LangGraph shell |

The rules taxonomy is **not** a database collection — confirmed, static, code-authored Python (`app/rules/taxonomy.py`), matching the discovery-phase decision that rule curation is static for MVP.

---

## 5. LangGraph design — still design-stage, not yet built

Unchanged from prior versions of this doc. Nothing in Sections 6-11 below depends on this being built — the review engine and job system are fully functional headless of any conversational layer, confirmed by direct testing (the worker calls `review_document()` with zero LangGraph involvement).

---

## 6. Rules taxonomy — BUILT AND VERIFIED

`app/rules/` — three files:

- **`schema.py`** — the data model. `Rule` is a frozen dataclass with: `category`, `detection_type` (`DETERMINISTIC | LEXICAL | JUDGMENT`), `applies_to` (`GENERAL | AUDIT`), `english_variant` (`None | US | GLOBAL`), `pcs_exception: bool`, `pattern`, `match_validator` (optional post-match callable — see below), `trigger_terms`, `alternative`, `explanation`, `example_before/after`, `source_reference`. `RuleSet.validate()` runs at import time and enforces field-combination consistency per detection type (catches curator errors at deploy, not on first document).
- **`validators.py`** — the small amount of real *logic* the taxonomy needs (e.g. `is_ascending_range`, used to reject phone-number-shaped false positives from the numeric-range rule — a real bug found via production testing, see `EditEdge_Testing.md`). Kept separate so `taxonomy.py` stays pure declarative data.
- **`taxonomy.py`** — the actual curated content.

**Current real numbers** (confirmed, not estimated): **133 rules** — 41 `DETERMINISTIC`, 17 `LEXICAL`, 75 `JUDGMENT`. 117 apply generally; 16 more activate for `AUDIT` documents (133 total when `applies_to=AUDIT`). 2 rules carry `pcs_exception=True` (`risk-audit-advisor-terms`, `risk-audit-collaborate`) — **confirmed directly against the real style guide** (p.95-96, the asterisked PCS exception), not secondhand. 8 rules are `EnglishVariant.GLOBAL`-only and **confirmed inert by default** (tested directly: identical text produces 0 findings under the default `US` call, 1 finding when `GLOBAL` is explicitly requested) — correctly gated behind an intake question that doesn't exist yet in the conversational flow.

**Detection-type decision rule, confirmed and applied consistently**: if the source's own alternative-language column says "avoid using"/"delete" unconditionally → `LEXICAL`. If it says "may be acceptable when..." → `JUDGMENT`. If it's a structural pattern (dashes, digit grouping, spacing) → `DETERMINISTIC`.

**`match_validator`, a real, tested escape hatch** for rules where pure regex can match the *shape* but not the real condition — e.g., `numbers-range-should-use-en-dash`'s pattern matches `\d+-\d+` but can't tell a genuine range ("pages 15-22") from a phone-number fragment ("858-677"); `is_ascending_range` rejects descending pairs (real ranges are essentially never descending) as a free, no-LLM partial fix. `RuleSet.validate()` enforces this field is only ever set on `DETERMINISTIC` rules.

**Deliberately not exhaustive**: Word usage (US) and Global English sections cover what real source content has actually been transcribed and verified — see `EditEdge_Testing.md`'s note on the "verbatim transcription, cite the real printed page" discipline used for every single rule.

**Runtime execution** (`app/review/engine.py`): deterministic + lexical run first, on every block, free. Judgment rules run only for blocks matching at least one `trigger_terms` keyword (rules with no `trigger_terms` — mostly grammar rules with no useful keyword pre-filter — are always candidates, confirmed ~75% of judgment rules fall in this category), batched into LLM calls via structured output. `CONSISTENCY`-category rules get a separate, whole-document pass.

---

## 7. Document processing — BUILT AND VERIFIED

Unchanged in substance from prior versions of this doc — all four parsers (`docx`, `pptx`, `xlsx`, `pdf`), the dispatcher, and image/chart extraction remain as previously documented, with one **new, real production fix**:

**Image format normalization** (`app/documents/image_extraction.py`) — the vision endpoint (Azure OpenAI, fronted by LiteLLM — both facts confirmed directly from a real production error's traceback, previously unknown about this project's infrastructure) rejects any image format outside jpeg/gif/webp/png. None of the four parsers validate or convert the native format they extract (JPEG2000, JBIG2, TIFF, BMP are all real possibilities from scanned/complex documents, not edge cases) — confirmed as a real failure via the first real-endpoint test against an actual audit RFP PDF. Fixed by normalizing every image to PNG via Pillow **before** the vision request is built, regardless of source format — verified against the exact failure scenario (a non-PNG source image) and confirmed working in a second real production run (block count went 6→7, meaning the previously-failing extraction now succeeds).

**Not yet load-tested at 100MB** — still the single largest unverified risk in the parsing layer.

---

## 8. Review engine — BUILT AND VERIFIED

`app/review/` — six files, all with real, direct-tested logic (not just import-checked):

- **`models.py`** — `Finding` (Pydantic, the full internal/API-facing representation) and `LLMJudgmentFinding`/`LLMJudgmentBatchResponse` (the *minimal* schema asked of the LLM — deliberately excludes anything already known deterministically from the `Rule` being checked, reducing tokens and hallucination surface).
- **`deterministic.py`** — `run_deterministic_rules()` (regex, applies `match_validator` when present) and `run_lexical_rules()` (bounded term match). Both use `app/review/matching.py`'s lookaround-based matching, not naive substring or `\b`-based matching — confirmed via direct testing that naive substring matching produced real false positives ("who" matching inside "whole," "trust" matching inside "Trust Solutions") and that `\b` itself fails for symbol-starting/ending triggers (confirmed: `\b&\b` doesn't match "risk & capital" at all, same failure class as an earlier acronym-regex bug).
- **`judgment.py`** — batched, structured-output LLM calls with a `trigger_terms`-based candidate pre-filter, per-batch failure isolation (one bad batch is logged and skipped, not fatal to the whole review).
- **`consistency.py`** — the document-level pass for `CONSISTENCY`-category rules, sending the whole document in one call. Explicitly **not yet redesigned for 100MB scale** (see Section 2, principle 5) — a 200-block truncation cap exists as a stopgap, not a real solution.
- **`matching.py`** — the shared, tested term-matching utility both `deterministic.py` and `judgment.py` depend on.
- **`engine.py`** — `review_document()`, the single entry point tying `app/documents/` output + `app/rules/RULE_SET` into a `list[Finding]`. Signature: `review_document(parsed, rule_set, applies_to, judgment_model, english_variant=US, is_pcs=False)`. Correctly applies `RuleSet.for_applies_to_with_pcs()` and `RuleSet.for_english_variant()` — **a real bug was found and fixed here**: an earlier version bypassed both of those methods with hand-rolled inline filtering, meaning the entire PCS carve-out was built but completely unreachable (no `is_pcs` parameter existed at all). Confirmed fixed by direct end-to-end test.

**Real bugs found and fixed in this layer** (full detail in `EditEdge_Testing.md`): the PCS-unreachable bug above; `punc-double-space-after-period` and `numbers-sentence-initial-spell-out` regexes capturing surrounding context into `original_text` (confusing user-facing output, not just cosmetic); the substring/word-boundary matching bugs above; a taxonomy transcription bug where two distinct source table rows (`ensure/insure/assure` vs. `certify/guarantee/promise/validate/verify/warrant`) had been merged into one rule using only the second row's alternative wording.

---

## 9. Job infrastructure — BUILT AND VERIFIED

`app/jobs/` — six files. Confirmed decision: **in-process** with the FastAPI app (asyncio background tasks started from the lifespan), not a separately deployed service like `knowledge-sync-worker` — appropriate because EditEdge is single-agent, where that operational simplicity outweighs a separate deployment's benefit at this stage.

- **`schema.py`** — `ReviewJob` (Pydantic), `JobStatus` enum.
- **`storage.py`** — GridFS wrapper (`gridfs.AsyncGridFSBucket`, confirmed correct current import path — NOT under `pymongo`, a real thing to get wrong). Durability of the raw file bytes (not just job status) is what makes heartbeat/stale-job-requeue *actually* meaningful for an in-process worker — an in-memory-only file would be unrecoverable after a process crash regardless of what the job record says.
- **`repository.py`** — job CRUD plus the two operations worth being precise about:
  - `claim_next_pending_job()` — atomically claims the oldest pending job **belonging to a user who doesn't already have a job RUNNING** (excludes via `distinct()` + `find_one_and_update()`'s `$nin` filter). **Confirmed via direct testing NOT safe to call concurrently without external serialization** — the read-then-write pair has a genuine TOCTOU race (two concurrent calls can both read "no one running" before either commits); `worker.py`'s shared `asyncio.Lock` fixes this, confirmed by reproducing the race, applying the fix, and re-testing to confirm resolution.
  - `requeue_stale_jobs()` — resets a `RUNNING` job with a stale heartbeat back to `PENDING`.
- **`findings_repository.py`** — persists `Finding` objects keyed by `job_id`.
- **`service.py`** — `submit_review_job()`, the real entry point for any future caller (REST route, future Teams handler). Enforces `MAX_QUEUED_JOBS_PER_USER` for the first time (the setting existed in config since early in the build with nothing checking it until now).
- **`worker.py`** — **N persistent "worker slots"** (`settings.MAX_CONCURRENT_JOBS`), each independently claiming and processing jobs in its own loop — not a bounded-batch-then-wait design (rejected: wastes capacity if some batched jobs finish faster than others) and not an explicit `asyncio.Semaphore` guarding one shared loop (the slot-pool pattern needs neither — concurrency is naturally bounded by "N slot coroutines exist," and every slot self-heals independently, so one slot crash-looping doesn't reduce total capacity to zero). **Confirmed via direct, timestamped testing**: two different users' jobs genuinely run concurrently; two jobs from the same user genuinely stay sequential (the fairness/resource-protection property "one active job per user" was designed to guarantee) — not just asserted, watched fail once, fixed, and watched pass.

**Honest, stated limitation**: heartbeat updates only at phase boundaries (claimed, after-parse, after-review, completed), not mid-batch within a single long LLM call — `STALE_JOB_THRESHOLD_SECONDS` must be set generously relative to real batch durations until/unless this gets finer-grained (a real, contained future change once 100MB timing data exists to size it against).

---

## 10. API layer — BUILT AND VERIFIED (dev/testing surface — see Section 1)

`app/api/v1/documents.py` + `schemas.py` — thin routes; all real logic lives in `app/jobs/service.py`/`repository.py`. Confirmed via a full, real `TestClient` request cycle (real multipart upload → job processing → status/findings retrieval), not just unit-tested in isolation:

- **`POST /documents/review`** — accepts file + `applies_to`/`is_pcs`/`english_variant`, rejects unsupported file types immediately (before queueing a job that would just fail later), returns 202 + `job_id`.
- **`GET /documents/review/{job_id}`** — status + findings once `succeeded`. Returns 404 (not 403) for a job that exists but belongs to a different user — confirmed via direct test, avoids leaking job existence across users.

**Auth — `app/auth/dependencies.py`**, dual-mode via `settings.AUTH_MODE`:
- `"header"` (default) — reads `X-User-Id` directly, matching the confirmed reality that Entra auth wasn't implemented in the app this pattern is modeled on.
- `"entra"` — delegates to the **real** `app/auth/entra.py` (not a separate reimplementation — an earlier version of this dependency had its own independent JWKS/audience-check logic, removed once the real module was available, to avoid two token-validation implementations silently drifting apart). Confirmed working via direct test against the real `entra.py` code (header mode, entra dev-bypass mode, missing-token rejection).

**Real finding from testing, not yet resolved, your call**: `entra.py`'s `AUTH_DEV_BYPASS` path returns early and skips the tenant guard entirely, not just signature verification — confirmed via direct test (a token with a different tenant ID was accepted in dev-bypass mode). Gated behind `not IS_PRODUCTION` either way, so not exploitable as currently configured, but the comment above the tenant check reads as if it always applies, when it doesn't in bypass mode. Flagged, not changed — it's your file.

---

## 11. Dev client — Streamlit

`streamlit_app/app.py` — explicitly a **dev/testing tool**, not the production interface (see Section 1). Built against `streamlit==1.61.1` (confirmed current stable at build time via direct install, not assumed) using the current, non-experimental polling pattern (`@st.fragment(run_every="3s")`, `st.rerun()`) and current sizing convention (`width="stretch"`, not the older `use_container_width=True`). Upload → submit → auto-polling status panel → categorized findings display with CSV export.

**Tested headlessly via Streamlit's own `AppTest` framework** (not just written and assumed correct) across five real scenarios: initial render, full findings display (mocked with real-shaped `JobStatusResponse` data), pending, clean-document, and failed states — all confirmed rendering with zero exceptions.

**Genuinely untested**: live polling against a real running FastAPI server (both processes were never run together in this environment), and real browser CSV-download behavior.

---

## 12. Channel integration — design stage, not yet built

Unchanged from prior versions. Confirmed important clarification (Section 1): Teams will call `app/jobs/service.py` functions **directly, in-process**, not through the REST API in Section 10.

---

## 13. Explicitly deferred (Phase 2+)

Unchanged from prior versions, plus:
- **Consistency pass extract-then-adjudicate redesign** — deliberately deferred, see Section 2 principle 5. Revisit only with real evidence from the 100MB spike that the current design is a genuine bottleneck.
- **Fine-grained job heartbeat** (mid-batch, not just phase-boundary) — real, contained future work once real 100MB timing data exists to size `STALE_JOB_THRESHOLD_SECONDS` against properly.
- **`entra.py`'s dev-bypass tenant-guard gap** — flagged in Section 10, your call whether to fix.

---

## 14. Tech stack — versions confirmed against real installed packages

| Layer | Choice | Note |
|---|---|---|
| Python | 3.13 | Unchanged |
| API framework | FastAPI 0.141.1 | Confirmed current via direct install; multipart (`UploadFile.size`, `Form()` Enum/bool coercion) verified via real `TestClient` requests, not assumed |
| Orchestration | LangGraph 1.2.10 | Not yet integrated |
| DB driver | PyMongo native async (`AsyncMongoClient`), `pymongo.ReturnDocument` | Confirmed `find_one_and_update()` is a genuine atomic single-document operation |
| File storage | `gridfs.AsyncGridFSBucket` | Confirmed correct import path (top-level `gridfs` package, not `pymongo.gridfs`) |
| Auth | PyJWT 2.10.1, `PyJWKClient` | Real `entra.py` in use, not a placeholder |
| GenAI client | `langchain-openai`/`langchain-core`, shared `ChatOpenAI` instance | Confirmed real infrastructure: Azure OpenAI fronted by LiteLLM (learned from a real production error trace) |
| Document parsing | `python-docx`, `python-pptx`, `openpyxl`, `pymupdf`, Pillow | Pillow now load-bearing for image format normalization, not just testing |
| Dev client | Streamlit 1.61.1 | Confirmed current |
| Background job execution | In-process asyncio worker pool (`app/jobs/worker.py`) | Confirmed decision over a separate deployed service or a library (Celery/arq) — see Section 9 |

---

## 15. Still-open items

1. Real ~100MB test files — still the highest-priority open item; blocks validating the parsing layer, the consistency pass's scalability, and `MAX_CONCURRENT_JOBS`/`STALE_JOB_THRESHOLD_SECONDS` tuning all at once.
2. `GENAI_LLM_MODEL`'s real value — every use so far has been a placeholder (`"azure.gpt-4.1"`), never confirmed as the real model string your endpoint expects.
3. `entra.py`'s dev-bypass tenant-guard gap (Section 10) — a decision, not a blocker.
4. Live Streamlit ↔ real-API integration — never run together in this environment.
5. `AUTH_MODE="entra"`'s full production path — validated logic-level (mocked JWKS), never against a real Entra tenant's real signing keys.
6. Job-system settings (`MAX_CONCURRENT_JOBS`, `POLL_INTERVAL_SECONDS`, `STALE_JOB_THRESHOLD_SECONDS`, `MAX_QUEUED_JOBS_PER_USER`) — all real, working code, all untuned defaults.
7. Word usage (US)/Global English taxonomy coverage — grows as more real source pages are supplied; doesn't require engine changes.

---

**Next step:** your call — job infra + API + dev client together now form a complete, testable slice. Natural next pieces are either the LangGraph conversational shell, or closing out the still-open items above (especially #1 and #2) before building further.
