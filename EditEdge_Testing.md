# EditEdge — Testing Documentation

**Status:** Living document — single source of truth for testing, alongside `EditEdge_Architecture.md`. Covers testing philosophy, what's verified and how per layer, the full list of real bugs found during this build, and what remains genuinely untested.

---

## 1. Testing philosophy

The single discipline behind everything in this document: **verify against real APIs, real libraries, and real behavior — never assume, even when confident.** This has been applied consistently across the whole build, and has repeatedly caught real bugs that reasoning alone would have missed. Concrete pattern, repeated throughout:

1. Before writing code against any library API, inspect the *installed* version directly (`inspect.signature`, reading source) rather than trusting training-data memory of what an older version's API looked like.
2. Before trusting a regex, test it against real matching *and* non-matching text — compiling successfully is not evidence a pattern works.
3. Before trusting concurrent/async logic, write a test that actually exercises concurrency (real timestamps, real overlapping tasks) rather than reasoning about what *should* happen.
4. When an external review (a second Claude instance, with access to real source files this agent didn't have) flags something, independently reproduce the claim before accepting or rejecting it — several flagged "bugs" were disproven this way; several others were confirmed and fixed.
5. When something is genuinely untestable in this environment (no real MongoDB, no real GenAI endpoint, no browser), say so explicitly rather than imply confidence that doesn't exist.

---

## 2. Document parsers — `app/documents/`

**Method**: real fixture files generated via each format's own authoring library (`python-docx`, `python-pptx`, `openpyxl`, `pymupdf`), covering both the happy path and specific edge cases pulled from two rounds of structured external code review.

**Real bugs found and fixed** (docx): nested tables (a table inside a table cell) were completely invisible via `doc.tables` — fixed via `iter_inner_content()`, which also fixed paragraph/table document-order interleaving as a side effect. Merged cells were then found to be processed multiple times by that same refactor (`row.cells` repeats the same underlying element per grid position spanned) — fixed via identity-based dedup on `cell._tc`. Floating/wrapped images (`wp:anchor`, not just `wp:inline`) were invisible. Images/OLE objects inside table cells were invisible. EMF/WMF images crashed the parser. Linked (not embedded) pictures were misreported as failures. `mc:AlternateContent` could double-count the same image.

**Real bugs found and fixed** (pptx): populated picture placeholders (e.g. the "Picture with Caption" layout) were **silently dropped entirely** — `shape_type` reports `PLACEHOLDER`, not `PICTURE`, even when populated, reproduced directly against a real layout. Movie/audio media was confirmed to already reach the safety net correctly (an initial external-review claim that it would crash was disproven by reading the actual library source — `Movie` and `Picture` are siblings, not parent/child) but was mislabeled and discarding a real extractable poster-frame image.

**Real bugs found and fixed** (xlsx): `read_only=True` mode (required for memory efficiency at 100MB scale) doesn't expose charts/images at all — solved by reading the `.xlsx` zip container's relationship chain directly, verified against real generated fixtures rather than assumed from documentation.

**Real bugs found and fixed** (pdf): `page.get_images()` reports images merely *referenced* in a page's (possibly inherited) `/Resources` dictionary, not necessarily images actually *displayed* — confirmed via maintainer guidance, fixed by switching to `page.get_image_info(xrefs=True)`.

**Not tested**: any of the four parsers at 100MB scale. Every fixture used has been small and synthetic.

---

## 3. Rules taxonomy — `app/rules/`

**Method**: two layers of verification.

1. **Content fidelity** — every rule's `description`/`alternative`/`explanation`/`source_reference` is transcribed as close to verbatim as practical from real pasted source-document text, citing the source's own **printed** page number (never a parser's `page_number`, which is physical document order and can diverge from what's printed — a real, confirmed discrepancy class, not hypothetical).
2. **Mechanical correctness** — every `DETERMINISTIC` rule's regex is tested against real matching *and* non-matching text, not just confirmed to compile. `RuleSet.validate()` runs at import time and enforces internal consistency (a `DETERMINISTIC` rule must have a `pattern` and must not have `trigger_terms`; a `LEXICAL` rule must have `trigger_terms` and must not have a `pattern`; `match_validator` may only be set on `DETERMINISTIC` rules).

**Real bugs found and fixed**:
- `punc-acronym-no-periods`'s original pattern had a trailing `\b` that silently failed to match "U.S." — a period followed by a space has no word-boundary transition, since both characters are non-word characters. Same failure class recurred twice more (IRS possessive check, ampersand trigger term) before being generalized into `app/review/matching.py`'s lookaround-based matching (see Section 4).
- `numbers-four-plus-digits-need-comma` flagged years ("2020") as needing comma-grouping — confirmed via direct test, contradicted the source's own Years entry. Fixed by excluding a plausible year range.
- A transcription bug: two distinct source table rows (`ensure/insure/assure` — one alternative list; `certify/guarantee/promise/validate/verify/warrant` — a different, shorter alternative list) had been merged into one rule using only the second row's alternative wording. Split into two rules matching the source's actual structure.
- `numbers-range-should-use-en-dash` flagged phone-number-shaped fragments ("858-677," from real production testing against an actual audit RFP) as if they were genuine numeric ranges. Fixed via `match_validator=is_ascending_range` — real ranges are essentially never expressed in descending order.
- `gram-capitalization-consistency` was tagged a per-block category (`CAPITALIZATION`) despite being a document-level check (a single block can't answer "is this capitalized the same way elsewhere in the document") — caught while designing the review engine's routing, moved to `CONSISTENCY`.
- The PCS exception (`pcs_exception` field) was initially set on two rules based on a **secondhand** external review (which had the real style guide file; this agent did not). Later independently **confirmed directly** against real pasted source content (p.95-96) — the secondhand guess turned out correct, but was explicitly flagged as unverified until that direct confirmation happened.

**Real, confirmed-external-review claims that were investigated and disproven** (not applied): several claimed docx/pptx parser bugs, on independent reproduction, did not hold up against the real library source — see Section 2's "Real bugs found and fixed" lists, which represent only the claims that *did* reproduce.

**Current numbers**: 133 rules — 41 `DETERMINISTIC`, 17 `LEXICAL`, 75 `JUDGMENT`. See `EditEdge_Architecture.md` Section 6 for the full breakdown.

**Not tested**: rule *quality* at scale — every regex has been unit-tested for correctness, but the taxonomy's real-world false-positive/false-negative rate has only been observed across two real production test runs (Section 7).

---

## 4. Review engine — `app/review/`

**Method**: layered — pure logic (candidate selection, matching, batching) tested directly with real inputs; full pipeline tested with a mocked LLM client (`AsyncMock`/`MagicMock` returning real `LLMJudgmentBatchResponse` Pydantic instances, so schema validation is real even though the network call is not).

**Real bugs found and fixed**:
- **The PCS carve-out was fully built but completely unreachable.** `review_document()` had no `is_pcs` parameter at all — an earlier version hand-rolled inline filtering, bypassing `RuleSet.for_applies_to_with_pcs()` and `RuleSet.for_english_variant()` entirely, both of which existed as dead code. Confirmed via direct code inspection, fixed, and confirmed resolved via a full end-to-end test (mocked LLM flags a PCS-exempt rule; non-PCS submission shows the finding, PCS submission correctly suppresses it entirely).
- `numbers-sentence-initial-spell-out`'s regex captured the leading punctuation/whitespace into the match itself, so `original_text` (shown to the user as "the offending text") came out as `". 12"` instead of `"12"` — confirmed from real production output, fixed via lookbehind.
- `punc-double-space-after-period`'s regex had the same class of bug (captured the next sentence's first letter) — confirmed from real production output (`". \nO"`), fixed via lookaround.
- **Systemic substring-matching bug**, found via a specific external-review claim ("that/which/who/whom trigger_terms will fire on nearly every block") that was *partially* right but under-scoped: the real, broader bug was that `_select_candidate_rules()` and `run_lexical_rules()` both did naive `term in text` substring matching, which candidate-matched "who" inside "whole"/"wholesale," "less" inside "unless"/"nevertheless," and "trust" inside "Trust Solutions"/"entrust" — confirmed via direct test. Fixed with a shared, tested `app/review/matching.py` using lookaround (`(?<!\w)term(?!\w)`), not `\b` — confirmed via direct test that `\b` itself fails for symbol-starting/ending triggers (`\b&\b` doesn't match "risk & capital" at all).
- `run_lexical_rules()` was reporting the trigger term's own casing in `original_text`, not the actual matched text from the document (e.g. "Customer" in the source would be reported as "customer"). Fixed to extract the real match via `re.Match.group(0)`.

**Concurrency-adjacent testing** (job worker's use of the review engine, not the engine itself) — see Section 5.

**Not tested**: judgment/consistency-pass output *quality* against the real endpoint at any scale beyond two small production runs (Section 7). `with_structured_output()`'s default `json_schema` mode has only been confirmed to work against the real endpoint for small batches.

---

## 5. Job infrastructure — `app/jobs/`

**Method**: an in-memory fake MongoDB (`fake_mongo.py` — `FakeCollection`/`FakeDB`) built to mimic real Mongo *semantics* (filter matching including `$in`/`$nin`/`$lt`, atomic `find_one_and_update` with sort, `distinct()`), not just mocked call signatures — deliberately, so tests exercise real logic (FIFO ordering, atomicity, concurrency) rather than confirming functions were called with expected arguments.

**Real bugs found and fixed**:
- **A genuine TOCTOU race condition**, confirmed by direct reproduction: `claim_next_pending_job()`'s "check which users are running, then claim a job excluding them" is two separate operations (`distinct()` read, then `find_one_and_update()` write). Two concurrent calls (as genuinely happens across worker slots) can both read "no one running" before either commits, allowing two jobs from the *same* user to end up `RUNNING` simultaneously — exactly the property "one active job per user" exists to prevent. Confirmed by watching it fail (two same-user jobs both started at t=0.00s in a timestamped test), fixed via a single-process `asyncio.Lock` shared across worker slots (correct choice specifically because the worker is confirmed in-process/single-instance, not a distributed system), confirmed resolved by re-running the identical test and watching it pass.
- **Two dead-end debugging detours during that same investigation**, documented here because they cost real time and are a useful cautionary note: (1) an early version of the in-memory fake was missing a `$nin` implementation entirely, so a "fix" appeared to fail when the real cause was the test harness silently no-op'ing the exclusion filter; (2) a *stale copy* of `repository.py` in the test directory (never re-copied after a real fix was made in the source tree) caused a supposedly-fixed test to keep failing against old logic. Both were found by directly tracing execution with print statements rather than continuing to theorize, and both are why every "confirmed via test" claim in this document was re-verified against the *current* file content before being written down.

**Design decisions verified as actually achieving their goal, not just asserted**:
- The worker-slot-pool concurrency design (N persistent coroutines, not batch-claim-then-wait, not an explicit `Semaphore`) was confirmed via a timestamped test to genuinely let different users' jobs overlap in execution time.
- `submit_review_job()`'s `MAX_QUEUED_JOBS_PER_USER` enforcement was confirmed to correctly accept the first 5 submissions and reject the 6th with the right exception.

**Not tested**: against a real MongoDB instance (Atlas or otherwise) — every test uses the in-memory fake described above. A short real-Mongo smoke test (submit → confirm a document lands in `review_jobs` → confirm the worker picks it up) is a real, still-open gap, not yet done.

---

## 6. API layer & auth — `app/api/v1/`, `app/auth/`

**Method**: FastAPI's real `TestClient`, real multipart file uploads (not mocked at the HTTP layer), against the real route handlers with only the database (in-memory fake) and GenAI client (mocked) substituted underneath.

**Real, confirmed-via-test properties**: `UploadFile.size` is populated by Starlette's multipart parser *before* the route body runs — confirmed directly, not assumed, and used for a cheap pre-read size check. `Form()` correctly coerces string form values to `Enum`/`bool` types. A full submit → status-check → worker-processes-it → status-check-again cycle produces the correct findings, correct status transitions, and correct file cleanup (GridFS delete on success only).

**Real bugs found and fixed**:
- An import error (`supported_extensions` misattributed to the wrong module) caught by direct re-verification before running anything, not by trusting an earlier note about where it lived.
- Cross-user job access was confirmed to correctly return 404 (not 403, deliberately, to avoid confirming a job's existence to a non-owner) via direct test with two different overridden user identities.

**Auth dual-mode, both paths tested against the real `app/auth/entra.py`** (not a placeholder or independent reimplementation — an earlier standalone JWKS/audience-check implementation in `app/auth/dependencies.py` was removed once the real `entra.py` was available, specifically to avoid two token-validation implementations silently drifting apart):
- `AUTH_MODE="header"`: `X-User-Id` present → success; absent → 401. Confirmed.
- `AUTH_MODE="entra"` + `AUTH_DEV_BYPASS` + `not IS_PRODUCTION`: a real (unsigned, dev-bypass) JWT with `oid` claim → correct user extraction. Missing `Authorization` header → 401 via `entra.py`'s own `AuthError`. Confirmed.
- Invalid `AUTH_MODE` value → `RuntimeError`, confirmed it fails loudly rather than silently defaulting.

**Real finding, not a bug in this agent's code — a discovered property of the real `entra.py`, flagged rather than silently changed**: `AUTH_DEV_BYPASS`'s early-return path skips the tenant guard check entirely, not just signature verification. Confirmed via direct test: a token with a *different* tenant ID (`tid`) was accepted while bypass was active. Gated behind `not IS_PRODUCTION` either way, so not exploitable as currently configured — but the code comment immediately above the tenant check reads as if it always applies, when it does not in bypass mode. This is `entra.py`'s own file and decision; documented here, not changed.

**Not tested**: `AUTH_MODE="entra"` against a *real* Entra tenant's real signing keys (only the dev-bypass, unsigned-token path has been exercised) — `PyJWKClient`'s real network-fetching behavior is unverified.

---

## 7. Real production testing — against your actual GenAI endpoint

Two real end-to-end runs against a real audit RFP PDF and your real, live GenAI endpoint (not this agent's own environment — run and reported by you), each surfacing genuinely new information:

**Run 1 — found real infrastructure facts and a real bug**:
- Confirmed the GenAI service is Azure OpenAI fronted by LiteLLM (from a real error's traceback — `litellm.BadRequestError`, `AzureException`) — not previously known about this project's infrastructure.
- **Confirmed real bug**: the vision endpoint rejected an embedded image with "must be one of jpeg/gif/webp/png" — none of the four parsers validate or convert the native format they extract. Fixed by normalizing every image to PNG via Pillow before the vision call, regardless of source format (see `EditEdge_Architecture.md` Section 7).
- Confirmed error handling itself already worked correctly even before that fix — the `APIError` was caught, logged, and the rest of the review completed normally; the gap was purely "sends a rejected format," not the failure-handling path.

**Run 2 — confirmed the fix, surfaced a taxonomy precision issue**:
- Block count went 6 → 7 after the PNG-normalization fix (the previously-failing image extraction now succeeds) — confirmed the fix works against the real endpoint, not just in mocked tests.
- Surfaced `numbers-range-should-use-en-dash`'s phone-number false-positive (Section 3) — found because the *same* value (`858-677`) recurred identically across two separate real runs, giving enough evidence to design and verify a real fix (`is_ascending_range`).
- 32-34 total findings on a 6-7 block real document, including several genuinely solid catches (passive voice, product-name mentions, superlatives, healthcare/health care consistency) — the first real evidence the taxonomy's judgment quality is reasonable, not just that the plumbing works.
- Real timing data: judgment-pass LLM calls took ~30s on a tiny (6-7 block) document — the first real number toward sizing `STALE_JOB_THRESHOLD_SECONDS` and the still-open "acceptable turnaround time" question, though nowhere near enough to extrapolate confidently to 100MB scale.

**One important non-bug finding from this testing**: the test document used was Sharp Healthcare's *incoming* RFP to PwC, not a PwC-authored response draft. Several risk-language findings (superlative claims, product-name mentions) were technically correct rule matches against text that was actually the *client's own* RFP prompts, not PwC's authored claims — a reminder that the taxonomy is built around PwC's own language, and test documents should reflect that document type going forward.

---

## 8. Dev client — Streamlit (`streamlit_app/app.py`)

**Method**: Streamlit's own official headless testing framework, `streamlit.testing.v1.AppTest` — runs the real script file and inspects the resulting element tree, with `requests.get`/`requests.post` mocked to return real-shaped API responses (matching the actual `JobStatusResponse`/`Finding` Pydantic schemas).

**Confirmed via direct test, zero exceptions across all scenarios**: initial render (all widgets construct correctly), full findings display (correct metrics, category grouping, icons, all fields populated), pending status, clean-document (zero findings) success, and failed status.

**Not tested**: live polling against a real running FastAPI server — both processes have never been run together in this environment. Real browser rendering and the CSV download button's actual browser behavior.

---

## 9. What remains genuinely untested — the honest summary

1. **100MB-scale documents**, anywhere in the pipeline — parsers, the consistency pass, job timing/heartbeat thresholds. The single largest open risk in this entire build.
2. **A real MongoDB instance** — every job-system test uses an in-memory fake, however semantically faithful.
3. **`AUTH_MODE="entra"`'s real-signature path** — only the dev-bypass (unsigned) path has been exercised.
4. **Streamlit ↔ real API, live**, in a browser.
5. **Judgment/consistency-pass quality at any real scale** — two small real production runs is real signal, not a substitute for broader validation.
6. **`GENAI_LLM_MODEL`'s actual real value** — every test has used a placeholder string, never confirmed against what your endpoint actually expects.

---

## 10. How to run what exists

- **`scripts/test_review_pipeline.py`** — standalone script, run locally with real `.env` credentials and a real small file: `python scripts/test_review_pipeline.py path/to/file.docx [--audit] [--pcs] [--no-images]`. This is what produced the real production findings in Section 7.
- **`streamlit run streamlit_app/app.py`** — the dev client, once a FastAPI server is also running locally.
- No committed pytest suite exists yet in this repo — all testing described in this document was done interactively during the build, using in-memory fakes and `AppTest`/`TestClient` patterns that could be extracted into a real committed test suite as a next step, if that's valuable to formalize.
