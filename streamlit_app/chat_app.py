"""
streamlit_app/chat_app.py

EditEdge's chat-based dev/testing client - built once the LangGraph
shell existed, per your request to test real conversational
scenarios properly (not just upload-and-poll, which streamlit_app/
app.py already covers). Talks to POST /api/v1/chat/message.

INTAKE FLOW: when a file is attached, the backend graph (see
app/agent/nodes/submit_document.py) already asks the three real
intake questions (general/audit, PCS if audit, US/Global English)
as part of the normal conversation - this app does NOT re-implement
that logic. It just needs to correctly send the file bytes on the
turn they're attached, and display whatever the assistant asks in
response, exactly like any other message - the questions arrive as
ordinary chat replies, no special UI needed for them.

Uses st.chat_input(accept_file=True) - confirmed current, stable
Streamlit API (installed version 1.62.0) - files attach directly in
the chat input box itself, not a separate uploader widget, which is
the more natural fit for a chat interface than the upload-first flow
in the other Streamlit app.
"""

from __future__ import annotations

import requests
import streamlit as st

st.set_page_config(page_title="EditEdge Chat", page_icon="💬", layout="centered")

DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1"

if "api_base_url" not in st.session_state:
    st.session_state.api_base_url = DEFAULT_API_BASE_URL
if "user_id" not in st.session_state:
    st.session_state.user_id = "test-user"
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "history" not in st.session_state:
    st.session_state.history = []  # list of {"role": "user"|"assistant", "content": str, "filename": str|None}


def _api_url(path: str) -> str:
    return f"{st.session_state.api_base_url.rstrip('/')}{path}"


def _auth_headers() -> dict:
    # Matches AUTH_MODE="header" (the confirmed default) - see
    # app/auth/dependencies.py.
    return {"X-User-Id": st.session_state.user_id}


with st.sidebar:
    st.header("⚙️ Connection")
    st.session_state.api_base_url = st.text_input("API base URL", value=st.session_state.api_base_url)
    st.session_state.user_id = st.text_input("User ID (X-User-Id)", value=st.session_state.user_id)

    st.divider()
    st.caption("EditEdge chat client — dev/testing surface.")
    st.caption("Attach a file directly in the message box below.")

    if st.button("🔄 New conversation", width="stretch"):
        st.session_state.session_id = None
        st.session_state.history = []
        st.rerun()

    if st.session_state.session_id:
        st.divider()
        st.caption(f"Session: `{st.session_state.session_id[:12]}…`")


st.title("💬 EditEdge")
st.caption("Chat with EditEdge — submit documents, ask about style rules, check on reviews in progress.")

for turn in st.session_state.history:
    with st.chat_message(turn["role"]):
        if turn.get("filename"):
            st.caption(f"📎 {turn['filename']}")
        st.write(turn["content"])

prompt = st.chat_input(
    "Ask a question or attach a document to review...",
    accept_file=True,
    file_type=["docx", "pptx", "xlsx", "pdf"],
)

if prompt:
    message_text = prompt.text or ("Please review this document." if prompt.files else "")
    uploaded_file = prompt.files[0] if prompt.files else None

    st.session_state.history.append({
        "role": "user",
        "content": message_text,
        "filename": uploaded_file.name if uploaded_file else None,
    })
    with st.chat_message("user"):
        if uploaded_file:
            st.caption(f"📎 {uploaded_file.name}")
        st.write(message_text)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                data = {"message": message_text}
                if st.session_state.session_id:
                    data["session_id"] = st.session_state.session_id

                files = None
                if uploaded_file is not None:
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type or "application/octet-stream")}

                resp = requests.post(
                    _api_url("/chat/message"),
                    data=data,
                    files=files,
                    headers=_auth_headers(),
                    timeout=60,
                )
            except requests.exceptions.ConnectionError:
                st.error(f"Could not reach the API at {st.session_state.api_base_url} - is it running?")
                st.stop()

        if resp.status_code == 200:
            body = resp.json()
            st.session_state.session_id = body["conversation_id"]
            reply = body["text"]
            st.write(reply)
            st.session_state.history.append({"role": "assistant", "content": reply, "filename": None})
        elif resp.status_code in (400, 413):
            detail = resp.json().get("detail", "Request rejected.")
            st.error(detail)
            st.session_state.history.append({"role": "assistant", "content": f"⚠️ {detail}", "filename": None})
        else:
            st.error(f"Unexpected error ({resp.status_code}): {resp.text}")