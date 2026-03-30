# app_candidate.py
# BEI Cape — Candidate Interview Interface v2
# Fixes: gender-aware TTS, dynamic probing tips (not constant), small talk handling,
#         competency indicators tracking, human-like responses

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
.block-container {
    padding-top: 1.2rem;
    padding-bottom: 2rem;
    max-width: 1280px;
}
.mode-pill {
    display: inline-block;
    padding: 0.35rem 0.9rem;
    border-radius: 999px;
    border: 1.5px solid #4f8ef7;
    font-size: 0.82rem;
    font-weight: 700;
    color: #4f8ef7;
    margin-bottom: 0.8rem;
    letter-spacing: 0.04em;
}
.big-title {
    font-size: 2rem;
    font-weight: 800;
    margin-bottom: 0.15rem;
    letter-spacing: -0.01em;
}
.subtle {
    opacity: 0.72;
    margin-bottom: 1.2rem;
    font-size: 0.95rem;
}
.pronoun-status-we {
    padding: 0.3rem 0.8rem;
    border-radius: 6px;
    background: rgba(255, 170, 0, 0.12);
    border: 1px solid rgba(255, 170, 0, 0.4);
    font-size: 0.82rem;
    color: #e8a800;
    font-weight: 600;
}
.pronoun-status-i {
    padding: 0.3rem 0.8rem;
    border-radius: 6px;
    background: rgba(40, 200, 100, 0.12);
    border: 1px solid rgba(40, 200, 100, 0.4);
    font-size: 0.82rem;
    color: #28c864;
    font-weight: 600;
}
.diff-badge-low {
    display: inline-block;
    padding: 0.2rem 0.7rem;
    border-radius: 999px;
    background: rgba(40, 200, 100, 0.15);
    border: 1px solid rgba(40, 200, 100, 0.4);
    color: #28c864;
    font-size: 0.8rem;
    font-weight: 700;
}
.diff-badge-medium {
    display: inline-block;
    padding: 0.2rem 0.7rem;
    border-radius: 999px;
    background: rgba(255, 170, 0, 0.15);
    border: 1px solid rgba(255, 170, 0, 0.4);
    color: #e8a800;
    font-size: 0.8rem;
    font-weight: 700;
}
.diff-badge-high {
    display: inline-block;
    padding: 0.2rem 0.7rem;
    border-radius: 999px;
    background: rgba(220, 50, 50, 0.12);
    border: 1px solid rgba(220, 50, 50, 0.35);
    color: #dc3232;
    font-size: 0.8rem;
    font-weight: 700;
}
.comp-addressed {
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    background: rgba(40, 200, 100, 0.12);
    border: 1px solid rgba(40, 200, 100, 0.3);
    font-size: 0.75rem;
    color: #28c864;
    display: inline-block;
    margin: 2px;
}
.comp-not-addressed {
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    background: rgba(220, 50, 50, 0.08);
    border: 1px solid rgba(220, 50, 50, 0.25);
    font-size: 0.75rem;
    color: #dc3232;
    display: inline-block;
    margin: 2px;
}
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


def get_difficulty_override(selected: str) -> str | None:
    if selected == "Auto (based on session count)":
        return None
    return selected.lower()


def get_persona_options():
    return PersonaStore.list_persona_files()


def load_persona_by_selection(file_name: str):
    return PersonaStore.load_persona_by_file(file_name)


