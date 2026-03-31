# app_assessor.py
# BEI Cape — Assessor Review Interface v3
# Additions: interviewer audio playback in transcript, revised rubric display,
#            blended AI+rubric feedback, improved evidence presentation

import json
import os
import base64
import asyncio
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
import edge_tts

from bei_engine import (
    BEIEngine,
    PersonaStore,
    SessionStore,
    COMPETENCY_RUBRIC,
    get_tts_voice_for_persona,
    INTERVIEWER_TTS_VOICE,
)

APP_MODE = "assessor"

st.set_page_config(
    page_title="BEI Cape — Assessor Review",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.block-container {
    padding-top: 1.2rem;
    padding-bottom: 2rem;
    max-width: 1320px;
}
.mode-pill {
    display: inline-block;
    padding: 0.35rem 0.9rem;
    border-radius: 999px;
    border: 1.5px solid #a855f7;
    font-size: 0.82rem;
    font-weight: 700;
    color: #a855f7;
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
.comp-addressed {
    padding: 0.2rem 0.6rem;
    border-radius: 4px;
    background: rgba(40, 200, 100, 0.12);
    border: 1px solid rgba(40, 200, 100, 0.3);
    font-size: 0.8rem;
    color: #28c864;
    display: inline-block;
    margin: 2px;
    font-weight: 600;
}
.comp-not-addressed {
    padding: 0.2rem 0.6rem;
    border-radius: 4px;
    background: rgba(220, 50, 50, 0.08);
    border: 1px solid rgba(220, 50, 50, 0.25);
    font-size: 0.8rem;
    color: #dc3232;
    display: inline-block;
    margin: 2px;
    font-weight: 600;
}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="mode-pill">🔍 Assessor Review Mode</div>', unsafe_allow_html=True)
st.markdown('<div class="big-title">BEI Cape</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Review completed sessions, inspect rubric-based evaluations, manage personas, and analyse interviewer performance.</div>', unsafe_allow_html=True)

engine = BEIEngine()

# ── Audio directory for assessor-generated interviewer audio ──────────────
ASSESSOR_AUDIO_DIR = os.path.join("sessions", "audio", "assessor")
os.makedirs(ASSESSOR_AUDIO_DIR, exist_ok=True)


async def _generate_tts_async(text: str, output_file: str, voice: str):
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(output_file)


def generate_interviewer_audio(text: str, session_id: str, turn_index: int) -> str:
    """Generate TTS audio for an interviewer question using interviewer voice."""
    output_file = os.path.join(ASSESSOR_AUDIO_DIR, f"{session_id}_interviewer_{turn_index}.mp3")
    if not os.path.exists(output_file):
        asyncio.run(_generate_tts_async(text=text, output_file=output_file, voice=INTERVIEWER_TTS_VOICE))
    return output_file


def generate_candidate_audio_if_missing(text: str, session_id: str, turn_index: int, persona_name: str) -> str:
    """Generate TTS audio for a candidate reply if the audio file is missing."""
    candidate_audio_dir = os.path.join("sessions", "audio")
    os.makedirs(candidate_audio_dir, exist_ok=True)
    output_file = os.path.join(candidate_audio_dir, f"{session_id}_candidate_{turn_index}.mp3")
    if not os.path.exists(output_file):
        voice = get_tts_voice_for_persona(persona_name)
        asyncio.run(_generate_tts_async(text=text, output_file=output_file, voice=voice))
    return output_file


def render_audio_player(audio_file: str, key_suffix: str = ""):
    """Render an audio player for a given file."""
    if not audio_file or not os.path.exists(audio_file):
        return
    try:
        with open(audio_file, "rb") as f:
            st.audio(f.read(), format="audio/mp3")
    except Exception:
        pass


def render_transcript(session: dict):
    st.markdown("### Interview Transcript")
    st.caption("🔊 Click play buttons to hear both interviewer questions and candidate replies.")
    if not session.get("conversation"):
        st.info("No conversation recorded.")
        return

    session_id = session.get("session_id", "unknown")
    persona_name = session.get("persona", {}).get("name", "")
    interviewer_turn_idx = 0
    candidate_turn_idx = 0

    for msg in session["conversation"]:
        ts = msg.get("elapsed_mmss", msg.get("timestamp", ""))
        if msg["role"] == "interviewer":
            interviewer_turn_idx += 1
            with st.chat_message("user"):
                label = "Interviewer"
                if msg.get("is_small_talk"):
                    label += " 💬"
                elif msg.get("is_behavioral"):
                    label += " 🎯"
                st.markdown(f"**{label} [{ts}]**  \n{msg['content']}")
                # Generate and show interviewer audio play button
                with st.expander("🔊 Play interviewer audio", expanded=False):
                    try:
                        audio_file = generate_interviewer_audio(
                            text=msg["content"],
                            session_id=session_id,
                            turn_index=interviewer_turn_idx
                        )
                        render_audio_player(audio_file, f"int_{interviewer_turn_idx}")
                    except Exception as e:
                        st.caption(f"Audio generation failed: {e}")
        else:
            candidate_turn_idx += 1
            with st.chat_message("assistant"):
                pronoun_info = msg.get("pronoun_counts", {})
                we_c = pronoun_info.get("we_count", 0)
                i_c = pronoun_info.get("i_count", 0)
                pronoun_tag = f" (we:{we_c} / I:{i_c})" if (we_c + i_c) > 0 else ""
                st.markdown(f"**Candidate [{ts}]{pronoun_tag}**  \n{msg['content']}")
                # Use existing audio file or generate one
                audio_file = msg.get("audio_file")
                if not audio_file or not os.path.exists(audio_file or ""):
                    try:
                        audio_file = generate_candidate_audio_if_missing(
                            text=msg["content"],
                            session_id=session_id,
                            turn_index=candidate_turn_idx,
                            persona_name=persona_name
                        )
                    except Exception:
                        audio_file = None
                if audio_file:
                    render_audio_player(audio_file, f"cand_{candidate_turn_idx}")


def init_state():
    defaults = {
        "app_mode": APP_MODE,
        "selected_session_id": None,
        "persona_management_file": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    st.session_state["app_mode"] = APP_MODE


def render_plotly_radar(report: dict):
    parameter_scores = report.get("parameter_scores", {})
    parameter_labels = report.get("parameter_labels", {})

    categories = []
    values = []

    for key, label in parameter_labels.items():
        categories.append(label)
        values.append(float(parameter_scores.get(key, {}).get("score", 0)))

    if not categories:
        st.info("No spider chart data available.")
        return

    categories.append(categories[0])
    values.append(values[0])

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values,
        theta=categories,
        fill="toself",
        name="Performance",
        line_color="#a855f7",
        fillcolor="rgba(168, 85, 247, 0.15)"
    ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 5],
                tickvals=[1, 2, 3, 4, 5],
                tickfont=dict(size=10)
            )
        ),
        showlegend=False,
        height=380,
        margin=dict(l=40, r=40, t=50, b=30),
        title=dict(text="Performance Spider Chart", font=dict(size=14))
    )

    st.plotly_chart(fig, use_container_width=True)


