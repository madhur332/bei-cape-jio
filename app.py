# app.py
# BEI Cape — Main entry point
# Navigation is handled via the sidebar (Streamlit native multi-page)

import streamlit as st

st.set_page_config(
    page_title="BEI Cape",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.block-container { padding-top: 2rem; max-width: 900px; }
.big-title { font-size: 2.5rem; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 0.2rem; }
.subtitle { font-size: 1.1rem; opacity: 0.65; margin-bottom: 2rem; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="big-title">🎙️ BEI Cape</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Behavioral Event Interview Training Platform</div>', unsafe_allow_html=True)
st.markdown("---")

col1, col2 = st.columns(2)

with col1:
    with st.container(border=True):
        st.markdown("### 🎙️ Candidate Interview")
        st.write("Practice your BEI questioning technique with an AI candidate. Choose a persona, set difficulty, ask questions by voice, and get rubric-based feedback.")
        st.markdown("**For:** Interviewers / Assessors in training")
        st.info("👈 Click **candidate** in the sidebar to open")

with col2:
    with st.container(border=True):
        st.markdown("### 🔍 Assessor Review")
        st.write("Review completed interview sessions. Inspect rubric-based evaluations, competency coverage rankings, pronoun arc analysis, and detailed coaching feedback.")
        st.markdown("**For:** Trainers / Managers reviewing sessions")
        st.info("👈 Click **assessor** in the sidebar to open")

st.markdown("---")
st.caption("BEI Cape v3 — Powered by Claude API (Anthropic)")
