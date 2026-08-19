"""
streamlit_app/app.py

EditEdge's dev/testing client - NOT the production interface. The
real client is Teams (and eventually M365 Copilot), reached through
a separate Bot Framework endpoint that calls the same underlying
app/jobs/service.py functions directly, in-process - not through
this app or the REST API it talks to. This exists purely to exercise
POST /api/v1/documents/review and GET /api/v1/documents/review/{id}
end to end during development, without needing Teams set up.

Run with: streamlit run streamlit_app/app.py

Built against streamlit==1.61.1 (confirmed current stable at build
time, not assumed) - uses @st.fragment(run_every=...) for polling
(the current, non-experimental API - st.experimental_rerun/
experimental_fragment are gone from current Streamlit, this is not
a leftover from that older pattern), and the current width='stretch'
sizing convention rather than the older use_container_width=True,
which newer widget versions are moving away from.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests
import streamlit as st

st.set_page_config(
    page_title="EditEdge Review",
    page_icon="📝",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Config / session state
# ---------------------------------------------------------------------------

DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1"

if "api_base_url" not in st.session_state:
    st.session_state.api_base_url = DEFAULT_API_BASE_URL
if "user_id" not in st.session_state:
    st.session_state.user_id = "test-user"
if "active_job_id" not in st.session_state:
    st.session_state.active_job_id = None
if "job_history" not in st.session_state:
    st.session_state.job_history = []  # list of job_ids submitted this session

_DETECTION_TYPE_STYLE = {
    "deterministic": ("🟢", "Deterministic"),
    "lexical": ("🟡", "Lexical"),
    "judgment": ("🔵", "Judgment"),
}

_STATUS_STYLE = {
    "pending": ("⏳", "Pending"),
    "running": ("⚙️", "Running"),
    "succeeded": ("✅", "Succeeded"),
    "failed": ("❌", "Failed"),
}


def _api_url(path: str) -> str:
    return f"{st.session_state.api_base_url.rstrip('/')}{path}"


def _auth_headers() -> dict:
    # Matches AUTH_MODE="header" (the confirmed default) - see
    # app/auth/dependencies.py. If your environment runs
    # AUTH_MODE="entra" instead, swap this for a real
    # "Authorization: Bearer <token>" header (e.g. from an MSAL
    # device-code flow) - this app doesn't attempt real Entra login,
    # it's a dev/testing tool for the "header" path specifically.
    return {"X-User-Id": st.session_state.user_id}


# ---------------------------------------------------------------------------
# Sidebar - connection settings
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Connection")
    st.session_state.api_base_url = st.text_input(
        "API base URL",
        value=st.session_state.api_base_url,
        help="Your running FastAPI app's base URL, including /api/v1",
    )
    st.session_state.user_id = st.text_input(
        "User ID (X-User-Id)",
        value=st.session_state.user_id,
        help="Sent as the X-User-Id header - matches AUTH_MODE='header'",
    )

    st.divider()
    st.caption("EditEdge dev client — not the production interface.")
    st.caption("The real client is Teams, via a separate endpoint.")

    if st.session_state.job_history:
        st.divider()
        st.subheader("📜 This session's jobs")
        for jid in reversed(st.session_state.job_history[-10:]):
            if st.button(jid[:12] + "…", key=f"history_{jid}", width="stretch"):
                st.session_state.active_job_id = jid
                st.rerun()


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("📝 EditEdge")
st.caption("Pursuit document review — grammar, style, and risk-language checks against the PwC style guide.")


# ---------------------------------------------------------------------------
# Submission form
# ---------------------------------------------------------------------------

with st.container(border=True):
    st.subheader("Submit a document")

    col1, col2 = st.columns([2, 1])

    with col1:
        uploaded_file = st.file_uploader(
            "Document",
            type=["docx", "pptx", "xlsx", "pdf"],
            help="Word, PowerPoint, Excel, or PDF",
        )

    with col2:
        applies_to = st.selectbox(
            "Document type",
            options=["general", "audit"],
            format_func=lambda v: "General proposal" if v == "general" else "Audit / assurance proposal",
        )
        is_pcs = st.checkbox(
            "PCS (Private Company Services) audit",
            disabled=(applies_to != "audit"),
            help="Only relevant for audit proposals - suppresses a small set of audit-restricted terms that are explicitly permitted in PCS proposals.",
        )
        english_variant = st.selectbox(
            "English variant",
            options=["us", "global"],
            format_func=lambda v: "US English" if v == "us" else "Global (UK) English",
        )

    submit_disabled = uploaded_file is None
    if st.button("Submit for review", type="primary", disabled=submit_disabled, width="content"):
        with st.spinner("Uploading and queueing..."):
            try:
                files = {
                    "file": (
                        uploaded_file.name,
                        uploaded_file.getvalue(),
                        uploaded_file.type or "application/octet-stream",
                    )
                }
                data = {
                    "applies_to": applies_to,
                    "is_pcs": str(is_pcs).lower(),
                    "english_variant": english_variant,
                }
                resp = requests.post(
                    _api_url("/documents/review"),
                    files=files,
                    data=data,
                    headers=_auth_headers(),
                    timeout=30,
                )
            except requests.exceptions.ConnectionError:
                st.error(f"Could not reach the API at {st.session_state.api_base_url} - is it running?")
                st.stop()

        if resp.status_code == 202:
            body = resp.json()
            st.session_state.active_job_id = body["job_id"]
            st.session_state.job_history.append(body["job_id"])
            if body["queued_behind_existing_job"]:
                st.info(f"ℹ️ {body['message']}")
            else:
                st.success(f"✅ {body['message']}")
            st.rerun()
        elif resp.status_code == 429:
            st.error(f"🚦 {resp.json().get('detail', 'Too many jobs queued.')}")
        elif resp.status_code == 413:
            st.error(f"📦 {resp.json().get('detail', 'File too large.')}")
        elif resp.status_code == 400:
            st.error(f"⚠️ {resp.json().get('detail', 'Bad request.')}")
        else:
            st.error(f"Unexpected error ({resp.status_code}): {resp.text}")


st.divider()


# ---------------------------------------------------------------------------
# Status + findings - a fragment so it polls independently of the rest
# of the page, without re-running the whole script every few seconds
# ---------------------------------------------------------------------------

def _render_findings(findings: list[dict]) -> None:
    if not findings:
        st.success("🎉 No findings — this document looks clean against the current ruleset.")
        return

    by_category: dict[str, list[dict]] = {}
    for f in findings:
        by_category.setdefault(f["category"], []).append(f)

    summary_cols = st.columns(min(len(by_category) + 1, 6))
    with summary_cols[0]:
        st.metric("Total findings", len(findings), border=True)
    for i, (cat, items) in enumerate(sorted(by_category.items(), key=lambda kv: -len(kv[1]))):
        if i + 1 < len(summary_cols):
            with summary_cols[i + 1]:
                st.metric(cat.replace("_", " ").title(), len(items), border=True)

    st.write("")

    category_filter = st.multiselect(
        "Filter by category",
        options=sorted(by_category.keys()),
        default=[],
        placeholder="All categories",
    )
    visible_categories = category_filter or sorted(by_category.keys())

    for cat in visible_categories:
        items = by_category.get(cat, [])
        if not items:
            continue
        with st.expander(f"**{cat.replace('_', ' ').title()}** ({len(items)})", expanded=True):
            for f in items:
                icon, label = _DETECTION_TYPE_STYLE.get(f["detection_type"], ("⚪", f["detection_type"]))
                with st.container(border=True):
                    top = st.columns([3, 1])
                    with top[0]:
                        st.markdown(f"**{f['rule_id']}**  ·  {icon} {label}  ·  📍 {f['location_display']}")
                    with top[1]:
                        st.caption(f["source_reference"])

                    st.markdown(f"> {f['original_text']}")
                    st.write(f["explanation"])
                    if f.get("suggested_rewrite"):
                        st.markdown(f"**Suggested:** {f['suggested_rewrite']}")


@st.fragment(run_every="3s")
def _job_status_panel() -> None:
    job_id = st.session_state.active_job_id
    if not job_id:
        st.info("Submit a document above to see its review here.")
        return

    try:
        resp = requests.get(
            _api_url(f"/documents/review/{job_id}"),
            headers=_auth_headers(),
            timeout=15,
        )
    except requests.exceptions.ConnectionError:
        st.error(f"Could not reach the API at {st.session_state.api_base_url}")
        return

    if resp.status_code == 404:
        st.error("Job not found (or belongs to a different user).")
        return
    if resp.status_code != 200:
        st.error(f"Unexpected error ({resp.status_code}): {resp.text}")
        return

    job = resp.json()
    icon, label = _STATUS_STYLE.get(job["status"], ("❔", job["status"]))

    header_cols = st.columns([3, 1, 1])
    with header_cols[0]:
        st.subheader(f"{icon} {job['filename']}")
        st.caption(f"Job {job_id}  ·  {job['applies_to']}{' (PCS)' if job['is_pcs'] else ''}  ·  {job['english_variant']}")
    with header_cols[1]:
        st.metric("Status", label)
    with header_cols[2]:
        if job.get("finding_count") is not None:
            st.metric("Findings", job["finding_count"])

    if job["status"] in ("pending", "running"):
        with st.status(f"{label}…", state="running"):
            st.write("Polling every 3 seconds — this updates automatically, no need to refresh.")
        return  # keep polling via run_every, nothing more to render yet

    if job["status"] == "failed":
        st.error(f"Review failed: {job.get('error_message') or 'Unknown error'}")
        return

    if job["status"] == "succeeded":
        st.success("Review complete.")
        findings = job.get("findings") or []
        _render_findings(findings)

        if findings:
            import csv
            import io

            buf = io.StringIO()
            writer = csv.DictWriter(
                buf,
                fieldnames=["category", "detection_type", "rule_id", "location_display",
                            "original_text", "explanation", "suggested_rewrite", "source_reference"],
            )
            writer.writeheader()
            for f in findings:
                writer.writerow({k: f.get(k, "") for k in writer.fieldnames})

            st.download_button(
                "⬇️ Download findings (CSV)",
                data=buf.getvalue(),
                file_name=f"editedge_findings_{job_id[:8]}.csv",
                mime="text/csv",
            )


st.subheader("Review status")
_job_status_panel()