def render_competency_addressed_summary(report: dict):
    """Show which competency indicators were addressed vs not addressed."""
    comp_summary = report.get("competency_addressed_summary", {})
    if not comp_summary:
        return

    st.markdown("### Competency Indicators — Addressed vs Not Addressed")
    st.caption(f"STAR+Learning completeness: **{comp_summary.get('star_completeness_pct', 0)}%**")

    addressed = comp_summary.get("addressed", [])
    not_addressed = comp_summary.get("not_addressed", [])

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**✓ Addressed**")
        if addressed:
            html_parts = [f'<span class="comp-addressed">✓ {item}</span>' for item in addressed]
            st.markdown(" ".join(html_parts), unsafe_allow_html=True)
        else:
            st.write("None")
    with col2:
        st.markdown("**✗ Not Addressed**")
        if not_addressed:
            html_parts = [f'<span class="comp-not-addressed">✗ {item}</span>' for item in not_addressed]
            st.markdown(" ".join(html_parts), unsafe_allow_html=True)
        else:
            st.write("All covered!")


def render_score_breakdown_matrix(report: dict):
    breakdown = report.get("score_breakdown_matrix", [])
    if not breakdown:
        parameter_scores = report.get("parameter_scores", {})
        if parameter_scores:
            breakdown = engine.build_score_breakdown_matrix(parameter_scores)

    if not breakdown:
        st.info("Score breakdown not available.")
        return

    st.markdown("### Score Breakdown Matrix (Rubric-Based)")
    st.caption(
        "This table shows exactly how the final percentage was computed using the competency rubric. "
        "Formula: **(Score ÷ 5) × Weight = Weighted Contribution**."
    )

    df = pd.DataFrame(breakdown)

    def highlight_total(row):
        if row["Parameter"] == "TOTAL":
            return ["font-weight: bold; background: rgba(168,85,247,0.12)"] * len(row)
        return [""] * len(row)

    try:
        styled = df.style.apply(highlight_total, axis=1).format({
            "Weighted Contribution": "{:.1f}",
            "Max Possible": "{:.0f}"
        })
        st.dataframe(styled, use_container_width=True, hide_index=True)
    except Exception:
        st.dataframe(df, use_container_width=True, hide_index=True)

    with st.expander("How to read this table"):
        st.markdown("""
**Column definitions:**

| Column | Meaning |
|--------|---------|
| Parameter | The BEI evaluation dimension |
| Weight (%) | How much this parameter contributes to the total score |
| Score (1-5) | Raw score given by the evaluator, matched to the rubric level |
| Rubric Level | The description from the competency rubric for this score |
| Weighted Contribution | (Score ÷ 5) × Weight — the actual % points earned |
| Max Possible | The maximum % points achievable for this parameter |

**To reach 70% (Ready)**, you need an average above 3.5/5 across weighted parameters.
        """)