def init_state():
    persona_options = get_persona_options()
    default_file = persona_options[0]["file_name"] if persona_options else None
    default_persona = load_persona_by_selection(default_file) if default_file else {}

    defaults = {
        "app_mode": APP_MODE,
        "session_id": None,
        "selected_persona_file": default_file,
        "persona": default_persona,
        "persona_editor_text": json.dumps(default_persona, indent=2, ensure_ascii=False) if default_persona else "{}",
        "selected_difficulty": DIFFICULTY_OPTIONS[0],
        "pending_end_confirmation": False,
        "last_transcribed_question": "",
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
        text = recognizer.recognize_google(audio_data)
        return text.strip()
    except sr.UnknownValueError:
        return ""
    except Exception:
        return ""
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


async def _save_edge_tts_async(text: str, output_file: str, voice: str = "en-US-AriaNeural"):
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(output_file)


def generate_tts_file(text: str, session_id: str, candidate_turn_index: int, persona_name: str = "") -> str:
    """Generate TTS with gender-appropriate voice based on persona."""
    output_file = os.path.join(AUDIO_DIR, f"{session_id}_candidate_{candidate_turn_index}.mp3")
    if not os.path.exists(output_file):
        voice = get_tts_voice_for_persona(persona_name)
        asyncio.run(_save_edge_tts_async(text=text, output_file=output_file, voice=voice))
    return output_file


def autoplay_audio_file(audio_file: str):
    if not audio_file or not os.path.exists(audio_file):
        return
    with open(audio_file, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    audio_html = f"""
    <audio autoplay controls style="width:100%; margin-top:8px;">
        <source src="data:audio/mp3;base64,{b64}" type="audio/mp3">
    </audio>
    """
    components.html(audio_html, height=60)


def render_audio_controls(audio_file: str):
    if not audio_file or not os.path.exists(audio_file):
        return
    with open(audio_file, "rb") as f:
        st.audio(f.read(), format="audio/mp3")


def render_transcript(session: dict):
    st.markdown("### Interview Transcript")
    if not session["conversation"]:
        st.info("The interview has not started yet. Record a question to begin.")
        return

    for msg in session["conversation"]:
        role = msg["role"]
        ts = msg.get("elapsed_mmss", msg.get("timestamp", ""))
        if role == "interviewer":
            with st.chat_message("user"):
                label = "Interviewer"
                if msg.get("is_small_talk"):
                    label += " 💬"
                elif msg.get("is_behavioral"):
                    label += " 🎯"
                st.markdown(f"**{label} [{ts}]**  \n{msg['content']}")
        else:
            with st.chat_message("assistant"):
                pronoun_info = msg.get("pronoun_counts", {})
                we_c = pronoun_info.get("we_count", 0)
                i_c = pronoun_info.get("i_count", 0)
                pronoun_tag = f" (we:{we_c} / I:{i_c})" if (we_c + i_c) > 0 else ""
                st.markdown(f"**Candidate [{ts}]{pronoun_tag}**  \n{msg['content']}")
                if msg.get("audio_file"):
                    render_audio_controls(msg["audio_file"])


def render_score_breakdown(report: dict):
    breakdown = report.get("score_breakdown_matrix", [])
    if not breakdown:
        return

    st.markdown("### How Your Score Was Calculated")
    st.caption(
        "Each parameter is scored 1–5 using the competency rubric and multiplied by its weight. "
        "The contributions are summed to produce the final percentage."
    )

    import pandas as pd
    df = pd.DataFrame(breakdown)

    def highlight_total(row):
        if row["Parameter"] == "TOTAL":
            return ["font-weight: bold; background-color: rgba(79,142,247,0.1)"] * len(row)
        return [""] * len(row)

    try:
        styled = df.style.apply(highlight_total, axis=1)
        st.dataframe(styled, use_container_width=True, hide_index=True)
    except Exception:
        st.dataframe(df, use_container_width=True, hide_index=True)

    with st.expander("Formula used"):
        st.markdown("""
**Score formula:**
```
Weighted Contribution = (Score / 5) × Weight
Final Score % = Sum of all Weighted Contributions
```

**Example (Depth of Probing):**
Score = 3/5, Weight = 20%
→ Contribution = (3 ÷ 5) × 20 = **12.0**

If every parameter scored 3/5, the total would be **60%**.
        """)


def render_competency_addressed(report: dict):
    """Show which competency indicators were addressed vs not addressed."""
    comp_summary = report.get("competency_addressed_summary", {})
    if not comp_summary:
        return

    st.markdown("### Competency Indicators Addressed")
    st.caption(f"STAR+Learning completeness: {comp_summary.get('star_completeness_pct', 0)}%")

    addressed = comp_summary.get("addressed", [])
    not_addressed = comp_summary.get("not_addressed", [])

    html_parts = []
    for item in addressed:
        html_parts.append(f'<span class="comp-addressed">✓ {item}</span>')
    for item in not_addressed:
        html_parts.append(f'<span class="comp-not-addressed">✗ {item}</span>')

    st.markdown(" ".join(html_parts), unsafe_allow_html=True)


def render_candidate_summary(report: dict):
    candidate_summary = report.get("candidate_summary", {})
    assessor_feedback = report.get("assessor_feedback", {})

    st.markdown("## Your Interview Feedback")

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Final Score", f"{report.get('final_score_percent', 0)}%")
    with c2:
        readiness = report.get("readiness_status", "NA")
        color = "🟢" if readiness == "Ready" else "🟡"
        st.metric("Readiness", f"{color} {readiness}")

    # Competency addressed summary
    with st.container(border=True):
        render_competency_addressed(report)

    # Score breakdown
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
            strengths = candidate_summary.get("strengths", [])
            for item in (strengths or ["No strengths captured."]):
                st.write(f"✓ {item}")

    with col2:
        with st.container(border=True):
            st.markdown("### Areas to Improve")
            improvements = candidate_summary.get("improvements", [])
            for item in (improvements or ["No improvement areas captured."]):
                st.write(f"→ {item}")

    with st.container(border=True):
        st.markdown("### Assessor Feedback Snapshot")
        sub1, sub2 = st.columns(2)

        with sub1:
            st.markdown("**What Worked**")
            for item in assessor_feedback.get("strengths", []):
                st.write(f"✓ {item}")

        with sub2:
            st.markdown("**What To Improve**")
            for item in assessor_feedback.get("missed_probes", []):
                st.write(f"→ {item}")

        st.markdown("**Probing Quality Assessment**")
        st.write(assessor_feedback.get("probing_quality", ""))

    with st.container(border=True):
        st.markdown("### Better Questions You Could Have Asked")
        for q in assessor_feedback.get("better_questions", []):
            st.write(f"💡 *{q}*")


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic Probing Tips (NOT constant — changes based on actual session state)
# ─────────────────────────────────────────────────────────────────────────────

def render_dynamic_probing_tip(session: dict):
    """
    Render a contextual probing tip based on ACTUAL session state.
    This fixes the bug where the tip was constant regardless of interviewer actions.
    """
    pronoun_shifted = session.get("pronoun_shift_triggered", False)
    metrics = session.get("metrics", {})
    accumulated = session.get("competency_indicators_accumulated", {})
    interviewer_turns = metrics.get("interviewer_turns", 0)
    probe_count = metrics.get("probe_like_questions", 0)

    # Determine what's missing
    missing_indicators = [k for k, v in accumulated.items() if not v]
    addressed_indicators = [k for k, v in accumulated.items() if v]

    if interviewer_turns == 0:
        # Not started yet
        with st.container(border=True):
            st.markdown("**💡 Getting Started**")
            st.write("Start with a warm greeting, then move to a behavioral question like: "
                     "*'Tell me about a time when you faced a challenging situation at work.'*")
        return

    if interviewer_turns <= 2 and not pronoun_shifted:
        # Early stage — encourage behavioral questioning
        with st.container(border=True):
            st.markdown("**💡 Tip: Move to Behavioral Questions**")
            st.write("Good start! Now try a behavioral question: "
                     "*'Can you walk me through a specific example of when you had to handle a difficult situation?'*")
        return

    if not pronoun_shifted:
        # Mid-interview — candidate still using "we"
        pronoun_tracking = session.get("pronoun_tracking", {})
        total_we = pronoun_tracking.get("total_we_count", 0)
        total_i = pronoun_tracking.get("total_i_count", 0)

        if total_we > total_i * 2:
            with st.container(border=True):
                st.markdown("**⚠️ Pronoun Alert: Candidate using 'We' heavily**")
                st.write(
                    f"The candidate has used 'we/our/team' **{total_we} times** vs 'I/my/me' **{total_i} times**. "
                    "Push for personal ownership: *'What did YOU specifically decide or do in that situation?'*"
                )
        else:
            with st.container(border=True):
                st.markdown("**💡 Tip: Probe Deeper**")
                st.write("The candidate is giving mixed pronouns. Try: "
                         "*'Can you focus specifically on your own contribution here?'*")
        return

    # Pronoun shift has been triggered — now give specific guidance based on gaps
    if pronoun_shifted:
        if "result_explored" in missing_indicators:
            with st.container(border=True):
                st.markdown("**✅ Personal Probing Active — Now Push for Results**")
                st.write("Good, the candidate is using 'I' now. But you haven't explored outcomes yet. Try: "
                         "*'What was the measurable result of your action?'* or *'How did you know it worked?'*")
        elif "learning_explored" in missing_indicators:
            with st.container(border=True):
                st.markdown("**✅ Personal Probing Active — Explore Learning**")
                st.write("You've covered actions and results. Now explore reflection: "
                         "*'Looking back, what would you do differently?'* or *'What did you learn from that experience?'*")
        elif "reasoning_explored" in missing_indicators:
            with st.container(border=True):
                st.markdown("**✅ Personal Probing Active — Explore Reasoning**")
                st.write("Try understanding the 'why': "
                         "*'What was your thinking behind that approach?'* or *'Why did you choose that route?'*")
        elif len(missing_indicators) == 0:
            with st.container(border=True):
                st.markdown("**🎯 Excellent Coverage**")
                st.write("You've addressed all major competency indicators. "
                         "Consider wrapping up or exploring a second behavioral example for depth.")
        else:
            missing_labels = [i.replace("_explored", "").replace("_", " ").title() for i in missing_indicators]
            with st.container(border=True):
                st.markdown("**✅ Personal Probing Active — Fill Remaining Gaps**")
                st.write(f"Still missing: **{', '.join(missing_labels)}**. "
                         "Keep probing to complete the behavioral picture.")


# ─────────────────────────────────────────────────────────────────────────────
# Init
# ─────────────────────────────────────────────────────────────────────────────
init_state()

persona_options = get_persona_options()
display_map = {row["display_name"]: row["file_name"] for row in persona_options}
reverse_map = {row["file_name"]: row["display_name"] for row in persona_options}

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Interview Setup")

    if st.session_state.session_id is None:
        # ── Persona selection ──────────────────────────────────────────────
        st.markdown("#### Step 1 — Choose Candidate Persona")
        persona_names = list(display_map.keys())
        current_display = reverse_map.get(
            st.session_state.selected_persona_file,
            persona_names[0] if persona_names else ""
        )

        selected_display = st.selectbox(
            "Candidate Persona",
            persona_names,
            index=persona_names.index(current_display) if current_display in persona_names else 0,
            help="Each persona has a different background, communication style, and set of interview gaps."
        )

        selected_file = display_map[selected_display]
        if selected_file != st.session_state.selected_persona_file:
            persona = load_persona_by_selection(selected_file)
            st.session_state.selected_persona_file = selected_file
            st.session_state.persona = persona
            st.session_state.persona_editor_text = json.dumps(persona, indent=2, ensure_ascii=False)
            st.rerun()

        # ── Difficulty selection ───────────────────────────────────────────
        st.markdown("#### Step 2 — Choose Difficulty")
        selected_difficulty = st.selectbox(
            "Interview Difficulty",
            DIFFICULTY_OPTIONS,
            index=DIFFICULTY_OPTIONS.index(st.session_state.selected_difficulty)
            if st.session_state.selected_difficulty in DIFFICULTY_OPTIONS else 0,
            help="Controls how evasive and vague the AI candidate will be."
        )
        st.session_state.selected_difficulty = selected_difficulty

        diff_desc = DIFFICULTY_DESCRIPTIONS.get(selected_difficulty, "")
        if "Low" in selected_difficulty:
            badge_class = "diff-badge-low"
            badge_label = "LOW"
        elif "Medium" in selected_difficulty:
            badge_class = "diff-badge-medium"
            badge_label = "MEDIUM"
        elif "High" in selected_difficulty:
            badge_class = "diff-badge-high"
            badge_label = "HIGH"
        else:
            badge_class = "diff-badge-low"
            badge_label = "AUTO"

        st.markdown(f'<div class="{badge_class}">{badge_label}</div>', unsafe_allow_html=True)
        st.caption(diff_desc)

        # Show gender/voice info
        persona = st.session_state.persona
        persona_gender = persona.get("gender", "unknown")
        voice = get_tts_voice_for_persona(persona.get("name", ""))
        st.caption(f"🔊 Voice: {voice} ({'Male' if is_male_voice(voice) else 'Female'})")

        # ── Advanced persona editor ────────────────────────────────────────
        with st.expander("Advanced — Edit Persona JSON"):
            edited_text = st.text_area(
                "Persona JSON",
                value=st.session_state.persona_editor_text,
                height=300
            )
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("Apply Edits", use_container_width=True):
                    try:
                        updated_persona = json.loads(edited_text)
                        st.session_state.persona = updated_persona
                        st.session_state.persona_editor_text = json.dumps(updated_persona, indent=2, ensure_ascii=False)
                        st.success("Persona updated.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")
            with col_b:
                if st.button("Save to File", use_container_width=True):
                    try:
                        updated_persona = json.loads(edited_text)
                        file_name = st.session_state.selected_persona_file or "custom_persona.json"
                        PersonaStore.save_persona_by_file(file_name, updated_persona)
                        st.session_state.persona = updated_persona
                        st.session_state.persona_editor_text = json.dumps(updated_persona, indent=2, ensure_ascii=False)
                        st.success("Persona file saved.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")

        st.markdown("---")
        if st.button("▶ Start New Interview", use_container_width=True, type="primary"):
            difficulty_override = get_difficulty_override(st.session_state.selected_difficulty)
            session_id = SessionStore.create_session(
                persona=st.session_state.persona,
                persona_file_name=st.session_state.selected_persona_file,
                difficulty_override=difficulty_override
            )
            st.session_state.session_id = session_id
            st.session_state.pending_end_confirmation = False
            st.session_state.latest_audio_file = None
            st.session_state.latest_reply_text = ""
            st.session_state.last_transcribed_question = ""
            st.success("Session started. Go to the Interview tab.")
            st.rerun()

    else:
        # Active session sidebar
        session = SessionStore.load_session(st.session_state.session_id)
        difficulty = session.get("difficulty", "unknown").upper()
        pronoun_shifted = session.get("pronoun_shift_triggered", False)

        st.success("✅ Session Active")
        st.write(f"**Persona:** {session.get('persona', {}).get('name', 'Unknown')}")
        st.markdown("**Competency:** Hidden during interview")

        # ── LIVE DIFFICULTY SLIDER ─────────────────────────────────────────
        st.markdown("---")
        st.markdown("**Difficulty Level**")
        current_diff = session.get("difficulty", "low")
        diff_options = ["low", "medium", "high"]
        diff_labels = {"low": "🟢 Low", "medium": "🟡 Medium", "high": "🔴 High"}
        diff_descriptions = {
            "low": "Cooperative, clear answers. Good for warm-up.",
            "medium": "Context-heavy, vague on specifics. Requires probing.",
            "high": "Guarded, evasive, avoids outcomes. Strong probing needed.",
        }

        selected_diff = st.select_slider(
            "Adjust difficulty",
            options=diff_options,
            value=current_diff,
            format_func=lambda x: diff_labels.get(x, x),
            help="Changing difficulty mid-session immediately affects the AI candidate's next response."
        )
        st.caption(diff_descriptions.get(selected_diff, ""))

        if selected_diff != current_diff:
            SessionStore.change_difficulty(st.session_state.session_id, selected_diff)
            st.success(f"Difficulty changed to **{selected_diff.upper()}**")
            st.rerun()

        # Pronoun arc status
        st.markdown("---")
        st.markdown("**Candidate Speaking Style**")
        pronoun_tracking = session.get("pronoun_tracking", {})
        total_we = pronoun_tracking.get("total_we_count", 0)
        total_i = pronoun_tracking.get("total_i_count", 0)

        if pronoun_shifted:
            st.markdown('<div class="pronoun-status-i">✅ Shifted to "I" — personal probing worked</div>', unsafe_allow_html=True)
            st.caption(f"Pronoun balance: we={total_we} / I={total_i}")
        else:
            st.markdown('<div class="pronoun-status-we">⚠️ Using "We" predominantly</div>', unsafe_allow_html=True)
            st.caption(f"Pronoun balance: we={total_we} / I={total_i}")

        # Competency indicators progress
        st.markdown("---")
        st.markdown("**STAR Coverage**")
        accumulated = session.get("competency_indicators_accumulated", {})
        star_labels = {
            "situation_explored": "S",
            "task_explored": "T",
            "action_explored": "A",
            "result_explored": "R",
            "learning_explored": "L",
            "reasoning_explored": "Why",
        }
        progress_parts = []
        for key, short_label in star_labels.items():
            if accumulated.get(key, False):
                progress_parts.append(f'<span class="comp-addressed">{short_label}</span>')
            else:
                progress_parts.append(f'<span class="comp-not-addressed">{short_label}</span>')
        st.markdown(" ".join(progress_parts), unsafe_allow_html=True)
        st.caption("S=Situation T=Task A=Action R=Result L=Learning Why=Reasoning")

        st.markdown("---")
        metrics = session.get("metrics", {})
        st.metric("Questions Asked", metrics.get("interviewer_turns", 0))
        st.metric("Candidate Responses", metrics.get("candidate_turns", 0))

        if session.get("status") == "active":
            if st.button("⏹ End Interview", type="primary", use_container_width=True):
                st.session_state.pending_end_confirmation = True
                st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Pre-session landing page
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.session_id is None:
    left, right = st.columns([1.3, 1])

    with left:
        with st.container(border=True):
            st.markdown("### How BEI Cape Works")
            st.write("1. **Choose a persona** — each has different gaps, communication styles, and behavioral traps.")
            st.write("2. **Set difficulty** — Low, Medium, or High changes how evasive the candidate is.")
            st.write("3. **Ask questions by voice** — practice your BEI questioning technique.")
            st.write("4. **Notice**: the candidate will lean toward 'we' by default. Push for personal contribution.")
            st.write("5. **Track STAR coverage** in the sidebar — aim to address S, T, A, R, L, and Why.")
            st.write("6. **End the interview** — get a rubric-based score with full breakdown.")

        with st.container(border=True):
            st.markdown("### Difficulty Guide")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown('<div class="diff-badge-low">LOW</div>', unsafe_allow_html=True)
                st.caption("Cooperative. Some probing needed.")
            with col2:
                st.markdown('<div class="diff-badge-medium">MEDIUM</div>', unsafe_allow_html=True)
                st.caption("Vague. Needs active probing for specifics.")
            with col3:
                st.markdown('<div class="diff-badge-high">HIGH</div>', unsafe_allow_html=True)
                st.caption("Guarded. Strong probing required.")

    with right:
        with st.container(border=True):
            st.markdown("### Selected Persona")
            persona = st.session_state.persona
            st.write(f"**{persona.get('name', 'No persona selected')}**")

            gender = persona.get("gender", "unknown")
            voice = get_tts_voice_for_persona(persona.get("name", ""))
            st.caption(f"Gender: {gender.title()} | Voice: {'Male' if is_male_voice(voice) else 'Female'}")

            profile = persona.get("idealized_candidate_profile", {})
            bg = profile.get("professional_background", {})
            if bg.get("industries"):
                st.caption(f"Industries: {', '.join(bg['industries'])}")
            if bg.get("progression"):
                st.caption(bg["progression"])

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

# ─────────────────────────────────────────────────────────────────────────────
# Active session header metrics
# ─────────────────────────────────────────────────────────────────────────────
session = SessionStore.load_session(st.session_state.session_id)

top1, top2, top3, top4, top5 = st.columns(5)
with top1:
    status_icon = "🟢" if session.get("status") == "active" else "✅"
    st.metric("Status", f"{status_icon} {session.get('status', 'NA').title()}")
with top2:
    st.metric("Persona", session.get("persona", {}).get("name", "Unknown")[:28])
with top3:
    diff_display = session.get("difficulty", "unknown").upper()
    diff_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(diff_display, "⚪")
    st.metric("Difficulty", f"{diff_emoji} {diff_display}")
with top4:
    turns = session.get("metrics", {}).get("interviewer_turns", 0)
    st.metric("Questions Asked", turns)
with top5:
    pronoun_shifted = session.get("pronoun_shift_triggered", False)
    st.metric("Candidate Mode", "Personal (I)" if pronoun_shifted else "Team (We)")

st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# End confirmation / warning
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.pending_end_confirmation and session["status"] == "active":
    if engine.should_show_continue_popup(st.session_state.session_id):
        st.warning(
            "⚠️ This interview is quite short. A longer session with more probing will produce a richer, "
            "more accurate evaluation. We recommend at least 5–6 questions."
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("▶ Continue Interview", use_container_width=True):
                st.session_state.pending_end_confirmation = False
                st.rerun()
        with c2:
            if st.button("⏹ End Anyway & Generate Feedback", use_container_width=True):
                progress_bar = st.progress(0, text="Analysing transcript...")
                progress_bar.progress(20, text="Scoring against rubric...")
                engine.generate_final_report(st.session_state.session_id)
                progress_bar.progress(80, text="Building report...")
                import time as _time; _time.sleep(0.3)
                progress_bar.progress(100, text="Done!")
                st.session_state.pending_end_confirmation = False
                st.success("Interview ended. Feedback is ready below.")
                st.rerun()
    else:
        progress_bar = st.progress(0, text="Analysing transcript...")
        progress_bar.progress(20, text="Scoring against rubric...")
        engine.generate_final_report(st.session_state.session_id)
        progress_bar.progress(80, text="Building report...")
        import time as _time; _time.sleep(0.3)
        progress_bar.progress(100, text="Done!")
        st.session_state.pending_end_confirmation = False
        st.success("Interview ended. Feedback is ready below.")
        st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Main tabs
# ─────────────────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["🎙️ Interview", "📄 Transcript"])

with tab1:
    if session["status"] == "completed":
        st.success("✅ Interview completed.")
        report = session.get("final_report", {})
        render_candidate_summary(report)

    else:
        # ── Live interview panel ───────────────────────────────────────────
        with st.container(border=True):
            st.markdown("### Ask the Candidate by Voice")
            st.caption("Record your BEI question, then click **Transcribe + Send** to get the candidate's response.")

            audio_value = st.audio_input("🎤 Record your question")

            col1, col2 = st.columns([1, 1])
            with col1:
                process_clicked = st.button("📤 Transcribe + Send", use_container_width=True, type="primary")
            with col2:
                clear_clicked = st.button("🗑 Clear", use_container_width=True)

            if clear_clicked:
                st.session_state.last_transcribed_question = ""
                st.rerun()

            if process_clicked:
                if audio_value is None:
                    st.warning("Please record a question first.")
                else:
                    with st.spinner("Transcribing..."):
                        question_text = transcribe_audio(audio_value)

                    if not question_text:
                        st.error("Could not transcribe the audio clearly. Please try again.")
                    else:
                        st.session_state.last_transcribed_question = question_text

                        with st.container(border=True):
                            st.markdown("**Your question (transcribed):**")
                            st.info(question_text)

                        with st.spinner("Candidate is thinking..."):
                            response = engine.ask_candidate(st.session_state.session_id, question_text)

                        # Typing effect for candidate reply
                        st.markdown("**Candidate Response:**")
                        stream_placeholder = st.empty()
                        typed = ""
                        for word in response["reply_text"].split():
                            typed += word + " "
                            stream_placeholder.markdown(f"*{typed.strip()}*")
                            time.sleep(0.04)

                        # Generate TTS with gender-appropriate voice
                        updated_session = SessionStore.load_session(st.session_state.session_id)
                        candidate_turns = [
                            m for m in updated_session["conversation"] if m["role"] == "candidate"
                        ]
                        candidate_turn_index = len(candidate_turns)
                        persona_name = updated_session.get("persona", {}).get("name", "")

                        audio_file = generate_tts_file(
                            text=response["reply_text"],
                            session_id=st.session_state.session_id,
                            candidate_turn_index=candidate_turn_index,
                            persona_name=persona_name,
                        )
                        engine.attach_audio_to_latest_candidate_turn(st.session_state.session_id, audio_file)
                        st.session_state.latest_audio_file = audio_file
                        st.session_state.latest_reply_text = response["reply_text"]

                        st.rerun()

        # Last transcribed question reminder
        if st.session_state.last_transcribed_question:
            st.caption(f"**Last question:** {st.session_state.last_transcribed_question}")

        # Latest response audio player
        if st.session_state.latest_audio_file:
            with st.container(border=True):
                st.markdown("**Latest Candidate Response — Audio Playback**")
                autoplay_audio_file(st.session_state.latest_audio_file)

        # Dynamic probing tips (FIX: no longer constant)
        # Re-load session to get latest state
        session = SessionStore.load_session(st.session_state.session_id)
        render_dynamic_probing_tip(session)

with tab2:
    render_transcript(session)