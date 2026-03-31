# pages/candidate.py
# BEI Cape — Candidate Interview Interface v3
# Changes: compact metrics header, editable transcription box

import os
import json
import time
import base64
import asyncio
import tempfile

import streamlit as st
import streamlit.components.v1 as components
import speech_recognition as sr
import edge_tts

from bei_engine import (
    BEIEngine,
    PersonaStore,
    SessionStore,
    get_tts_voice_for_persona,
    is_male_voice,
)

APP_MODE = "candidate"

st.set_page_config(
    page_title="BEI Cape — Candidate Interview",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1280px; }
.mode-pill {
    display: inline-block; padding: 0.25rem 0.7rem; border-radius: 999px;
    border: 1.5px solid #4f8ef7; font-size: 0.75rem; font-weight: 700;
    color: #4f8ef7; margin-bottom: 0.5rem; letter-spacing: 0.04em;
}
.big-title { font-size: 1.5rem; font-weight: 800; margin-bottom: 0.1rem; }
.subtle { opacity: 0.72; margin-bottom: 1rem; font-size: 0.88rem; }
div[data-testid="metric-container"] {
    background: rgba(79,142,247,0.06);
    border: 1px solid rgba(79,142,247,0.15);
    border-radius: 8px;
    padding: 0.4rem 0.6rem !important;
}
div[data-testid="metric-container"] label { font-size: 0.7rem !important; opacity: 0.7; }
div[data-testid="metric-container"] div[data-testid="stMetricValue"] { font-size: 0.9rem !important; font-weight: 700; }
.pronoun-status-we {
    padding: 0.25rem 0.6rem; border-radius: 6px;
    background: rgba(255,170,0,0.12); border: 1px solid rgba(255,170,0,0.4);
    font-size: 0.75rem; color: #e8a800; font-weight: 600;
}
.pronoun-status-i {
    padding: 0.25rem 0.6rem; border-radius: 6px;
    background: rgba(40,200,100,0.12); border: 1px solid rgba(40,200,100,0.4);
    font-size: 0.75rem; color: #28c864; font-weight: 600;
}
.diff-badge-low { display:inline-block;padding:0.15rem 0.6rem;border-radius:999px;background:rgba(40,200,100,0.15);border:1px solid rgba(40,200,100,0.4);color:#28c864;font-size:0.75rem;font-weight:700; }
.diff-badge-medium { display:inline-block;padding:0.15rem 0.6rem;border-radius:999px;background:rgba(255,170,0,0.15);border:1px solid rgba(255,170,0,0.4);color:#e8a800;font-size:0.75rem;font-weight:700; }
.diff-badge-high { display:inline-block;padding:0.15rem 0.6rem;border-radius:999px;background:rgba(220,50,50,0.12);border:1px solid rgba(220,50,50,0.35);color:#dc3232;font-size:0.75rem;font-weight:700; }
.comp-addressed { padding:0.12rem 0.45rem;border-radius:4px;background:rgba(40,200,100,0.12);border:1px solid rgba(40,200,100,0.3);font-size:0.72rem;color:#28c864;display:inline-block;margin:2px; }
.comp-not-addressed { padding:0.12rem 0.45rem;border-radius:4px;background:rgba(220,50,50,0.08);border:1px solid rgba(220,50,50,0.25);font-size:0.72rem;color:#dc3232;display:inline-block;margin:2px; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="mode-pill">🎙️ Candidate Interview Mode</div>', unsafe_allow_html=True)
st.markdown('<div class="big-title">BEI Cape</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Choose a persona, set difficulty, record your questions by voice, and receive structured feedback when done.</div>', unsafe_allow_html=True)

engine = BEIEngine()
AUDIO_DIR = os.path.join("sessions", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

DIFFICULTY_OPTIONS = ["Auto (based on session count)", "Low", "Medium", "High"]
DIFFICULTY_DESCRIPTIONS = {
    "Auto (based on session count)": "System assigns difficulty based on how many interviews you have completed.",
    "Low": "Candidate is cooperative and reasonably clear. Good for beginners.",
    "Medium": "Candidate is context-heavy and vague about specifics. Requires active probing.",
    "High": "Candidate is guarded, evasive, and avoids outcomes. Requires strong probing.",
}

def get_difficulty_override(selected):
    if selected == "Auto (based on session count)":
        return None
    return selected.lower()

def init_state():
    persona_options = PersonaStore.list_persona_files()
    default_file = persona_options[0]["file_name"] if persona_options else None
    default_persona = PersonaStore.load_persona_by_file(default_file) if default_file else {}
    defaults = {
        "app_mode": APP_MODE,
        "session_id": None,
        "selected_persona_file": default_file,
        "persona": default_persona,
        "persona_editor_text": json.dumps(default_persona, indent=2, ensure_ascii=False) if default_persona else "{}",
        "selected_difficulty": DIFFICULTY_OPTIONS[0],
        "pending_end_confirmation": False,
        "last_transcribed_question": "",
        "edited_question": "",
        "transcription_ready": False,
        "latest_audio_file": None,
        "latest_reply_text": ""
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    st.session_state["app_mode"] = APP_MODE

def transcribe_audio(uploaded_audio) -> str:
    if uploaded_audio is None:
        return ""
    recognizer = sr.Recognizer()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp.write(uploaded_audio.read())
        tmp_path = tmp.name
    try:
        with sr.AudioFile(tmp_path) as source:
            audio_data = recognizer.record(source)
        return recognizer.recognize_google(audio_data).strip()
    except Exception:
        return ""
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

async def _save_edge_tts_async(text, output_file, voice="en-US-AriaNeural"):
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(output_file)

def generate_tts_file(text, session_id, candidate_turn_index, persona_name=""):
    output_file = os.path.join(AUDIO_DIR, f"{session_id}_candidate_{candidate_turn_index}.mp3")
    if not os.path.exists(output_file):
        voice = get_tts_voice_for_persona(persona_name)
        asyncio.run(_save_edge_tts_async(text=text, output_file=output_file, voice=voice))
    return output_file

def autoplay_audio_file(audio_file):
    if not audio_file or not os.path.exists(audio_file):
        return
    with open(audio_file, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    components.html(f'<audio autoplay controls style="width:100%;margin-top:8px;"><source src="data:audio/mp3;base64,{b64}" type="audio/mp3"></audio>', height=60)

def render_audio_controls(audio_file):
    if not audio_file or not os.path.exists(audio_file):
        return
    with open(audio_file, "rb") as f:
        st.audio(f.read(), format="audio/mp3")

def render_transcript(session):
    st.markdown("### Interview Transcript")
    if not session["conversation"]:
        st.info("The interview has not started yet. Record a question to begin.")
        return
    for msg in session["conversation"]:
        role = msg["role"]
        ts = msg.get("elapsed_mmss", msg.get("timestamp", ""))
        if role == "interviewer":
            with st.chat_message("user"):
                label = "Interviewer" + (" 💬" if msg.get("is_small_talk") else " 🎯" if msg.get("is_behavioral") else "")
                st.markdown(f"**{label} [{ts}]**  \n{msg['content']}")
        else:
            with st.chat_message("assistant"):
                pc = msg.get("pronoun_counts", {})
                we_c, i_c = pc.get("we_count", 0), pc.get("i_count", 0)
                pronoun_tag = f" (we:{we_c} / I:{i_c})" if (we_c + i_c) > 0 else ""
                st.markdown(f"**Candidate [{ts}]{pronoun_tag}**  \n{msg['content']}")
                if msg.get("audio_file"):
                    render_audio_controls(msg["audio_file"])

def render_score_breakdown(report):
    breakdown = report.get("score_breakdown_matrix", [])
    if not breakdown:
        return
    st.markdown("### How Your Score Was Calculated")
    import pandas as pd
    df = pd.DataFrame(breakdown)
    def highlight_total(row):
        if row["Parameter"] == "TOTAL":
            return ["font-weight:bold;background-color:rgba(79,142,247,0.1)"] * len(row)
        return [""] * len(row)
    try:
        st.dataframe(df.style.apply(highlight_total, axis=1), use_container_width=True, hide_index=True)
    except Exception:
        st.dataframe(df, use_container_width=True, hide_index=True)

def render_competency_addressed(report):
    comp_summary = report.get("competency_addressed_summary", {})
    if not comp_summary:
        return
    st.markdown("### Competency Indicators Addressed")
    st.caption(f"STAR+Learning completeness: {comp_summary.get('star_completeness_pct', 0)}%")
    parts = [f'<span class="comp-addressed">✓ {i}</span>' for i in comp_summary.get("addressed", [])]
    parts += [f'<span class="comp-not-addressed">✗ {i}</span>' for i in comp_summary.get("not_addressed", [])]
    st.markdown(" ".join(parts), unsafe_allow_html=True)

def render_candidate_summary(report):
    candidate_summary = report.get("candidate_summary", {})
    assessor_feedback = report.get("assessor_feedback", {})
    st.markdown("## Your Interview Feedback")
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Final Score", f"{report.get('final_score_percent', 0)}%")
    with c2:
        readiness = report.get("readiness_status", "NA")
        st.metric("Readiness", f"{'🟢' if readiness == 'Ready' else '🟡'} {readiness}")
    with st.container(border=True):
        render_competency_addressed(report)
    with st.container(border=True):
        render_score_breakdown(report)
    st.markdown("---")
    with st.container(border=True):
        st.markdown("### Overall Impression")
        st.write(candidate_summary.get("overall_impression", ""))
    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.markdown("### Strengths")
            for item in (candidate_summary.get("strengths") or ["No strengths captured."]):
                st.write(f"✓ {item}")
    with col2:
        with st.container(border=True):
            st.markdown("### Areas to Improve")
            for item in (candidate_summary.get("improvements") or ["No improvement areas captured."]):
                st.write(f"→ {item}")
    with st.container(border=True):
        st.markdown("### Assessor Feedback Snapshot")
        s1, s2 = st.columns(2)
        with s1:
            st.markdown("**What Worked**")
            for item in assessor_feedback.get("strengths", []):
                st.write(f"✓ {item}")
        with s2:
            st.markdown("**What To Improve**")
            for item in assessor_feedback.get("missed_probes", []):
                st.write(f"→ {item}")
        st.markdown("**Probing Quality Assessment**")
        st.write(assessor_feedback.get("probing_quality", ""))
    with st.container(border=True):
        st.markdown("### Better Questions You Could Have Asked")
        for q in assessor_feedback.get("better_questions", []):
            st.write(f"💡 *{q}*")

def render_dynamic_probing_tip(session):
    pronoun_shifted = session.get("pronoun_shift_triggered", False)
    metrics = session.get("metrics", {})
    accumulated = session.get("competency_indicators_accumulated", {})
    interviewer_turns = metrics.get("interviewer_turns", 0)
    missing_indicators = [k for k, v in accumulated.items() if not v]

    if interviewer_turns == 0:
        with st.container(border=True):
            st.markdown("**💡 Getting Started**")
            st.write("Start with a warm greeting, then try: *'Tell me about a time when you faced a challenging situation at work.'*")
        return
    if interviewer_turns <= 2 and not pronoun_shifted:
        with st.container(border=True):
            st.markdown("**💡 Move to Behavioral Questions**")
            st.write("Try: *'Can you walk me through a specific example of when you had to handle a difficult situation?'*")
        return
    if not pronoun_shifted:
        pt = session.get("pronoun_tracking", {})
        total_we, total_i = pt.get("total_we_count", 0), pt.get("total_i_count", 0)
        with st.container(border=True):
            if total_we > total_i * 2:
                st.markdown("**⚠️ Pronoun Alert: Candidate using 'We' heavily**")
                st.write(f"'we/our/team' used **{total_we}x** vs 'I/my/me' **{total_i}x**. Push: *'What did YOU specifically do?'*")
            else:
                st.markdown("**💡 Probe Deeper**")
                st.write("Try: *'Can you focus specifically on your own contribution here?'*")
        return
    with st.container(border=True):
        if "result_explored" in missing_indicators:
            st.markdown("**✅ Now Push for Results**")
            st.write("Try: *'What was the measurable result of your action?'*")
        elif "learning_explored" in missing_indicators:
            st.markdown("**✅ Explore Learning**")
            st.write("Try: *'Looking back, what would you do differently?'*")
        elif len(missing_indicators) == 0:
            st.markdown("**🎯 Excellent Coverage**")
            st.write("All indicators addressed. Consider a second example for depth.")
        else:
            missing_labels = [i.replace("_explored","").replace("_"," ").title() for i in missing_indicators]
            st.markdown("**✅ Fill Remaining Gaps**")
            st.write(f"Still missing: **{', '.join(missing_labels)}**.")

# ─────────────────────────────────────────────────────────────────────────────
init_state()
persona_options = PersonaStore.list_persona_files()
display_map = {row["display_name"]: row["file_name"] for row in persona_options}
reverse_map = {row["file_name"]: row["display_name"] for row in persona_options}

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Interview Setup")

    if st.session_state.session_id is None:
        st.markdown("#### Step 1 — Choose Candidate Persona")
        persona_names = list(display_map.keys())
        current_display = reverse_map.get(st.session_state.selected_persona_file, persona_names[0] if persona_names else "")
        selected_display = st.selectbox("Candidate Persona", persona_names,
            index=persona_names.index(current_display) if current_display in persona_names else 0)
        selected_file = display_map[selected_display]
        if selected_file != st.session_state.selected_persona_file:
            persona = PersonaStore.load_persona_by_file(selected_file)
            st.session_state.selected_persona_file = selected_file
            st.session_state.persona = persona
            st.session_state.persona_editor_text = json.dumps(persona, indent=2, ensure_ascii=False)
            st.rerun()

        st.markdown("#### Step 2 — Choose Difficulty")
        selected_difficulty = st.selectbox("Interview Difficulty", DIFFICULTY_OPTIONS,
            index=DIFFICULTY_OPTIONS.index(st.session_state.selected_difficulty)
            if st.session_state.selected_difficulty in DIFFICULTY_OPTIONS else 0)
        st.session_state.selected_difficulty = selected_difficulty

        if "Low" in selected_difficulty: badge_class, badge_label = "diff-badge-low", "LOW"
        elif "Medium" in selected_difficulty: badge_class, badge_label = "diff-badge-medium", "MEDIUM"
        elif "High" in selected_difficulty: badge_class, badge_label = "diff-badge-high", "HIGH"
        else: badge_class, badge_label = "diff-badge-low", "AUTO"
        st.markdown(f'<div class="{badge_class}">{badge_label}</div>', unsafe_allow_html=True)
        st.caption(DIFFICULTY_DESCRIPTIONS.get(selected_difficulty, ""))

        persona = st.session_state.persona
        voice = get_tts_voice_for_persona(persona.get("name", ""))
        st.caption(f"🔊 Voice: {'Male' if is_male_voice(voice) else 'Female'}")

        with st.expander("Advanced — Edit Persona JSON"):
            edited_text = st.text_area("Persona JSON", value=st.session_state.persona_editor_text, height=300)
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("Apply Edits", use_container_width=True):
                    try:
                        updated = json.loads(edited_text)
                        st.session_state.persona = updated
                        st.session_state.persona_editor_text = json.dumps(updated, indent=2, ensure_ascii=False)
                        st.success("Persona updated.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")
            with col_b:
                if st.button("Save to File", use_container_width=True):
                    try:
                        updated = json.loads(edited_text)
                        PersonaStore.save_persona_by_file(st.session_state.selected_persona_file or "custom.json", updated)
                        st.session_state.persona = updated
                        st.success("Saved.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")

        st.markdown("---")
        if st.button("▶ Start New Interview", use_container_width=True, type="primary"):
            session_id = SessionStore.create_session(
                persona=st.session_state.persona,
                persona_file_name=st.session_state.selected_persona_file,
                difficulty_override=get_difficulty_override(st.session_state.selected_difficulty)
            )
            st.session_state.session_id = session_id
            st.session_state.pending_end_confirmation = False
            st.session_state.latest_audio_file = None
            st.session_state.latest_reply_text = ""
            st.session_state.last_transcribed_question = ""
            st.session_state.edited_question = ""
            st.session_state.transcription_ready = False
            st.success("Session started!")
            st.rerun()

    else:
        session = SessionStore.load_session(st.session_state.session_id)
        pronoun_shifted = session.get("pronoun_shift_triggered", False)
        st.success("✅ Session Active")
        st.write(f"**Persona:** {session.get('persona', {}).get('name', 'Unknown')}")

        st.markdown("---")
        st.markdown("**Difficulty Level**")
        current_diff = session.get("difficulty", "low")
        diff_labels = {"low": "🟢 Low", "medium": "🟡 Medium", "high": "🔴 High"}
        selected_diff = st.select_slider("Adjust difficulty", options=["low","medium","high"],
            value=current_diff, format_func=lambda x: diff_labels.get(x, x))
        if selected_diff != current_diff:
            SessionStore.change_difficulty(st.session_state.session_id, selected_diff)
            st.success(f"Difficulty → **{selected_diff.upper()}**")
            st.rerun()

        st.markdown("---")
        st.markdown("**Candidate Speaking Style**")
        pt = session.get("pronoun_tracking", {})
        total_we, total_i = pt.get("total_we_count", 0), pt.get("total_i_count", 0)
        if pronoun_shifted:
            st.markdown('<div class="pronoun-status-i">✅ Shifted to "I"</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="pronoun-status-we">⚠️ Using "We" predominantly</div>', unsafe_allow_html=True)
        st.caption(f"we={total_we} / I={total_i}")

        st.markdown("---")
        st.markdown("**STAR Coverage**")
        accumulated = session.get("competency_indicators_accumulated", {})
        star_labels = {"situation_explored":"S","task_explored":"T","action_explored":"A",
                       "result_explored":"R","learning_explored":"L","reasoning_explored":"Why"}
        parts = []
        for key, short in star_labels.items():
            cls = "comp-addressed" if accumulated.get(key) else "comp-not-addressed"
            parts.append(f'<span class="{cls}">{short}</span>')
        st.markdown(" ".join(parts), unsafe_allow_html=True)
        st.caption("S=Situation T=Task A=Action R=Result L=Learning Why=Reasoning")

        st.markdown("---")
        metrics = session.get("metrics", {})
        st.metric("Questions Asked", metrics.get("interviewer_turns", 0))
        st.metric("Candidate Responses", metrics.get("candidate_turns", 0))

        if session.get("status") == "active":
            if st.button("⏹ End Interview", type="primary", use_container_width=True):
                st.session_state.pending_end_confirmation = True
                st.rerun()

# ── Pre-session landing ───────────────────────────────────────────────────────
if st.session_state.session_id is None:
    left, right = st.columns([1.3, 1])
    with left:
        with st.container(border=True):
            st.markdown("### How BEI Cape Works")
            for step in [
                "**Choose a persona** — each has different gaps, styles, and behavioral traps.",
                "**Set difficulty** — Low, Medium, or High changes how evasive the candidate is.",
                "**Ask questions by voice** — practice your BEI questioning technique.",
                "**Notice**: the candidate leans toward 'we'. Push for personal contribution.",
                "**Track STAR coverage** in the sidebar — aim to address S, T, A, R, L, Why.",
                "**End the interview** — get a rubric-based score with full breakdown.",
            ]:
                st.write(f"{step}")
    with right:
        with st.container(border=True):
            st.markdown("### Selected Persona")
            persona = st.session_state.persona
            st.write(f"**{persona.get('name', 'No persona selected')}**")
            profile = persona.get("idealized_candidate_profile", {})
            bg = profile.get("professional_background", {})
            if bg.get("industries"):
                st.caption(f"Industries: {', '.join(bg['industries'])}")
            traits = profile.get("behavioral_traits", [])
            if traits:
                st.markdown("**Behavioural Style**")
                for item in traits[:4]:
                    st.write(f"• {item}")
            gaps = profile.get("common_gaps", [])
            if gaps:
                st.markdown("**Common Interview Gaps**")
                for item in gaps[:3]:
                    st.write(f"⚠ {item}")
    st.stop()

# ── Active session — COMPACT header ──────────────────────────────────────────
session = SessionStore.load_session(st.session_state.session_id)

# ── Compact session info bar — no truncation ─────────────────────────────
_status = session.get("status", "NA").title()
_status_icon = "🟢" if session.get("status") == "active" else "✅"
_persona_full = session.get("persona", {}).get("name", "Unknown")
_persona_short = _persona_full.split("—")[0].strip()
_difficulty = session.get("difficulty", "unknown").upper()
_diff_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(_difficulty, "⚪")
_questions = session.get("metrics", {}).get("interviewer_turns", 0)
pronoun_shifted = session.get("pronoun_shift_triggered", False)
_mode = "Personal (I)" if pronoun_shifted else "Team (We)"

st.markdown(f"""
<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
  <div style="flex:1;min-width:100px;background:rgba(79,142,247,0.07);border:1px solid rgba(79,142,247,0.2);border-radius:8px;padding:8px 12px;">
    <div style="font-size:0.68rem;opacity:0.6;margin-bottom:2px;">STATUS</div>
    <div style="font-size:0.88rem;font-weight:700;">{_status_icon} {_status}</div>
  </div>
  <div style="flex:2;min-width:160px;background:rgba(79,142,247,0.07);border:1px solid rgba(79,142,247,0.2);border-radius:8px;padding:8px 12px;">
    <div style="font-size:0.68rem;opacity:0.6;margin-bottom:2px;">PERSONA</div>
    <div style="font-size:0.88rem;font-weight:700;">{_persona_short}</div>
  </div>
  <div style="flex:1;min-width:100px;background:rgba(79,142,247,0.07);border:1px solid rgba(79,142,247,0.2);border-radius:8px;padding:8px 12px;">
    <div style="font-size:0.68rem;opacity:0.6;margin-bottom:2px;">DIFFICULTY</div>
    <div style="font-size:0.88rem;font-weight:700;">{_diff_emoji} {_difficulty}</div>
  </div>
  <div style="flex:1;min-width:80px;background:rgba(79,142,247,0.07);border:1px solid rgba(79,142,247,0.2);border-radius:8px;padding:8px 12px;">
    <div style="font-size:0.68rem;opacity:0.6;margin-bottom:2px;">QUESTIONS</div>
    <div style="font-size:0.88rem;font-weight:700;">{_questions}</div>
  </div>
  <div style="flex:1;min-width:110px;background:rgba(79,142,247,0.07);border:1px solid rgba(79,142,247,0.2);border-radius:8px;padding:8px 12px;">
    <div style="font-size:0.68rem;opacity:0.6;margin-bottom:2px;">MODE</div>
    <div style="font-size:0.88rem;font-weight:700;">{_mode}</div>
  </div>
</div>
""", unsafe_allow_html=True)

st.markdown("---")

# ── End confirmation ──────────────────────────────────────────────────────────
if st.session_state.pending_end_confirmation and session["status"] == "active":
    if engine.should_show_continue_popup(st.session_state.session_id):
        st.warning("⚠️ Short session — recommend at least 5–6 questions for a richer evaluation.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("▶ Continue Interview", use_container_width=True):
                st.session_state.pending_end_confirmation = False
                st.rerun()
        with c2:
            if st.button("⏹ End Anyway & Generate Feedback", use_container_width=True):
                bar = st.progress(0, text="Analysing...")
                bar.progress(20, text="Scoring against rubric...")
                engine.generate_final_report(st.session_state.session_id)
                bar.progress(80, text="Building report...")
                import time as _t; _t.sleep(0.3)
                bar.progress(100, text="Done!")
                st.session_state.pending_end_confirmation = False
                st.rerun()
    else:
        bar = st.progress(0, text="Analysing...")
        bar.progress(20, text="Scoring against rubric...")
        engine.generate_final_report(st.session_state.session_id)
        bar.progress(80, text="Building report...")
        import time as _t; _t.sleep(0.3)
        bar.progress(100, text="Done!")
        st.session_state.pending_end_confirmation = False
        st.rerun()

# ── Main tabs ─────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["🎙️ Interview", "📄 Transcript"])

with tab1:
    if session["status"] == "completed":
        st.success("✅ Interview completed.")
        render_candidate_summary(session.get("final_report", {}))
    else:
        # ── Voice + EDITABLE transcription ────────────────────────────────
        with st.container(border=True):
            st.markdown("### Ask the Candidate by Voice")
            st.caption("① Record → ② Transcribe → ③ **Edit if needed** → ④ Send")

            audio_value = st.audio_input("🎤 Record your question")

            col1, col2 = st.columns([1, 1])
            with col1:
                transcribe_clicked = st.button("🔤 Transcribe", use_container_width=True)
            with col2:
                clear_clicked = st.button("🗑 Clear", use_container_width=True)

            if clear_clicked:
                st.session_state.last_transcribed_question = ""
                st.session_state.edited_question = ""
                st.session_state.transcription_ready = False
                st.rerun()

            if transcribe_clicked:
                if audio_value is None:
                    st.warning("Please record a question first.")
                else:
                    with st.spinner("Transcribing..."):
                        question_text = transcribe_audio(audio_value)
                    if not question_text:
                        st.error("Could not transcribe clearly. Please try again.")
                    else:
                        st.session_state.last_transcribed_question = question_text
                        st.session_state.edited_question = question_text
                        st.session_state.transcription_ready = True
                        st.rerun()

            # Editable box — only shown after transcription
            if st.session_state.transcription_ready:
                st.markdown("**✏️ Edit your question if needed, then send:**")
                edited = st.text_area(
                    "question_edit",
                    value=st.session_state.edited_question,
                    height=80,
                    label_visibility="collapsed",
                    placeholder="Your transcribed question appears here — edit freely before sending"
                )
                st.session_state.edited_question = edited

                if st.button("📤 Send Question", use_container_width=True, type="primary"):
                    final_question = st.session_state.edited_question.strip()
                    if not final_question:
                        st.warning("Question is empty. Please type or re-record.")
                    else:
                        with st.spinner("Candidate is thinking..."):
                            response = engine.ask_candidate(st.session_state.session_id, final_question)

                        st.markdown("**Candidate Response:**")
                        placeholder = st.empty()
                        typed = ""
                        for word in response["reply_text"].split():
                            typed += word + " "
                            placeholder.markdown(f"*{typed.strip()}*")
                            time.sleep(0.04)

                        updated_session = SessionStore.load_session(st.session_state.session_id)
                        candidate_turns = [m for m in updated_session["conversation"] if m["role"] == "candidate"]
                        audio_file = generate_tts_file(
                            text=response["reply_text"],
                            session_id=st.session_state.session_id,
                            candidate_turn_index=len(candidate_turns),
                            persona_name=updated_session.get("persona", {}).get("name", ""),
                        )
                        engine.attach_audio_to_latest_candidate_turn(st.session_state.session_id, audio_file)
                        st.session_state.latest_audio_file = audio_file

                        # Reset for next question
                        st.session_state.transcription_ready = False
                        st.session_state.edited_question = ""
                        st.session_state.last_transcribed_question = ""
                        st.rerun()

        if st.session_state.latest_audio_file:
            with st.container(border=True):
                st.markdown("**Latest Candidate Response — Audio**")
                autoplay_audio_file(st.session_state.latest_audio_file)

        session = SessionStore.load_session(st.session_state.session_id)
        render_dynamic_probing_tip(session)

with tab2:
    render_transcript(session)