def render_rubric_reference():
    """Show the full competency rubric as a reference table."""
    st.markdown("### Full Competency Rubric Reference")
    st.caption("This is the rubric used to evaluate interviewer performance.")

    rows = []
    for key, rubric in COMPETENCY_RUBRIC.items():
        for level in sorted(rubric["levels"].keys(), reverse=True):
            rows.append({
                "Parameter": rubric["label"],
                "Weight": f"{rubric['weight']}%",
                "Score": level,
                "Description": rubric["levels"][level],
            })

    df = pd.DataFrame(rows)

    def color_scores(val):
        if isinstance(val, int):
            if val >= 4:
                return "color: #28c864"
            elif val >= 3:
                return "color: #e8a800"
            else:
                return "color: #dc3232"
        return ""

    try:
        styled = df.style.applymap(color_scores, subset=["Score"])
        st.dataframe(styled, use_container_width=True, hide_index=True, height=600)
    except Exception:
        st.dataframe(df, use_container_width=True, hide_index=True, height=600)


def render_parameter_cards(report: dict):
    st.markdown("### Scoring by Parameter (Rubric-Aligned)")

    parameter_scores = report.get("parameter_scores", {})
    parameter_labels = report.get("parameter_labels", {})
    weights = report.get("weights", {})

    for key, label in parameter_labels.items():
        item = parameter_scores.get(key, {})
        score = item.get("score", 0)
        weight = weights.get(key, 0)
        weighted_contrib = round((score / 5.0) * weight, 1)
        rubric_desc = item.get("rubric_level_description", "")

        if score >= 4:
            score_color = "#28c864"
        elif score >= 3:
            score_color = "#e8a800"
        else:
            score_color = "#dc3232"

        with st.container(border=True):
            c1, c2, c3 = st.columns([1, 0.6, 3.5])
            with c1:
                st.metric(label, f"{score}/5")
                st.caption(f"Weight: {weight}% | Contribution: {weighted_contrib}")
            with c2:
                bar_pct = int((score / 5) * 100)
                st.markdown(
                    f"""
                    <div style="margin-top:8px;">
                        <div style="background:rgba(128,128,128,0.15); border-radius:4px; height:10px; width:100%;">
                            <div style="background:{score_color}; width:{bar_pct}%; height:10px; border-radius:4px;"></div>
                        </div>
                        <div style="font-size:0.75rem; opacity:0.6; margin-top:3px;">{bar_pct}%</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
            with c3:
                if rubric_desc:
                    st.markdown(f"**Rubric Level ({score}/5):** {rubric_desc}")
                st.markdown("**Why this score**")
                st.write(item.get("rationale", ""))
                with st.expander("Evidence & how to improve"):
                    st.markdown("**Evidence considered**")
                    st.write(item.get("evidence", ""))
                    st.markdown("**How to improve**")
                    # Show the blended advice from what_good_looked_like (not raw rubric)
                    advice = item.get("what_good_looked_like", "")
                    st.write(advice)
                    # Also show what level 5 looks like as additional context
                    if score < 5:
                        best_practice = COMPETENCY_RUBRIC.get(key, {}).get("levels", {}).get(5, "")
                        if best_practice:
                            st.markdown(f"**Best practice benchmark (Level 5):** {best_practice}")


def render_bullets(items, icon="•"):
    if items:
        for item in items:
            st.write(f"{icon} {item}")
    else:
        st.write("None captured.")


def render_top_summary(report: dict):
    st.markdown("## Evaluation Summary")

    c1, c2, c3 = st.columns(3)
    with c1:
        score = report.get("final_score_percent", 0)
        st.metric("Final Score", f"{score}%")
    with c2:
        readiness = report.get("readiness_status", "NA")
        st.metric("Readiness", readiness)
    with c3:
        effectiveness = report.get("assessor_feedback", {}).get("effectiveness_rating", 0)
        st.metric("Effectiveness Rating", f"{effectiveness} / 5")

    with st.container(border=True):
        st.markdown("### Interview Summary")
        st.write(report.get("session_summary", ""))

    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.markdown("### Top Strengths")
            render_bullets(report.get("top_strengths", []), "✓")
    with col2:
        with st.container(border=True):
            st.markdown("### Top Improvement Areas")
            render_bullets(report.get("top_improvement_areas", []), "→")


def render_assessor_feedback(report: dict):
    feedback = report.get("assessor_feedback", {})
    st.markdown("## Assessor Feedback")

    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.markdown("### What Worked")
            render_bullets(feedback.get("strengths", []), "✓")
    with col2:
        with st.container(border=True):
            st.markdown("### Missed Probes")
            render_bullets(feedback.get("missed_probes", []), "✗")

    with st.container(border=True):
        st.markdown("### Probing Quality Assessment")
        st.write(feedback.get("probing_quality", ""))

        st.markdown("### Better Questions That Could Have Been Asked")
        render_bullets(feedback.get("better_questions", []), "💡")


def render_competency_summary(report: dict):
    comp = report.get("competency_evidence_summary", {})
    star = comp.get("star_completeness", {})

    st.markdown("## Competency Evidence Summary")

    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.markdown("### Evidence Observed")
            render_bullets(comp.get("evidence_observed", []))
    with col2:
        with st.container(border=True):
            st.markdown("### Assessor Effectiveness")
            render_bullets(comp.get("assessor_effectiveness", []))

    star_entries = [
        {"STAR Element": "Situation", "Assessment": star.get("situation", "")},
        {"STAR Element": "Task", "Assessment": star.get("task", "")},
        {"STAR Element": "Action", "Assessment": star.get("action", "")},
        {"STAR Element": "Result", "Assessment": star.get("result", "")},
    ]
    # Include Learning if present
    if "learning" in star:
        star_entries.append({"STAR Element": "Learning", "Assessment": star.get("learning", "")})

    star_df = pd.DataFrame(star_entries)

    with st.container(border=True):
        st.markdown("### STAR + Learning Completeness")
        st.dataframe(star_df, use_container_width=True, hide_index=True)


def render_evidence_feedback(report: dict):
    rows = report.get("evidence_based_feedback", [])
    st.markdown("## Evidence-Based Coaching Feedback")

    if not rows:
        st.info("No evidence-based feedback available.")
        return

    for idx, item in enumerate(rows, start=1):
        with st.expander(f"{idx}. {item.get('parameter', 'Feedback')}"):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**What Worked**")
                st.write(item.get("what_worked", ""))
                st.markdown("**Why It Matters**")
                st.write(item.get("why_it_matters", ""))
            with c2:
                st.markdown("**What Missed**")
                st.write(item.get("what_missed", ""))
                st.markdown("**What To Do Next**")
                st.write(item.get("what_to_do_next", ""))

            st.markdown("**Transcript Evidence**")
            st.info(item.get("evidence", ""))


def render_manager_report(report: dict):
    mgr = report.get("manager_report", {})
    st.markdown("## Manager Report")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"**Assessor:** {mgr.get('assessor_name', 'Assessor')}")
        st.markdown(f"**Date:** {mgr.get('date', '')}")
    with c2:
        st.markdown(f"**Interview Effectiveness:** {mgr.get('interview_effectiveness_score', '')}")
        st.markdown(f"**STAR Extraction Score:** {mgr.get('star_extraction_score', '')}")
    with c3:
        st.markdown(f"**Probing Score:** {mgr.get('probing_score', '')}")

    with st.container(border=True):
        st.markdown("### Top Development Areas")
        render_bullets(mgr.get("top_development_areas", []), "→")
        st.markdown("### Practice Recommendation")
        st.write(mgr.get("practice_recommendation", ""))


def render_report_submission_table(report: dict):
    rs = report.get("report_submission", {})
    st.markdown("## Report Submission Table")
    st.caption("Copy-ready summary for submission or governance tracking.")

    df = pd.DataFrame([{
        "Assessor Name": rs.get("assessor_name", ""),
        "Assessor Email": rs.get("assessor_email", ""),
        "Interview Effectiveness Score": rs.get("interview_effectiveness_score", ""),
        "STAR Score": rs.get("star_score", ""),
        "Probing Score": rs.get("probing_score", ""),
        "Manager Report": rs.get("manager_report_summary", "")
    }])

    st.dataframe(df, use_container_width=True, hide_index=True)


def render_candidate_facing_summary(report: dict):
    candidate_summary = report.get("candidate_summary", {})
    st.markdown("## Candidate-Facing Summary")
    st.caption("This is the simplified version of the report that the candidate sees in their interface.")

    with st.container(border=True):
        st.markdown("### Overall Impression")
        st.write(candidate_summary.get("overall_impression", ""))

    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.markdown("### Strengths")
            render_bullets(candidate_summary.get("strengths", []), "✓")
    with col2:
        with st.container(border=True):
            st.markdown("### Improvements")
            render_bullets(candidate_summary.get("improvements", []), "→")


def render_pronoun_tracking(session: dict):
    """Show pronoun tracking data for the session."""
    pronoun_tracking = session.get("pronoun_tracking", {})
    total_we = pronoun_tracking.get("total_we_count", 0)
    total_i = pronoun_tracking.get("total_i_count", 0)
    pronoun_shifted = session.get("pronoun_shift_triggered", False)

    st.markdown("### Pronoun Arc Analysis")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("'We/Our/Team' Count", total_we)
    with col2:
        st.metric("'I/My/Me' Count", total_i)
    with col3:
        ratio = f"{total_we}:{total_i}" if (total_we + total_i) > 0 else "N/A"
        st.metric("We:I Ratio", ratio)

    if pronoun_shifted:
        st.success("✅ The interviewer successfully triggered the pronoun shift to 'I'.")
    else:
        st.warning("⚠️ The interviewer did not trigger the pronoun shift. The candidate stayed in 'we' mode throughout.")

    # Per-turn pronoun breakdown
    convo = session.get("conversation", [])
    candidate_msgs = [m for m in convo if m.get("role") == "candidate" and m.get("pronoun_counts")]
    if candidate_msgs:
        turn_data = []
        for idx, msg in enumerate(candidate_msgs, 1):
            pc = msg.get("pronoun_counts", {})
            turn_data.append({
                "Turn": idx,
                "We/Our/Team": pc.get("we_count", 0),
                "I/My/Me": pc.get("i_count", 0),
                "Dominant": "We" if pc.get("we_count", 0) > pc.get("i_count", 0) else "I" if pc.get("i_count", 0) > pc.get("we_count", 0) else "Mixed",
            })
        with st.expander("Per-Turn Pronoun Breakdown"):
            st.dataframe(pd.DataFrame(turn_data), use_container_width=True, hide_index=True)


def render_persona_manager():
    st.markdown("## Persona Library")

    personas = PersonaStore.list_persona_files()
    if not personas:
        st.info("No persona files found in the persona_store/ folder.")
        return

    display_map = {row["display_name"]: row["file_name"] for row in personas}
    names = list(display_map.keys())

    if st.session_state.persona_management_file is None:
        st.session_state.persona_management_file = display_map[names[0]]

    selected_name = st.selectbox(
        "Choose Persona to View or Edit",
        names,
        help="Select any of the 10 pre-built personas or a custom persona."
    )
    selected_file = display_map[selected_name]
    st.session_state.persona_management_file = selected_file

    persona = PersonaStore.load_persona_by_file(selected_file)
    profile = persona.get("idealized_candidate_profile", {})
    bg = profile.get("professional_background", {})

    col1, col2 = st.columns([1.5, 1])

    with col1:
        with st.container(border=True):
            st.markdown(f"### {persona.get('name', 'Unknown')}")
            gender = persona.get("gender", "unknown")
            st.caption(f"Gender: {gender.title()}")

            if bg.get("industries"):
                st.caption(f"**Industries:** {', '.join(bg['industries'])}")
            if bg.get("progression"):
                st.write(bg["progression"])

            traits = profile.get("behavioral_traits", [])
            if traits:
                st.markdown("**Behavioural Traits**")
                for item in traits[:5]:
                    st.write(f"• {item}")

            gaps = profile.get("common_gaps", [])
            if gaps:
                st.markdown("**Common Interview Gaps**")
                for item in gaps[:4]:
                    st.write(f"⚠ {item}")

    with col2:
        with st.container(border=True):
            behavior_model = persona.get("interviewee_behaviour_model", {})
            st.markdown("**Answer Start Style**")
            st.write(behavior_model.get("answer_start_style", ""))
            st.markdown("**Response to Probing**")
            st.write(behavior_model.get("response_to_probing", ""))
            st.markdown("**Response to Pressure**")
            st.write(behavior_model.get("response_to_pressure", ""))

    competencies = persona.get("hidden_competencies", [])
    if competencies:
        with st.container(border=True):
            st.markdown("**Hidden Competencies (Assessor View Only)**")
            comp_data = [{"Competency": c.get("competency", ""), "Scenario Seed": c.get("scenario_seed", "")} for c in competencies]
            st.dataframe(pd.DataFrame(comp_data), use_container_width=True, hide_index=True)

    with st.expander("Edit Persona (Advanced — JSON Editor)"):
        editor_text = st.text_area(
            "Persona JSON",
            value=json.dumps(persona, indent=2, ensure_ascii=False),
            height=400,
            key=f"persona_editor_{selected_file}"
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Save Persona", use_container_width=True):
                try:
                    updated_persona = json.loads(editor_text)
                    PersonaStore.save_persona_by_file(selected_file, updated_persona)
                    st.success("Persona saved.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Invalid JSON: {e}")
        with c2:
            if st.button("🔄 Reload Persona", use_container_width=True):
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Init
# ─────────────────────────────────────────────────────────────────────────────
init_state()

sessions = SessionStore.list_sessions()

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Session Review")

    if sessions:
        session_options = {
            f"{row['updated_at'][:16]} | {row['persona_name'][:20]} | {row['status']} | {row['session_id'][:8]}": row["session_id"]
            for row in sessions
        }
        selected_label = st.selectbox(
            "Choose Interview Session",
            list(session_options.keys()),
            help="Sessions are sorted by most recently updated."
        )
        st.session_state.selected_session_id = session_options[selected_label]
    else:
        st.info("No sessions found. Run an interview in the Candidate app first.")

# ─────────────────────────────────────────────────────────────────────────────
# Main tabs
# ─────────────────────────────────────────────────────────────────────────────
tabs = st.tabs(["📋 Interview Review", "🧬 Persona Library", "📊 Competency Rubric"])

with tabs[2]:
    render_rubric_reference()

with tabs[1]:
    render_persona_manager()

with tabs[0]:
    if not st.session_state.selected_session_id:
        st.info("No session selected. Use the sidebar to choose a session.")
        st.stop()

    session_id = st.session_state.selected_session_id
    session = SessionStore.load_session(session_id)

    # ── Session metadata header ────────────────────────────────────────────
    top1, top2, top3, top4, top5 = st.columns(5)
    with top1:
        status_icon = "🟢" if session.get("status") == "active" else "✅"
        st.metric("Status", f"{status_icon} {session.get('status', 'NA').title()}")
    with top2:
        st.metric("Persona", session.get("persona", {}).get("name", "Unknown")[:22])
    with top3:
        st.metric("Difficulty", session.get("difficulty", "NA").upper())
    with top4:
        st.metric("Hidden Competency", session.get("selected_competency", "NA"))
    with top5:
        pronoun_shifted = session.get("pronoun_shift_triggered", False)
        st.metric("Pronoun Shift", "✅ Triggered" if pronoun_shifted else "⚠️ Not Triggered")

    with st.expander("About the Pronoun Arc"):
        st.markdown("""
**Pronoun Arc** is a deliberate feature of the AI candidate's behaviour:

- By default, the candidate leans toward **"We"** — blending into team language (but not exclusively).
- When the interviewer explicitly asks for **personal contribution** (e.g. *"What did YOU do?"*), the candidate shifts to primarily using **"I"**.
- This tests whether the interviewer can **force individual accountability** — a key BEI skill.
        """)

    st.markdown("---")

    # ── Transcript + controls layout ──────────────────────────────────────
    left, right = st.columns([1.2, 0.8])

    with left:
        render_transcript(session)

    with right:
        with st.container(border=True):
            st.markdown("### Session Controls")
            st.write(f"**Persona:** {session.get('persona', {}).get('name', 'Unknown')}")
            st.write(f"**Gender:** {session.get('persona', {}).get('gender', 'Unknown').title()}")
            st.write(f"**Difficulty:** {session.get('difficulty', 'NA').upper()}")
            st.write(f"**Competency:** {session.get('selected_competency', 'NA')}")

            metrics = session.get("metrics", {})
            m1, m2 = st.columns(2)
            with m1:
                st.metric("Interviewer Turns", metrics.get("interviewer_turns", 0))
                st.metric("Probe Questions", metrics.get("probe_like_questions", 0))
                st.metric("Small Talk Turns", metrics.get("small_talk_turns", 0))
            with m2:
                st.metric("Candidate Turns", metrics.get("candidate_turns", 0))
                st.metric("Candidate Sentences", metrics.get("total_candidate_sentences", 0))
                st.metric("Behavioral Qs", metrics.get("behavioral_questions", 0))

            st.markdown("---")

            # Competency indicators
            accumulated = session.get("competency_indicators_accumulated", {})
            st.markdown("**STAR Coverage**")
            star_labels = {
                "situation_explored": "Situation",
                "task_explored": "Task",
                "action_explored": "Action",
                "result_explored": "Result",
                "learning_explored": "Learning",
                "reasoning_explored": "Reasoning",
            }
            for key, label in star_labels.items():
                status = "✅" if accumulated.get(key, False) else "❌"
                st.write(f"{status} {label}")

            st.markdown("---")

            if session.get("status") == "active":
                if st.button("⚡ Generate / Refresh Final Report", use_container_width=True, type="primary"):
                    with st.spinner("Generating final report..."):
                        engine.generate_final_report(session_id)
                    st.success("Report generated.")
                    st.rerun()

            with st.expander("Change Persona for Active Session"):
                personas = PersonaStore.list_persona_files()
                pdisplay_map = {row["display_name"]: row["file_name"] for row in personas}
                pnames = list(pdisplay_map.keys())

                if pnames:
                    selected_pname = st.selectbox(
                        "Select Persona",
                        pnames,
                        key="session_persona_select"
                    )
                    selected_pfile = pdisplay_map[selected_pname]
                    selected_persona = PersonaStore.load_persona_by_file(selected_pfile)

                    if st.button("Apply Persona to Session", use_container_width=True):
                        if session.get("status") != "active":
                            st.warning("Persona can only be changed for an active session.")
                        else:
                            SessionStore.overwrite_persona_for_session(session_id, selected_persona, selected_pfile)
                            st.success("Persona updated.")
                            st.rerun()

    st.markdown("---")

    # ── Final report rendering ─────────────────────────────────────────────
    session = SessionStore.load_session(session_id)
    report = session.get("final_report")

    if session.get("status") == "completed" and report:
        # A. Evaluation Summary
        render_top_summary(report)

        st.markdown("---")

        # B. Competency Addressed vs Not Addressed
        with st.container(border=True):
            render_competency_addressed_summary(report)

        st.markdown("---")

        # C. Pronoun Tracking
        with st.container(border=True):
            render_pronoun_tracking(session)

        st.markdown("---")

        # D. Score Breakdown Matrix (with rubric levels)
        with st.container(border=True):
            render_score_breakdown_matrix(report)

        st.markdown("---")

        # E. Spider Chart
        render_plotly_radar(report)

        st.markdown("---")

        # F. Parameter-level cards (with rubric descriptions)
        render_parameter_cards(report)

        st.markdown("---")

        # G. Assessor Feedback
        render_assessor_feedback(report)

        st.markdown("---")

        # H. Competency Evidence
        render_competency_summary(report)

        st.markdown("---")

        # I. Evidence-Based Coaching
        render_evidence_feedback(report)

        st.markdown("---")

        # J. Manager Report
        render_manager_report(report)

        st.markdown("---")

        # K. Submission Table
        render_report_submission_table(report)

        st.markdown("---")

        # L. Candidate-Facing Summary
        render_candidate_facing_summary(report)

    else:
        if session.get("status") == "active":
            st.info(
                "This session is still active. Generate the final report using the button in the Session Controls panel above."
            )
        else:
            st.warning("This session is completed but has no final report. It may have ended without report generation.")
