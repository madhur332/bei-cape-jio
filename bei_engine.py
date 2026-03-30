# bei_engine.py
# BEI Cape — Core Engine v2
# Fixes: controlled "we" pronoun, randomness/human-like responses, rubric-based scoring,
#         competency addressed vs not addressed, gender-aware TTS voice mapping,
#         small-talk handling, dynamic feedback

import os
import json
import uuid
import re
import random
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:7b")
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "sessions")
PERSONA_DIR = os.getenv("PERSONA_DIR", "persona_store")

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(PERSONA_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# COMPETENCY RUBRIC (from Excel)
# ─────────────────────────────────────────────────────────────────────────────

COMPETENCY_RUBRIC = {
    "depth_of_probing": {
        "weight": 20,
        "label": "Depth of Probing",
        "levels": {
            5: "Asks ≥3 follow-up layers per story. Every response yields action taken, reasoning, outcome, learning. Zero vague answers left unchallenged.",
            4: "2 follow-up layers in most stories. Actions + outcomes captured; learning occasionally missed. ≤1 vague answer accepted without probe.",
            3: "1 follow-up layer per story (surface STAR only). Stops after initial response in 30–40% of questions. Accepts vague answers without probing 3–4 times.",
            2: "Follow-up probes used in <50% questions. Vague answers accepted ≥5 times. Moves to next question before story is complete.",
            1: "No follow-up probes at all. Every vague answer accepted. Questions asked in sequence with no layer depth.",
        }
    },
    "bei_structure_adherence": {
        "weight": 15,
        "label": "BEI Structure Adherence",
        "levels": {
            5: "Every story contains Situation → Task → Action → Result → Learning. Interviewer explicitly closes each narrative before moving on. Zero stories left structurally incomplete.",
            4: "≥4 out of 5 stories contain full STAR. Learning captured in most cases; 1 story missing it. Closure attempted but occasionally skipped.",
            3: "S-T-A-R present in most stories; Learning missing in ≥2. No structured closure in 30–40% of narratives. At least 1 story ends mid-way without result.",
            2: "STAR incomplete in ≥3 stories. Result or Learning skipped in majority of narratives. No closure at all in most stories.",
            1: "No recognisable STAR structure in any story. Questions asked randomly with no narrative arc. No closure or transitions observed.",
        }
    },
    "evidence_validation": {
        "weight": 15,
        "label": "Evidence Validation",
        "levels": {
            5: "Every claim challenged with 'What was the metric?', 'Over what timeframe?', 'What was the baseline?'. Zero unquantified claims left unaddressed.",
            4: "Metrics or timeframes requested in ≥4 out of 5 stories. At most 1 vague claim accepted without challenge.",
            3: "Specifics requested in 2–3 stories only. General or unquantified answers accepted in ≥2 cases.",
            2: "Evidence requested in ≤1 story. Accepts statements like 'improved significantly' with no follow-up. No numbers, dates, or baselines captured.",
            1: "No request for metrics, timeframes, or outcomes made at any point. All claims taken at face value.",
        }
    },
    "question_precision": {
        "weight": 15,
        "label": "Question Precision",
        "levels": {
            5: "Every question is 1 sentence, 1 intent. Zero multi-part or compound questions asked. Zero filler phrases used.",
            4: "≤1 multi-part question across the interview. Questions are short and clear in ≥90% of cases.",
            3: "2–3 multi-part questions observed. Some questions require candidate to ask for clarification.",
            2: "≥4 multi-part or compound questions asked. Candidate visibly confused or asks 'which part should I answer?' ≥1 time.",
            1: "Majority of questions are rambling, multi-layered, or contain the answer. Candidate regularly needs the question repeated or clarified.",
        }
    },
    "listening_responsiveness": {
        "weight": 10,
        "label": "Listening & Responsiveness",
        "levels": {
            5: "Every follow-up question directly references something the candidate just said. Zero scripted transitions used. Interviewer picks up on implied details and surfaces them.",
            4: "≥4 follow-ups visibly linked to candidate's previous answer. Scripted transition used ≤1 time.",
            3: "Follow-ups linked to candidate's answer in ~50% of cases. Scripted transitions used 2–3 times.",
            2: "Follow-ups rarely reference candidate's words. Scripted transitions used ≥4 times. Key candidate cues (e.g. named a conflict, hinted at failure) ignored.",
            1: "All questions pre-scripted; no linkage to any candidate response. Candidate cues ignored throughout.",
        }
    },
    "neutrality_non_leading": {
        "weight": 10,
        "label": "Neutrality & Non-Leading",
        "levels": {
            5: "All questions are open-ended (begin with What, How, Tell me, Describe). Zero yes/no questions asked. Zero assumptive phrases used.",
            4: "≤1 leading or closed question observed. No assumptive phrases used.",
            3: "2–3 closed or slightly leading questions observed. At least 1 assumptive phrase used.",
            2: "≥4 leading questions or yes/no questions asked. Answer implied in question phrasing ≥2 times.",
            1: "Majority of questions are leading or closed. Interviewer regularly suggests the desired answer in the question.",
        }
    },
    "competency_coverage": {
        "weight": 10,
        "label": "Competency Coverage",
        "levels": {
            5: "All key behavioural indicators for the competency are explored (typically 3–4 dimensions). At least 2 distinct stories gathered per competency. No dimension left unexplored.",
            4: "3 out of 4 dimensions covered. At least 1 full story gathered; second story attempted.",
            3: "2 out of 4 dimensions covered. Only 1 story gathered; remaining dimensions not probed.",
            2: "Only 1 dimension explored, and only at surface level. Disproportionate time on 1 sub-topic.",
            1: "Competency not meaningfully addressed. No relevant story or example gathered.",
        }
    },
    "time_management": {
        "weight": 5,
        "label": "Time Management",
        "levels": {
            5: "Each competency completed within allocated time (±1 min). Depth maintained without running over. Smooth, deliberate transition to next competency.",
            4: "1 competency runs slightly over (by 2–3 min). Coverage remains mostly balanced.",
            3: "1 competency runs ≥4 min over; another is rushed as a result. Noticeable imbalance but all competencies still attempted.",
            2: "≥2 competencies severely over or under time. 1 competency skipped or abandoned due to time.",
            1: "No time awareness demonstrated. Stuck on 1 area; remaining areas not reached.",
        }
    },
}

PARAMETER_WEIGHTS = {k: v["weight"] for k, v in COMPETENCY_RUBRIC.items()}
PARAMETER_LABELS = {k: v["label"] for k, v in COMPETENCY_RUBRIC.items()}


# ─────────────────────────────────────────────────────────────────────────────
# GENDER-AWARE VOICE MAPPING
# ─────────────────────────────────────────────────────────────────────────────

PERSONA_VOICE_MAP = {
    # Female voices — clear, natural pronunciation
    "priya": "en-IN-NeerjaNeural",
    "sarah": "en-US-AriaNeural",
    "fatima": "en-US-AriaNeural",
    "elena": "en-US-JennyNeural",
    "nadia": "en-US-AriaNeural",
    # Male voices — en-US-AndrewNeural and en-US-ChristopherNeural
    # have the clearest pronunciation, matching female voice quality.
    # Avoid GuyNeural and PrabhatNeural which have poorer articulation.
    "arjun": "en-US-AndrewNeural",
    "liang": "en-US-ChristopherNeural",
    "marcus": "en-US-ChristopherNeural",
    "rohan": "en-US-AndrewNeural",
    "james": "en-US-ChristopherNeural",
}


def get_tts_voice_for_persona(persona_name: str) -> str:
    """Return the correct gendered TTS voice based on persona name."""
    name_lower = (persona_name or "").lower()
    for key, voice in PERSONA_VOICE_MAP.items():
        if key in name_lower:
            return voice
    # Default: neutral female
    return "en-US-AriaNeural"


def is_male_voice(voice: str) -> bool:
    """Check if a voice is male."""
    male_voices = ["AndrewNeural", "ChristopherNeural", "GuyNeural", "PrabhatNeural"]
    return any(m in voice for m in male_voices)


# Interviewer voice — used in assessor portal for playing back interviewer questions
INTERVIEWER_TTS_VOICE = "en-US-JennyNeural"


# ─────────────────────────────────────────────────────────────────────────────
# SMALL TALK / CONVERSATIONAL DETECTION
# ─────────────────────────────────────────────────────────────────────────────

SMALL_TALK_PATTERNS = [
    r"\bhow are you\b", r"\bhow're you\b", r"\bhow do you do\b",
    r"\bhow is it going\b", r"\bhow's it going\b", r"\bwhat's up\b",
    r"\bhow have you been\b", r"\btell me about yourself\b",
    r"\bintroduce yourself\b", r"\bwho are you\b", r"\byour name\b",
    r"\bnice to meet\b", r"\bgood morning\b", r"\bgood afternoon\b",
    r"\bgood evening\b", r"\bhello\b", r"\bhi there\b",
    r"\bthank you for coming\b", r"\bthanks for joining\b",
    r"\bhow was your day\b", r"\bhow was your commute\b",
    r"\bare you comfortable\b", r"\bcan i get you\b",
    r"\bwould you like water\b", r"\bwould you like coffee\b",
    r"\brelax\b", r"\bsettle in\b", r"\bmake yourself comfortable\b",
]


def is_small_talk(text: str) -> bool:
    """Detect if the interviewer's message is casual / small talk / ice-breaker."""
    text_l = (text or "").lower().strip()
    if len(text_l.split()) <= 2 and any(g in text_l for g in ["hi", "hello", "hey"]):
        return True
    return any(re.search(p, text_l) for p in SMALL_TALK_PATTERNS)


def is_behavioral_question(text: str) -> bool:
    """Detect if the question is a behavioral/competency question."""
    text_l = (text or "").lower()
    markers = [
        "tell me about a time", "give me an example", "describe a situation",
        "walk me through", "can you share", "what happened when",
        "how did you handle", "what was the challenge", "what did you do when",
        "have you ever faced", "recall a time", "think of a situation",
        "share an experience", "describe an instance", "when was a time",
    ]
    return any(m in text_l for m in markers)


# ─────────────────────────────────────────────────────────────────────────────
# HUMAN-LIKE FILLER / RANDOMNESS ELEMENTS
# ─────────────────────────────────────────────────────────────────────────────

FILLER_STARTS = [
    "Hmm, let me think...",
    "Oh, right — so...",
    "Yeah, so...",
    "Okay, so...",
    "Sure, so...",
    "Well...",
    "Hmm, okay —",
    "Right, so actually...",
    "Ah, that reminds me of something.",
    "Let me recall...",
    "So actually...",
    "Okay so this one time...",
    "Right, so...",
]

SMALL_TALK_REPLIES_DEPRECATED = []  # No longer used — all small talk goes through LLM for intelligent responses


def random_filler() -> str:
    return random.choice(FILLER_STARTS)


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now().isoformat()


def sanitize_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def sentence_cap(text: str, max_sentences: int = 6) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= max_sentences:
        return " ".join(parts)
    return " ".join(parts[:max_sentences])


def read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _extract_json_candidate(raw: str) -> str:
    raw = (raw or "").strip()
    code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if code_block_match:
        return code_block_match.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start:end + 1].strip()
    return raw


def safe_json_extract(raw: str) -> Optional[Dict[str, Any]]:
    candidate = _extract_json_candidate(raw)
    try:
        return json.loads(candidate)
    except Exception:
        return None


def tokenize_with_char_spans(text: str) -> List[Dict[str, Any]]:
    tokens = []
    for idx, match in enumerate(re.finditer(r"\S+", text)):
        tokens.append({
            "index": idx,
            "word": match.group(0),
            "start": match.start(),
            "end": match.end()
        })
    return tokens


def extract_sentence_count(text: str) -> int:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return len([p for p in parts if p.strip()])


def format_elapsed_mmss(start_iso: str, current_iso: str) -> str:
    try:
        start_dt = datetime.fromisoformat(start_iso)
        current_dt = datetime.fromisoformat(current_iso)
        secs = max(0, int((current_dt - start_dt).total_seconds()))
        mm = secs // 60
        ss = secs % 60
        return f"{mm:02d}:{ss:02d}"
    except Exception:
        return "00:00"


def strip_candidate_followup_questions(text: str) -> str:
    text = sanitize_text(text)
    if not text:
        return text

    # Strip "good question" variants that LLMs love to add
    good_q_patterns = [
        r"(?i)^(that'?s?\s+a\s+)?good\s+question[.!,]?\s*",
        r"(?i)^great\s+question[.!,]?\s*",
        r"(?i)^interesting\s+question[.!,]?\s*",
        r"(?i)^nice\s+question[.!,]?\s*",
        r"(?i)^(that'?s?\s+a\s+)?fair\s+question[.!,]?\s*",
    ]
    for pattern in good_q_patterns:
        text = re.sub(pattern, "", text).strip()

    parts = re.split(r"(?<=[.!?])\s+", text)
    cleaned_parts = []

    for part in parts:
        p = part.strip()
        if not p:
            continue

        low = p.lower()
        disallowed_question_starts = [
            "would you like", "do you want", "should i", "can i", "shall i",
            "does that help", "would it help", "want me to", "should i go on",
            "should i continue", "do you want me to", "would you like me to",
            "anything else", "any other", "can you clarify",
            "what else would you like",
        ]

        if p.endswith("?") or any(low.startswith(x) for x in disallowed_question_starts):
            continue

        cleaned_parts.append(p)

    if not cleaned_parts:
        text = re.sub(r"\s+[A-Z][^.?!]*\?$", "", text).strip()
        text = text.rstrip("?").strip()
        if text and text[-1] not in ".!":
            text += "."
        return text

    final = " ".join(cleaned_parts).strip()
    if final and final[-1] not in ".!":
        final += "."
    return final


def detect_personal_probe(text: str) -> bool:
    """Detect if the interviewer is explicitly asking for personal/individual contribution."""
    text_l = (text or "").lower()
    personal_markers = [
        "what did you do", "your role", "your contribution", "you personally",
        "what did you specifically", "what was your part", "tell me about you",
        "what did you personally", "i want to hear about you", "focus on yourself",
        "use 'i'", "use i ", "speak for yourself", "what you did", "your actions",
        "your decision", "you specifically", "personally", "individually",
        "what were you responsible for", "your own contribution",
        "what was your specific", "what exactly did you", "your individual",
    ]
    return any(marker in text_l for marker in personal_markers)


def count_we_vs_i(text: str) -> Dict[str, int]:
    """Count 'we'/'our'/'the team' vs 'I'/'my'/'me' in candidate response."""
    text_l = (text or "").lower()
    we_count = len(re.findall(r"\bwe\b|\bour\b|\bthe team\b|\bus\b", text_l))
    i_count = len(re.findall(r"\bi\b|\bmy\b|\bme\b|\bmyself\b", text_l))
    return {"we_count": we_count, "i_count": i_count}


# ─────────────────────────────────────────────────────────────────────────────
# COMPETENCY TRACKING
# ─────────────────────────────────────────────────────────────────────────────

def detect_competency_indicators(text: str) -> Dict[str, bool]:
    """Detect which competency indicators are present in an interviewer question."""
    text_l = (text or "").lower()
    indicators = {
        "situation_explored": any(w in text_l for w in [
            "situation", "context", "background", "what was happening", "describe the setting",
            "tell me about a time", "give me an example"
        ]),
        "task_explored": any(w in text_l for w in [
            "task", "objective", "goal", "what were you trying", "what was expected",
            "your responsibility", "what needed to be done"
        ]),
        "action_explored": any(w in text_l for w in [
            "what did you do", "how did you", "what action", "what steps",
            "your approach", "what was your role", "specifically do", "your contribution"
        ]),
        "result_explored": any(w in text_l for w in [
            "result", "outcome", "impact", "what happened", "measurable",
            "how did it turn out", "what changed", "improvement", "metric", "data"
        ]),
        "learning_explored": any(w in text_l for w in [
            "what did you learn", "takeaway", "differently", "looking back",
            "what would you change", "reflection", "lesson"
        ]),
        "reasoning_explored": any(w in text_l for w in [
            "why did you", "what was your thinking", "reasoning", "rationale",
            "why that approach", "what made you decide", "thought process"
        ]),
    }
    return indicators


class PersonaStore:
    @staticmethod
    def list_persona_files() -> List[Dict[str, str]]:
        rows = []
        for file in os.listdir(PERSONA_DIR):
            if not file.endswith(".json"):
                continue
            path = os.path.join(PERSONA_DIR, file)
            try:
                data = read_json(path)
                rows.append({
                    "file_name": file,
                    "display_name": data.get("name", file.replace(".json", "").replace("_", " ").title()),
                    "path": path
                })
            except Exception:
                continue
        rows.sort(key=lambda x: x["display_name"].lower())
        return rows

    @staticmethod
    def load_persona_by_file(file_name: str) -> Dict[str, Any]:
        return read_json(os.path.join(PERSONA_DIR, file_name))

    @staticmethod
    def save_persona_by_file(file_name: str, persona: Dict[str, Any]) -> None:
        write_json(os.path.join(PERSONA_DIR, file_name), persona)

    @staticmethod
    def ensure_sample_personas() -> None:
        for persona_data in get_all_personas():
            slug = persona_data["name"].lower().replace(" ", "_").replace("/", "_").replace(",", "")
            fname = f"{slug}.json"
            fpath = os.path.join(PERSONA_DIR, fname)
            if not os.path.exists(fpath):
                write_json(fpath, persona_data)


class SessionStore:
    @staticmethod
    def _session_path(session_id: str) -> str:
        return os.path.join(SESSIONS_DIR, f"{session_id}.json")

    @staticmethod
    def _completed_session_count() -> int:
        count = 0
        for file in os.listdir(SESSIONS_DIR):
            if not file.endswith(".json"):
                continue
            try:
                data = read_json(os.path.join(SESSIONS_DIR, file))
                if data.get("status") == "completed":
                    count += 1
            except Exception:
                pass
        return count

    @staticmethod
    def assign_difficulty(override: Optional[str] = None) -> str:
        if override and override.lower() in ("low", "medium", "high"):
            return override.lower()
        completed = SessionStore._completed_session_count()
        if completed <= 3:
            return "low"
        if completed <= 7:
            return "medium"
        return "high"

    @staticmethod
    def pick_hidden_competency(persona: Dict[str, Any]) -> Dict[str, Any]:
        competencies = persona.get("hidden_competencies", [])
        if not competencies:
            return {}
        total_sessions = len(SessionStore.list_sessions())
        idx = total_sessions % len(competencies)
        return competencies[idx]

    @staticmethod
    def create_session(
        persona: Dict[str, Any],
        persona_file_name: Optional[str] = None,
        difficulty_override: Optional[str] = None
    ) -> str:
        session_id = str(uuid.uuid4())
        difficulty = SessionStore.assign_difficulty(override=difficulty_override)
        hidden_seed = SessionStore.pick_hidden_competency(persona)

        payload = {
            "session_id": session_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "started_at": now_iso(),
            "ended_at": None,
            "difficulty": difficulty,
            "selected_competency": hidden_seed.get("competency"),
            "hidden_competency_seed": hidden_seed,
            "persona": persona,
            "persona_file_name": persona_file_name,
            "status": "active",
            "conversation": [],
            "pronoun_shift_triggered": False,
            "final_report": None,
            "metrics": {
                "interviewer_turns": 0,
                "candidate_turns": 0,
                "probe_like_questions": 0,
                "total_candidate_sentences": 0,
                "small_talk_turns": 0,
                "behavioral_questions": 0,
            },
            # NEW: Track competency indicators across the interview
            "competency_indicators_accumulated": {
                "situation_explored": False,
                "task_explored": False,
                "action_explored": False,
                "result_explored": False,
                "learning_explored": False,
                "reasoning_explored": False,
            },
            # NEW: Track we/I pronoun balance across candidate responses
            "pronoun_tracking": {
                "total_we_count": 0,
                "total_i_count": 0,
            },
        }
        write_json(SessionStore._session_path(session_id), payload)
        return session_id

    @staticmethod
    def load_session(session_id: str) -> Dict[str, Any]:
        return read_json(SessionStore._session_path(session_id))

    @staticmethod
    def save_session(session_id: str, data: Dict[str, Any]) -> None:
        data["updated_at"] = now_iso()
        write_json(SessionStore._session_path(session_id), data)

    @staticmethod
    def list_sessions() -> List[Dict[str, Any]]:
        rows = []
        for file in os.listdir(SESSIONS_DIR):
            if not file.endswith(".json"):
                continue
            path = os.path.join(SESSIONS_DIR, file)
            try:
                data = read_json(path)
                rows.append({
                    "session_id": data.get("session_id"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "difficulty": data.get("difficulty"),
                    "status": data.get("status"),
                    "persona_name": data.get("persona", {}).get("name", "Unknown"),
                    "selected_competency": data.get("selected_competency"),
                })
            except Exception:
                continue
        rows.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return rows

    @staticmethod
    def overwrite_persona_for_session(
        session_id: str,
        persona: Dict[str, Any],
        persona_file_name: Optional[str] = None
    ) -> None:
        session = SessionStore.load_session(session_id)
        if session.get("status") == "completed":
            return
        session["persona"] = persona
        session["persona_file_name"] = persona_file_name
        SessionStore.save_session(session_id, session)

    @staticmethod
    def end_session(session_id: str, final_report: Dict[str, Any]) -> None:
        session = SessionStore.load_session(session_id)
        session["final_report"] = final_report
        session["status"] = "completed"
        session["ended_at"] = now_iso()
        SessionStore.save_session(session_id, session)

    @staticmethod
    def change_difficulty(session_id: str, new_difficulty: str) -> None:
        """Change difficulty mid-session. Only works for active sessions."""
        if new_difficulty.lower() not in ("low", "medium", "high"):
            return
        session = SessionStore.load_session(session_id)
        if session.get("status") != "active":
            return
        session["difficulty"] = new_difficulty.lower()
        SessionStore.save_session(session_id, session)


class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_BASE_URL, model_name: str = MODEL_NAME):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        # Reuse HTTP session for connection pooling — faster subsequent calls
        self._session = requests.Session()

    def generate(self, prompt: str, temperature: float = 0.8, num_predict: int = 300) -> str:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m",  # Keep model loaded in memory for 10 min
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": 2048,  # Smaller context window = faster inference
            }
        }
        resp = self._session.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("response") or "").strip()


class BEIEngine:
    PARAMETER_WEIGHTS = PARAMETER_WEIGHTS
    PARAMETER_LABELS = PARAMETER_LABELS

    def __init__(self):
        self.client = OllamaClient()

    def build_candidate_system_prompt(
        self,
        persona: Dict[str, Any],
        difficulty: str,
        hidden_seed: Dict[str, Any],
        conversation: List[Dict[str, Any]],
        pronoun_shift_triggered: bool = False,
        is_small_talk_turn: bool = False,
        is_behavioral_turn: bool = False,
    ) -> str:
        hidden_competencies = persona.get("hidden_competencies", [])
        sample_answer_bank = persona.get("sample_answer_bank", [])
        behavior_model = persona.get("interviewee_behaviour_model", {})
        response_rules = persona.get("response_style_rules", [])
        idealized_profile = persona.get("idealized_candidate_profile", {})

        # ── Random personality traits for this turn ─────────────────────────
        mood_variations = random.choice([
            "You are in a calm, reflective mood today.",
            "You are slightly tired but trying your best.",
            "You are feeling confident but cautious.",
            "You are a bit nervous, which makes you ramble slightly.",
            "You are relaxed and naturally conversational.",
            "You are focused and want to give thoughtful answers.",
        ])

        # ── Small talk override — INTELLIGENT, context-aware ────────────────
        if is_small_talk_turn:
            last_interviewer_msg = ""
            for m in reversed(conversation):
                if m.get("role") == "interviewer":
                    last_interviewer_msg = m.get("content", "")
                    break

            persona_name_short = persona.get("name", "Candidate").split("—")[0].strip()
            persona_bg = idealized_profile.get("professional_background", {})
            persona_roles = persona_bg.get("roles", [])
            persona_industries = persona_bg.get("industries", [])
            current_role = persona_roles[-1] if persona_roles else "professional"
            industry = persona_industries[0] if persona_industries else "my field"

            return f"""
You are {persona_name_short}, a {current_role} in {industry}. You are sitting in an interview room.

The interviewer just said: "{last_interviewer_msg}"

This is small talk / ice-breaking. Respond the way a real person would in an interview waiting room.

RULES:
- Respond DIRECTLY and SPECIFICALLY to what they said. If they said "good morning", say good morning back. If they asked how you are, say how you feel.
- Be warm, friendly, and natural — like a real human, not a bot.
- You can mention something small and personal: the weather today, getting here, being a bit nervous, looking forward to the chat.
- If they say "tell me about yourself", give a 2-sentence professional intro: your name, current role, and how long you've been in the field.
- 1-3 sentences MAXIMUM. Short and natural.
- Do NOT mention any work challenge, project, or competency.
- Do NOT give a structured or STAR answer.
- Do NOT say "good question" or "that's a great question".
- Do NOT end with a question.
- {mood_variations}

YOUR PERSONA TRAITS: {json.dumps(idealized_profile.get("behavioral_traits", [])[:3])}
"""

        # ── Difficulty guidance ──────────────────────────────────────────────
        difficulty_guide = {
            "low": """
DIFFICULTY LOW:
- Be cooperative and reasonably clear.
- Still give answers that need light probing.
- Mention context before getting to your contribution.
- Leave the result vague unless the interviewer asks directly.
- Aim for 4-5 sentences. Finish your thought completely.
- Use a MIX of "we" and "I" — lean toward "we" about 60% of the time but naturally include some "I" statements.
""",
            "medium": """
DIFFICULTY MEDIUM:
- Give context-heavy answers. Bury your personal contribution.
- Use "we" more than "I" — about 70% "we" — but not exclusively. Occasionally slip in "I" naturally.
- Do not mention the outcome unless explicitly probed.
- Sound natural but slightly evasive about specifics.
- Aim for 3-5 sentences. Finish your thought completely.
""",
            "high": """
DIFFICULTY HIGH:
- Be guarded and somewhat vague.
- Over-explain the team situation and minimize personal role.
- Use "we" heavily — about 80% — but still say "I" at least once to sound human.
- If asked about outcomes, say things were still being measured or you moved on.
- Occasionally say you don't remember exact details.
- Sound slightly defensive if pushed hard.
- Aim for 3-4 sentences. Be terse but finish your thought.
"""
        }.get(difficulty.lower(), "")

        # ── Pronoun instruction (controlled — NOT 100% "we") ────────────────
        if pronoun_shift_triggered:
            pronoun_rule = """
PRONOUN RULE (SHIFTED TO "I"):
The interviewer has explicitly asked you to speak personally.
- Use "I" as your PRIMARY pronoun now — about 70-80% of the time.
- You CAN still mention the team occasionally — that's natural.
- Describe your own actions and decisions directly.
- Still keep some gaps — do not suddenly give a perfect answer.
- Do not over-explain. Stay concise.
"""
        else:
            pronoun_rule = """
PRONOUN RULE (DEFAULT — CONTROLLED MIX):
- Use "we" and team language as your PRIMARY framing — but NOT exclusively.
- IMPORTANT: Mix in some "I" statements naturally (about 20-30% of sentences).
  For example: "We were dealing with the project and I was mostly focused on the coordination side."
- This is critical: real people don't say "we" in every single sentence. Sound natural.
- Start answers with varied openings — NOT always "We were..." or "The team..."
- Example good mix: "So there was this project we were working on, and I was handling the ops side of things. We had some issues with the timeline..."
- Only shift to mostly "I" if the interviewer specifically asks what YOU personally did.
"""

        # ── Non-STAR imperfection rules with randomness ─────────────────────
        non_star_rules = f"""
ANTI-STAR RULES + HUMAN RANDOMNESS (CRITICAL):
- Do NOT structure your answer as Situation → Task → Action → Result.
- {random.choice([
            "Start somewhere in the middle of the story.",
            "Start with a tangential detail before getting to the point.",
            "Start with how you felt about the situation.",
            "Start with a brief aside or context that's slightly off-topic.",
            "Start by referencing something the interviewer said.",
        ])}
- Skip the result entirely unless asked.
- Give a partial action but not the full sequence.
- {random.choice([
            "Be slightly rambling — like someone recalling something casually.",
            "Be a bit scattered — jump between two related thoughts.",
            "Be somewhat direct but leave gaps.",
            "Trail off slightly mid-thought before finishing.",
            "Pause mentally — say 'hmm' or 'let me think' before continuing.",
        ])}
- Do not quantify anything unless the interviewer asks specifically.
- Do not summarise at the end of your answer.
- Sound like someone recalling something, not presenting it.
- {mood_variations}

VARIETY RULE:
- Do NOT start every answer the same way.
- Vary your sentence structure across turns.
- Sometimes be brief (3 sentences), sometimes medium (5 sentences).
- Occasionally include a filler phrase like "hmm", "actually", "you know", "honestly".
"""

        convo_str = "\n".join(
            [f"{m['role'].upper()}: {m['content']}" for m in conversation[-10:]]
        )

        return f"""
You are simulating a realistic AI interviewee for a Behavioral Event Interview training platform.

YOUR ROLE:
- You are ONLY the candidate. Do not be a coach, assistant, or evaluator.
- The human is the interviewer/assessor practicing their BEI skill.

STRICT RULES:
1. Aim for 3-5 sentences. You may go up to 7 if needed to finish a thought, but NEVER more.
2. Your reply must feel COMPLETE — finish your thought naturally. Do NOT stop mid-idea.
3. Do NOT give complete STAR answers. Do not structure your answer.
4. Do NOT say "as an AI" or break character.
5. Do NOT reveal scoring, competencies, seeds, or internal logic.
6. Do NOT ask the interviewer any question. Never end with a question mark.
7. Do NOT say things like "would you like me to continue" or "should I explain more".
8. Sound natural, slightly imperfect, conversational — like a real person recalling something.
9. Do NOT proactively state learnings, results, or outcomes — wait to be asked.
10. Do NOT summarise at the end of your reply.
11. VARY your answer style — do NOT sound templated or repetitive.
12. NEVER say "good question", "great question", "that's a good one" or any variant. Just answer directly.

{difficulty_guide}

{pronoun_rule}

{non_star_rules}

HIDDEN SEED FOR THIS SESSION:
{json.dumps(hidden_seed, indent=2)}

IDEALIZED CANDIDATE PROFILE:
{json.dumps(idealized_profile, indent=2)}

INTERVIEWEE BEHAVIOUR MODEL:
{json.dumps(behavior_model, indent=2)}

RESPONSE STYLE RULES FROM PERSONA:
{json.dumps(response_rules, indent=2)}

ALL HIDDEN COMPETENCIES:
{json.dumps(hidden_competencies, indent=2)}

SAMPLE ANSWER BANK STYLE REFERENCE:
{json.dumps(sample_answer_bank, indent=2)}

CONVERSATION SO FAR:
{convo_str}

Now answer ONLY as the candidate. Be brief. Be imperfect. Do not structure your answer. Sound human.
"""

    def _count_probe_like_questions(self, text: str) -> int:
        text_l = text.lower()
        probe_markers = [
            "specifically", "exactly", "what did you do", "what was your role",
            "what happened next", "how did you", "what result", "what was the outcome",
            "can you give an example", "tell me more", "walk me through",
            "what was the impact", "how did you measure", "what was your contribution",
            "what challenge", "how did you handle", "why did you", "what changed"
        ]
        return 1 if any(marker in text_l for marker in probe_markers) else 0

    def ask_candidate(self, session_id: str, interviewer_question: str) -> Dict[str, Any]:
        session = SessionStore.load_session(session_id)

        if session.get("status") == "completed":
            return {
                "reply_text": "This interview session has already ended.",
                "tokens": [],
                "timestamp": now_iso()
            }

        interviewer_question = sanitize_text(interviewer_question)
        if not interviewer_question:
            return {"reply_text": "", "tokens": [], "timestamp": now_iso()}

        # ── Detect question type ────────────────────────────────────────────
        small_talk_turn = is_small_talk(interviewer_question)
        behavioral_turn = is_behavioral_question(interviewer_question)

        # ── Check if interviewer is probing for personal/individual answer ──
        if detect_personal_probe(interviewer_question):
            session["pronoun_shift_triggered"] = True

        pronoun_shift = session.get("pronoun_shift_triggered", False)

        # ── Track competency indicators ─────────────────────────────────────
        indicators = detect_competency_indicators(interviewer_question)
        accumulated = session.get("competency_indicators_accumulated", {})
        for key, val in indicators.items():
            if val:
                accumulated[key] = True
        session["competency_indicators_accumulated"] = accumulated

        q_ts = now_iso()
        session["conversation"].append({
            "role": "interviewer",
            "content": interviewer_question,
            "timestamp": q_ts,
            "elapsed_mmss": format_elapsed_mmss(session["started_at"], q_ts),
            "is_small_talk": small_talk_turn,
            "is_behavioral": behavioral_turn,
        })

        session["metrics"]["interviewer_turns"] += 1
        session["metrics"]["probe_like_questions"] += self._count_probe_like_questions(interviewer_question)
        if small_talk_turn:
            session["metrics"]["small_talk_turns"] = session["metrics"].get("small_talk_turns", 0) + 1
        if behavioral_turn:
            session["metrics"]["behavioral_questions"] = session["metrics"].get("behavioral_questions", 0) + 1

        # ── ALL responses go through LLM (including small talk) ──────────────
        prompt = self.build_candidate_system_prompt(
            persona=session["persona"],
            difficulty=session["difficulty"],
            hidden_seed=session.get("hidden_competency_seed", {}),
            conversation=session["conversation"],
            pronoun_shift_triggered=pronoun_shift,
            is_small_talk_turn=small_talk_turn,
            is_behavioral_turn=behavioral_turn,
        )

        # Add random filler prefix for behavioral questions only (not small talk)
        filler_prefix = ""
        if not small_talk_turn and random.random() < 0.35:
            filler_prefix = random_filler() + " "

        # Shorter token limits: small talk = very short, behavioral = enough to feel complete
        if small_talk_turn:
            max_tokens = 80
            max_sent = 2
        else:
            max_tokens = 250  # enough room for up to 7 natural sentences
            max_sent = 7      # max 7 — but prompt asks for 3-5 so most replies stay moderate

        full_prompt = f"""{prompt}

INTERVIEWER QUESTION:
{interviewer_question}

CANDIDATE RESPONSE (aim for 3-5 sentences, max {max_sent}. Make the reply feel COMPLETE — finish your thought naturally. Do NOT cut off mid-thought. Do NOT say "good question"):
"""

        raw_answer = self.client.generate(full_prompt, temperature=0.90, num_predict=max_tokens)
        final_answer = sentence_cap(strip_candidate_followup_questions(raw_answer), max_sentences=max_sent)
        final_answer = sanitize_text(final_answer)

        if filler_prefix and final_answer:
            final_answer = filler_prefix + final_answer

        if not final_answer:
            final_answer = "Hmm, let me think about that. So there was this situation at work, things got a bit complicated honestly."

        if final_answer.endswith("?"):
            final_answer = final_answer.rstrip("?").strip()
            if final_answer and final_answer[-1] not in ".!":
                final_answer += "."

        # ── Track pronoun usage ─────────────────────────────────────────────
        pronoun_counts = count_we_vs_i(final_answer)
        pronoun_tracking = session.get("pronoun_tracking", {"total_we_count": 0, "total_i_count": 0})
        pronoun_tracking["total_we_count"] += pronoun_counts["we_count"]
        pronoun_tracking["total_i_count"] += pronoun_counts["i_count"]
        session["pronoun_tracking"] = pronoun_tracking

        c_ts = now_iso()
        tokens = tokenize_with_char_spans(final_answer)

        candidate_turn = {
            "role": "candidate",
            "content": final_answer,
            "timestamp": c_ts,
            "elapsed_mmss": format_elapsed_mmss(session["started_at"], c_ts),
            "tokens": tokens,
            "sentence_count": extract_sentence_count(final_answer),
            "audio_file": None,
            "pronoun_counts": pronoun_counts,
        }

        session["conversation"].append(candidate_turn)
        session["metrics"]["candidate_turns"] += 1
        session["metrics"]["total_candidate_sentences"] += candidate_turn["sentence_count"]

        SessionStore.save_session(session_id, session)

        return {
            "reply_text": final_answer,
            "tokens": tokens,
            "timestamp": c_ts
        }

    def attach_audio_to_latest_candidate_turn(self, session_id: str, audio_file: str) -> None:
        session = SessionStore.load_session(session_id)
        for msg in reversed(session["conversation"]):
            if msg.get("role") == "candidate":
                msg["audio_file"] = audio_file
                break
        SessionStore.save_session(session_id, session)

    def should_show_continue_popup(self, session_id: str) -> bool:
        session = SessionStore.load_session(session_id)
        interviewer_turns = session.get("metrics", {}).get("interviewer_turns", 0)
        candidate_turns = session.get("metrics", {}).get("candidate_turns", 0)
        created_at = datetime.fromisoformat(session["created_at"])
        elapsed_secs = (datetime.now() - created_at).total_seconds()
        return interviewer_turns < 5 or candidate_turns < 4 or elapsed_secs < 360

    def _weighted_percentage(self, param_scores: Dict[str, Dict[str, Any]]) -> int:
        total = 0.0
        for key, weight in self.PARAMETER_WEIGHTS.items():
            raw_score = param_scores.get(key, {}).get("score", 1)
            raw_score = max(1, min(5, int(raw_score)))
            total += (raw_score / 5.0) * weight
        return int(round(total))

    def build_score_breakdown_matrix(self, param_scores: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows = []
        running_total = 0.0
        for key, weight in self.PARAMETER_WEIGHTS.items():
            raw_score = param_scores.get(key, {}).get("score", 1)
            raw_score = max(1, min(5, int(raw_score)))
            weighted_contribution = round((raw_score / 5.0) * weight, 1)
            running_total += weighted_contribution
            rubric_level = COMPETENCY_RUBRIC.get(key, {}).get("levels", {}).get(raw_score, "")
            rows.append({
                "Parameter": self.PARAMETER_LABELS.get(key, key),
                "Weight (%)": weight,
                "Score (1-5)": raw_score,
                "Rubric Level": rubric_level,
                "Weighted Contribution": weighted_contribution,
                "Max Possible": weight,
            })
        rows.append({
            "Parameter": "TOTAL",
            "Weight (%)": 100,
            "Score (1-5)": "—",
            "Rubric Level": "",
            "Weighted Contribution": round(running_total, 1),
            "Max Possible": 100,
        })
        return rows

    def build_competency_addressed_summary(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a summary of which competency indicators were addressed vs not addressed.
        Based on actual transcript analysis.
        """
        accumulated = session.get("competency_indicators_accumulated", {})
        star_map = {
            "situation_explored": "Situation",
            "task_explored": "Task",
            "action_explored": "Action",
            "result_explored": "Result",
            "learning_explored": "Learning",
            "reasoning_explored": "Reasoning",
        }

        addressed = []
        not_addressed = []

        for key, label in star_map.items():
            if accumulated.get(key, False):
                addressed.append(label)
            else:
                not_addressed.append(label)

        return {
            "addressed": addressed,
            "not_addressed": not_addressed,
            "star_completeness_pct": round(len(addressed) / len(star_map) * 100),
        }

    def _normalize_parameter_scores(self, parsed_scores: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        normalized = {}
        for key in self.PARAMETER_WEIGHTS.keys():
            item = parsed_scores.get(key, {})
            score = max(1, min(5, int(item.get("score", 3))))
            rubric_desc = COMPETENCY_RUBRIC.get(key, {}).get("levels", {}).get(score, "")
            normalized[key] = {
                "score": score,
                "rubric_level_description": rubric_desc,
                "rationale": sanitize_text(item.get("rationale", "No rationale provided.")),
                "evidence": sanitize_text(item.get("evidence", "No transcript evidence provided.")),
                "what_good_looked_like": sanitize_text(
                    item.get("what_good_looked_like",
                             "Stronger interviewing would include sharper probing and better evidence validation.")
                )
            }
        return normalized

    def _heuristic_fallback_report(self, session: Dict[str, Any]) -> Dict[str, Any]:
        convo = session.get("conversation", [])
        interviewer_msgs = [m for m in convo if m.get("role") == "interviewer"]
        interviewer_text = " ".join([m.get("content", "") for m in interviewer_msgs]).lower()

        probe_count = session.get("metrics", {}).get("probe_like_questions", 0)
        interviewer_turns = session.get("metrics", {}).get("interviewer_turns", 0)
        small_talk_turns = session.get("metrics", {}).get("small_talk_turns", 0)
        behavioral_qs = session.get("metrics", {}).get("behavioral_questions", 0)

        mentions_result = any(x in interviewer_text for x in ["result", "outcome", "impact", "measurable", "improvement"])
        mentions_ownership = any(x in interviewer_text for x in ["what did you do", "your role", "your contribution", "personally", "exactly did you"])
        mentions_challenge = any(x in interviewer_text for x in ["challenge", "difficult", "resistance", "pushback", "obstacle"])
        mentions_validation = any(x in interviewer_text for x in ["how did you measure", "how do you know", "what data", "what evidence", "how was it validated"])
        mentions_followup = probe_count >= 2
        mentions_star = any(x in interviewer_text for x in ["situation", "task", "action", "result", "walk me through"])
        question_precision = any(x in interviewer_text for x in ["specific", "exactly", "one example", "walk me through one instance"])
        pronoun_shift_triggered = session.get("pronoun_shift_triggered", False)
        mentions_learning = any(x in interviewer_text for x in ["learn", "takeaway", "differently", "looking back", "reflection"])

        # Use rubric-aligned scoring
        # Depth of probing
        if probe_count >= 4 and mentions_ownership and mentions_result and mentions_challenge:
            depth_score = 5
        elif probe_count >= 3 and mentions_ownership and mentions_result:
            depth_score = 4
        elif probe_count >= 2:
            depth_score = 3
        elif probe_count >= 1:
            depth_score = 2
        else:
            depth_score = 1

        # BEI structure
        if mentions_star and mentions_learning and interviewer_turns >= 5:
            structure_score = 5
        elif mentions_star and interviewer_turns >= 4:
            structure_score = 4
        elif mentions_star and interviewer_turns >= 3:
            structure_score = 3
        elif interviewer_turns >= 2:
            structure_score = 2
        else:
            structure_score = 1

        # Evidence validation
        if mentions_validation and mentions_result:
            validation_score = 4
        elif mentions_validation:
            validation_score = 3
        elif mentions_result:
            validation_score = 2
        else:
            validation_score = 1

        # Question precision
        if question_precision and mentions_ownership and interviewer_turns >= 4:
            precision_score = 4
        elif question_precision:
            precision_score = 3
        elif mentions_ownership:
            precision_score = 2
        else:
            precision_score = 1

        # Listening
        if mentions_followup and interviewer_turns >= 5:
            listening_score = 4
        elif mentions_followup:
            listening_score = 3
        elif interviewer_turns >= 3:
            listening_score = 2
        else:
            listening_score = 1

        # Neutrality
        has_leading = "shouldn't" in interviewer_text or "don't you think" in interviewer_text or "wouldn't you say" in interviewer_text
        if not has_leading and interviewer_turns >= 3:
            neutrality_score = 4
        elif not has_leading:
            neutrality_score = 3
        else:
            neutrality_score = 2

        # Coverage
        comp_summary = self.build_competency_addressed_summary(session)
        addressed_count = len(comp_summary["addressed"])
        if addressed_count >= 5:
            coverage_score = 5
        elif addressed_count >= 4:
            coverage_score = 4
        elif addressed_count >= 3:
            coverage_score = 3
        elif addressed_count >= 2:
            coverage_score = 2
        else:
            coverage_score = 1

        # Time management
        effective_turns = interviewer_turns - small_talk_turns
        if effective_turns >= 6 and behavioral_qs >= 2:
            time_score = 4
        elif effective_turns >= 4:
            time_score = 3
        elif effective_turns >= 2:
            time_score = 2
        else:
            time_score = 1

        # Bonus: probed for personal contribution
        if pronoun_shift_triggered and mentions_ownership:
            depth_score = min(5, depth_score + 1)

        parameter_scores = {}
        raw_scores = {
            "depth_of_probing": depth_score,
            "bei_structure_adherence": structure_score,
            "evidence_validation": validation_score,
            "question_precision": precision_score,
            "listening_responsiveness": listening_score,
            "neutrality_non_leading": neutrality_score,
            "competency_coverage": coverage_score,
            "time_management": time_score,
        }

        rationales = {}

        # Depth of probing
        depth_parts = [f"Across this session, the interviewer asked {probe_count} probing follow-up{'s' if probe_count != 1 else ''}."]
        if mentions_ownership and mentions_result:
            depth_parts.append("They pushed into ownership and outcomes, which shows intent to go beyond surface-level responses.")
        if probe_count < 3:
            depth_parts.append("However, several candidate answers were accepted without being challenged further — a stronger interviewer would have asked at least one more layer on each story.")
        if not mentions_ownership:
            depth_parts.append("Ownership questions were missing, meaning the candidate could hide behind team language.")
        if not mentions_result:
            depth_parts.append("Outcome-related questions were not asked, leaving the impact of the actions unclear.")
        rationales["depth_of_probing"] = " ".join(depth_parts)

        # BEI structure
        bei_parts = []
        if mentions_star:
            bei_parts.append("The interviewer showed awareness of STAR by referencing situation, task, action, or result at various points.")
        else:
            bei_parts.append("The interview lacked a clear STAR-based flow — questions were asked without a visible narrative structure.")
        if mentions_learning:
            bei_parts.append("Learning and reflection were explored, which is often the most missed element in BEI interviews.")
        else:
            bei_parts.append("The interviewer did not explore what the candidate learned from the experience — this is a critical gap because Learning is what separates a good BEI from a routine interview.")
        rationales["bei_structure_adherence"] = " ".join(bei_parts)

        # Evidence validation
        ev_parts = []
        if mentions_validation:
            ev_parts.append("The interviewer asked validation-type questions, pushing the candidate to back up their claims with specifics.")
        else:
            ev_parts.append("No evidence validation was observed — the interviewer accepted claims without asking for numbers, timelines, or proof.")
        if mentions_result:
            ev_parts.append("Outcome-related probing helped ground the story in measurable reality.")
        else:
            ev_parts.append("Without probing for results, the interview missed the chance to test whether actions actually led to a tangible outcome.")
        rationales["evidence_validation"] = " ".join(ev_parts)

        # Question precision
        qp_parts = []
        if question_precision:
            qp_parts.append("Questions were targeted and specific, making it harder for the candidate to give vague responses.")
        else:
            qp_parts.append("Some questions were broad or multi-layered, which allowed the candidate to choose which part to answer and avoid specifics.")
        if mentions_ownership:
            qp_parts.append("Ownership-focused questioning helped narrow the candidate to their personal contribution.")
        else:
            qp_parts.append("The absence of ownership-specific questioning meant the candidate could stay in generalised team territory.")
        rationales["question_precision"] = " ".join(qp_parts)

        # Listening
        lr_parts = []
        if mentions_followup:
            lr_parts.append("Follow-up patterns suggest the interviewer was listening and adapting — questions built on what the candidate said.")
        else:
            lr_parts.append("The questioning pattern appeared more scripted than adaptive — follow-ups did not clearly reference what the candidate had just shared.")
        lr_parts.append(f"Over {interviewer_turns} turns, {'there was a visible thread connecting questions' if mentions_followup else 'the conversation felt more like a checklist than a dialogue'}.")
        rationales["listening_responsiveness"] = " ".join(lr_parts)

        # Neutrality
        nl_parts = []
        if has_leading:
            nl_parts.append("Some leading language was detected, which may have steered the candidate toward a desired answer.")
            nl_parts.append("This is important because leading questions produce rehearsed answers rather than authentic behavioural evidence.")
        else:
            nl_parts.append("The interviewer maintained a neutral, open-ended questioning style throughout.")
            nl_parts.append("Open-ended questions give the candidate room to reveal genuine behaviour, which is exactly what BEI is designed to capture.")
        rationales["neutrality_non_leading"] = " ".join(nl_parts)

        # Coverage
        addressed_list = ", ".join(comp_summary["addressed"]) if comp_summary["addressed"] else "none"
        not_addressed_list = ", ".join(comp_summary["not_addressed"]) if comp_summary["not_addressed"] else ""
        cc_parts = [f"The interviewer covered {len(comp_summary['addressed'])} out of 6 competency dimensions: {addressed_list}."]
        if not_addressed_list:
            cc_parts.append(f"The following were not addressed: {not_addressed_list}. Unexplored dimensions mean the assessor has blind spots in their evaluation.")
        else:
            cc_parts.append("All key indicators were explored, giving a comprehensive picture.")
        rationales["competency_coverage"] = " ".join(cc_parts)

        # Time management
        tm_parts = [f"The session had {effective_turns} substantive question turns (excluding {small_talk_turns} ice-breaker{'s' if small_talk_turns != 1 else ''})."]
        if effective_turns >= 5:
            tm_parts.append("The pacing allowed for depth across topics.")
        else:
            tm_parts.append("The interview could have benefited from more time — rushing through means each competency gets only surface treatment.")
        rationales["time_management"] = " ".join(tm_parts)

        # Blended improvement advice — not raw rubric quotes, but practical guidance informed by rubric
        improvement_advice = {
            "depth_of_probing": "Try asking at least 3 follow-up layers on each story: one for the specific action, one for the reasoning behind it, and one for the measurable outcome. Never move on until you've challenged at least one vague answer.",
            "bei_structure_adherence": "Walk each story through the full Situation → Task → Action → Result → Learning arc. Before moving to the next topic, explicitly close the narrative by asking what the candidate learned or would do differently.",
            "evidence_validation": "When the candidate says something like 'it improved significantly', challenge it immediately: 'What was the metric? Over what timeframe? What was the baseline before you started?'. Never leave a claim unquantified.",
            "question_precision": "Keep every question to a single sentence with a single intent. If you catch yourself adding 'and also...' — stop. Ask the first part, wait for the answer, then ask the second part separately.",
            "listening_responsiveness": "After each candidate response, your next question should directly reference something they just said. Avoid jumping to a pre-planned question — the best follow-ups come from what was just shared.",
            "neutrality_non_leading": "Start every question with What, How, Tell me, or Describe. Avoid yes/no questions and phrases like 'I'm sure you...' or 'Obviously you would have...'. Let the candidate fill in the blanks without hints.",
            "competency_coverage": "Map out the competency dimensions before the interview. Track which ones you've covered as you go. Aim for at least 2 distinct stories per competency, touching all key indicators.",
            "time_management": "Allocate time per competency before you start. Keep a mental clock — if you're spending too long on one story, wrap it up and move on. It's better to have moderate depth across all areas than deep coverage of one.",
        }

        for key in self.PARAMETER_WEIGHTS.keys():
            score = raw_scores[key]
            rubric_desc = COMPETENCY_RUBRIC.get(key, {}).get("levels", {}).get(score, "")
            parameter_scores[key] = {
                "score": score,
                "rubric_level_description": rubric_desc,
                "rationale": rationales.get(key, ""),
                "evidence": f"Transcript analysis: {probe_count} probes, {interviewer_turns} turns, pronoun shift {'triggered' if pronoun_shift_triggered else 'not triggered'}.",
                "what_good_looked_like": improvement_advice.get(key, "Focus on extracting deeper behavioural evidence."),
            }

        final_percentage = self._weighted_percentage(parameter_scores)
        readiness = "Ready" if final_percentage >= 70 else "Needs Practice"
        score_breakdown = self.build_score_breakdown_matrix(parameter_scores)

        return {
            "session_summary": f"The interview had {interviewer_turns} turns with {probe_count} probing questions. "
                               f"{'The interviewer successfully triggered personal ownership language.' if pronoun_shift_triggered else 'The interviewer did not push for personal ownership language.'} "
                               f"Competency coverage: {comp_summary['star_completeness_pct']}% of STAR+Learning indicators addressed.",
            "parameter_scores": parameter_scores,
            "weights": self.PARAMETER_WEIGHTS,
            "parameter_labels": self.PARAMETER_LABELS,
            "final_score_percent": final_percentage,
            "readiness_status": readiness,
            "score_breakdown_matrix": score_breakdown,
            "competency_addressed_summary": comp_summary,
            "top_strengths": [s for s in [
                "Started with a behaviour-oriented questioning approach." if mentions_star else None,
                "Made probing follow-up attempts." if probe_count >= 2 else None,
                "Maintained a neutral tone." if not has_leading else None,
                "Successfully triggered personal ownership shift." if pronoun_shift_triggered else None,
                "Explored outcomes and impact." if mentions_result else None,
            ] if s][:3] or ["Some interview structure was attempted."],
            "top_improvement_areas": [s for s in [
                "Probe more deeply into exact actions and ownership." if depth_score < 4 else None,
                "Complete the full STAR chain before closing an example." if structure_score < 4 else None,
                "Push for measurable outcomes and evidence." if validation_score < 4 else None,
                "Ask more precise, single-intent questions." if precision_score < 4 else None,
                "Improve follow-up responsiveness to candidate cues." if listening_score < 4 else None,
                "Explore learning and reflection." if not mentions_learning else None,
            ] if s][:4] or ["Continue refining probing depth."],
            "evidence_based_feedback": [
                {
                    "parameter": self.PARAMETER_LABELS.get(key, key),
                    "what_worked": f"Score: {parameter_scores[key]['score']}/5 — {parameter_scores[key].get('rubric_level_description', '')}",
                    "what_missed": COMPETENCY_RUBRIC.get(key, {}).get("levels", {}).get(5, ""),
                    "why_it_matters": f"This parameter carries {self.PARAMETER_WEIGHTS[key]}% weight in the final score.",
                    "what_to_do_next": parameter_scores[key].get("what_good_looked_like", ""),
                    "evidence": parameter_scores[key].get("rationale", ""),
                }
                for key in self.PARAMETER_WEIGHTS.keys()
            ],
            "assessor_feedback": {
                "strengths": [s for s in [
                    "Used behaviour-oriented questioning." if mentions_star else None,
                    "Showed probing intent." if probe_count >= 1 else None,
                    "Neutral questioning tone." if not has_leading else None,
                ] if s][:3] or ["Some structure was present."],
                "missed_probes": [s for s in [
                    "Did not consistently force individual ownership." if not pronoun_shift_triggered else None,
                    "Did not fully push for measurable outcomes." if not mentions_result else None,
                    "Challenges and resistance not explored." if not mentions_challenge else None,
                    "No exploration of learning or reflection." if not mentions_learning else None,
                ] if s][:3] or ["Minor gaps in probing depth."],
                "probing_quality": f"Probing score: {depth_score}/5. {COMPETENCY_RUBRIC['depth_of_probing']['levels'].get(depth_score, '')}",
                "better_questions": [
                    "What exactly did you do personally in that situation?",
                    "What challenge did you face when you tried to move this forward?",
                    "How did you know your approach worked — what data showed that?",
                    "What changed because of your action?",
                    "Looking back, what would you do differently?",
                ],
                "effectiveness_rating": max(1, min(5, round(final_percentage / 20)))
            },
            "competency_evidence_summary": {
                "evidence_observed": [f"Indicator '{ind}' was {'addressed' if comp_summary['addressed'] and ind in comp_summary['addressed'] else 'not addressed'}" for ind in ["Situation", "Task", "Action", "Result", "Learning", "Reasoning"]],
                "star_completeness": {
                    "situation": "Covered" if "Situation" in comp_summary["addressed"] else "Not covered",
                    "task": "Covered" if "Task" in comp_summary["addressed"] else "Not covered",
                    "action": "Covered" if "Action" in comp_summary["addressed"] else "Not covered",
                    "result": "Covered" if "Result" in comp_summary["addressed"] else "Not covered",
                    "learning": "Covered" if "Learning" in comp_summary["addressed"] else "Not covered",
                },
                "assessor_effectiveness": [
                    f"Addressed {len(comp_summary['addressed'])} of 6 competency indicators.",
                    f"Missing: {', '.join(comp_summary['not_addressed']) or 'None'}.",
                ]
            },
            "manager_report": {
                "assessor_name": "Assessor",
                "date": datetime.now().strftime("%d %B %Y"),
                "interview_effectiveness_score": f"{max(1, min(5, round(final_percentage / 20)))}/5",
                "star_extraction_score": f"{structure_score}/5",
                "probing_score": f"{depth_score}/5",
                "top_development_areas": [s for s in [
                    "Deeper probing on ownership" if depth_score < 4 else None,
                    "Complete STAR extraction" if structure_score < 4 else None,
                    "Drive toward quantified outcomes" if validation_score < 4 else None,
                    "Explore learning and reflection" if not mentions_learning else None,
                ] if s][:4] or ["Continue refining technique."],
                "practice_recommendation": f"Focus on completing one full STAR+Learning example. Current competency coverage is {comp_summary['star_completeness_pct']}%."
            },
            "report_submission": {
                "assessor_name": "Assessor",
                "assessor_email": "not_provided@local",
                "interview_effectiveness_score": f"{max(1, min(5, round(final_percentage / 20)))}/5",
                "star_score": f"{structure_score}/5",
                "probing_score": f"{depth_score}/5",
                "manager_report_summary": f"Needs stronger probing (scored {depth_score}/5), better STAR completion ({structure_score}/5), and more outcome-focused questioning ({validation_score}/5)."
            },
            "candidate_summary": {
                "overall_impression": f"You conducted an interview with {interviewer_turns} questions. Your overall score was {final_percentage}% ({readiness}). "
                                     f"{'You successfully pushed for personal ownership.' if pronoun_shift_triggered else 'You did not push for personal ownership — the candidate stayed in team language.'} "
                                     f"Competency indicators addressed: {len(comp_summary['addressed'])} of 6.",
                "strengths": [s for s in [
                    "Used behaviour-oriented questioning." if mentions_star else None,
                    "Probed for individual contribution." if pronoun_shift_triggered else None,
                    "Maintained neutral tone." if not has_leading else None,
                ] if s][:3] or ["Some structure was present."],
                "improvements": [s for s in [
                    "Probe harder on exact actions and ownership." if depth_score < 4 else None,
                    "Do not move on before result extraction." if not mentions_result else None,
                    "Push for quantified evidence and final impact." if validation_score < 4 else None,
                    "Explore what the candidate learned from the experience." if not mentions_learning else None,
                ] if s][:4] or ["Continue improving probing depth."],
            }
        }

    def generate_final_report(self, session_id: str) -> Dict[str, Any]:
        session = SessionStore.load_session(session_id)

        if session.get("status") == "completed" and session.get("final_report"):
            return session["final_report"]

        persona = session["persona"]
        convo = session["conversation"]

        transcript = "\n".join(
            [
                f"[{x.get('elapsed_mmss', '00:00')}] {x['role'].upper()}: {x['content']}"
                for x in convo
            ]
        )

        # Build rubric string for the evaluator
        rubric_text = ""
        for key, rubric in COMPETENCY_RUBRIC.items():
            rubric_text += f"\n{rubric['label']} (Weight: {rubric['weight']}%):\n"
            for level in sorted(rubric["levels"].keys(), reverse=True):
                rubric_text += f"  {level}/5: {rubric['levels'][level]}\n"

        comp_summary = self.build_competency_addressed_summary(session)

        evaluator_prompt = f"""
You are evaluating a completed Behavioral Event Interview practice session.

IMPORTANT:
- Evaluate the INTERVIEWER / ASSESSOR, not the candidate.
- The candidate is simulated.
- Judge how effectively the interviewer extracted behavioural evidence.
- The AI candidate was designed to initially use "we" and shift to "I" when probed. Credit the interviewer if they triggered this shift.

SCORING RUBRIC — USE THIS EXACTLY:
{rubric_text}

COMPETENCY INDICATORS ADDRESSED BY INTERVIEWER:
- Addressed: {', '.join(comp_summary['addressed']) or 'None'}
- Not addressed: {', '.join(comp_summary['not_addressed']) or 'None'}
- STAR+Learning completeness: {comp_summary['star_completeness_pct']}%

FOR EACH PARAMETER RETURN:
- score (integer 1-5, matching the rubric level that best fits)
- rationale (2-3 sentences, transcript-grounded, referencing the rubric level)
- evidence (what in the transcript supports this score)
- what_good_looked_like (concrete example of better practice — quote from the level 5 rubric)

ALSO RETURN:
- session_summary (mention competency indicators addressed vs not addressed)
- top_strengths (list of 3)
- top_improvement_areas (list of 4)
- evidence_based_feedback (array, one entry per parameter, each with: parameter, what_worked, what_missed, why_it_matters, what_to_do_next, evidence)
- assessor_feedback (strengths, missed_probes, probing_quality, better_questions, effectiveness_rating)
- competency_evidence_summary (evidence_observed, star_completeness with situation/task/action/result/learning, assessor_effectiveness)
- manager_report
- report_submission
- candidate_summary

STRICT JSON ONLY — no markdown, no preamble:
{{
  "session_summary": "...",
  "parameter_scores": {{
    "depth_of_probing": {{"score": 1, "rationale": "...", "evidence": "...", "what_good_looked_like": "..."}},
    "bei_structure_adherence": {{"score": 1, "rationale": "...", "evidence": "...", "what_good_looked_like": "..."}},
    "evidence_validation": {{"score": 1, "rationale": "...", "evidence": "...", "what_good_looked_like": "..."}},
    "question_precision": {{"score": 1, "rationale": "...", "evidence": "...", "what_good_looked_like": "..."}},
    "listening_responsiveness": {{"score": 1, "rationale": "...", "evidence": "...", "what_good_looked_like": "..."}},
    "neutrality_non_leading": {{"score": 1, "rationale": "...", "evidence": "...", "what_good_looked_like": "..."}},
    "competency_coverage": {{"score": 1, "rationale": "...", "evidence": "...", "what_good_looked_like": "..."}},
    "time_management": {{"score": 1, "rationale": "...", "evidence": "...", "what_good_looked_like": "..."}}
  }},
  "top_strengths": ["...", "...", "..."],
  "top_improvement_areas": ["...", "...", "...", "..."],
  "evidence_based_feedback": [
    {{"parameter": "...", "what_worked": "...", "what_missed": "...", "why_it_matters": "...", "what_to_do_next": "...", "evidence": "..."}}
  ],
  "assessor_feedback": {{
    "strengths": ["...", "..."],
    "missed_probes": ["...", "..."],
    "probing_quality": "...",
    "better_questions": ["...", "...", "..."],
    "effectiveness_rating": 2
  }},
  "competency_evidence_summary": {{
    "evidence_observed": ["...", "..."],
    "star_completeness": {{"situation": "...", "task": "...", "action": "...", "result": "...", "learning": "..."}},
    "assessor_effectiveness": ["...", "..."]
  }},
  "manager_report": {{
    "assessor_name": "Assessor",
    "date": "{datetime.now().strftime("%d %B %Y")}",
    "interview_effectiveness_score": "2/5",
    "star_extraction_score": "1.5/5",
    "probing_score": "2/5",
    "top_development_areas": ["...", "..."],
    "practice_recommendation": "..."
  }},
  "report_submission": {{
    "assessor_name": "Assessor",
    "assessor_email": "not_provided@local",
    "interview_effectiveness_score": "2/5",
    "star_score": "1.5/5",
    "probing_score": "2/5",
    "manager_report_summary": "..."
  }},
  "candidate_summary": {{
    "overall_impression": "...",
    "strengths": ["...", "..."],
    "improvements": ["...", "..."]
  }}
}}

HIDDEN SESSION CONTEXT:
- Competency: {session.get("selected_competency")}
- Hidden seed: {json.dumps(session.get("hidden_competency_seed", {}), indent=2)}
- Difficulty: {session.get("difficulty")}
- Pronoun shift triggered by interviewer: {session.get("pronoun_shift_triggered", False)}
- Competency indicators addressed: {json.dumps(comp_summary)}

PERSONA:
{json.dumps(persona.get("idealized_candidate_profile", {}), indent=2)}

BEHAVIOR MODEL:
{json.dumps(persona.get("interviewee_behaviour_model", {}), indent=2)}

TRANSCRIPT:
{transcript}
"""

        parsed = None
        # ALWAYS attempt LLM evaluation — even short sessions need nuanced judgment.
        # The heuristic fallback is only used if the LLM fails to return valid JSON.
        # Short transcripts naturally produce shorter prompts → faster inference anyway.
        try:
            # Token budget: scale with transcript length for efficiency
            # Short sessions need less generation, long ones get more room
            interviewer_turn_count = session.get("metrics", {}).get("interviewer_turns", 0)
            token_budget = min(1400, max(800, interviewer_turn_count * 150))
            raw = self.client.generate(evaluator_prompt, temperature=0.15, num_predict=token_budget)
            parsed = safe_json_extract(raw)
        except Exception:
            parsed = None

        if not parsed:
            report = self._heuristic_fallback_report(session)
            SessionStore.end_session(session_id, report)
            return report

        parameter_scores = self._normalize_parameter_scores(parsed.get("parameter_scores", {}))
        final_percentage = self._weighted_percentage(parameter_scores)
        readiness = "Ready" if final_percentage >= 70 else "Needs Practice"
        score_breakdown = self.build_score_breakdown_matrix(parameter_scores)

        report = {
            "session_summary": sanitize_text(parsed.get("session_summary", "A structured assessment was generated for this interview session.")),
            "parameter_scores": parameter_scores,
            "weights": self.PARAMETER_WEIGHTS,
            "parameter_labels": self.PARAMETER_LABELS,
            "final_score_percent": final_percentage,
            "readiness_status": readiness,
            "score_breakdown_matrix": score_breakdown,
            "competency_addressed_summary": comp_summary,
            "top_strengths": parsed.get("top_strengths", []),
            "top_improvement_areas": parsed.get("top_improvement_areas", []),
            "evidence_based_feedback": parsed.get("evidence_based_feedback", []),
            "assessor_feedback": parsed.get("assessor_feedback", {
                "strengths": [], "missed_probes": [], "probing_quality": "",
                "better_questions": [], "effectiveness_rating": 3
            }),
            "competency_evidence_summary": parsed.get("competency_evidence_summary", {
                "evidence_observed": [],
                "star_completeness": {"situation": "", "task": "", "action": "", "result": "", "learning": ""},
                "assessor_effectiveness": []
            }),
            "manager_report": parsed.get("manager_report", {
                "assessor_name": "Assessor",
                "date": datetime.now().strftime("%d %B %Y"),
                "interview_effectiveness_score": "3/5",
                "star_extraction_score": "3/5",
                "probing_score": "3/5",
                "top_development_areas": [],
                "practice_recommendation": ""
            }),
            "report_submission": parsed.get("report_submission", {
                "assessor_name": "Assessor",
                "assessor_email": "not_provided@local",
                "interview_effectiveness_score": "3/5",
                "star_score": "3/5",
                "probing_score": "3/5",
                "manager_report_summary": ""
            }),
            "candidate_summary": parsed.get("candidate_summary", {
                "overall_impression": "",
                "strengths": [],
                "improvements": []
            })
        }

        SessionStore.end_session(session_id, report)
        return report


# ─────────────────────────────────────────────────────────────────────────────
# 10 DIVERSE PERSONAS
# ─────────────────────────────────────────────────────────────────────────────

def get_all_personas():
    return [
        _persona_priya(),
        _persona_arjun(),
        _persona_sarah(),
        _persona_liang(),
        _persona_fatima(),
        _persona_marcus(),
        _persona_elena(),
        _persona_rohan(),
        _persona_nadia(),
        _persona_james(),
    ]


def _persona_priya():
    return {
        "name": "Priya Sharma — Mid-Level Ops Lead, Telecom",
        "gender": "female",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["Operations Analyst", "Process Improvement Lead", "Cross-functional Coordinator"],
                "industries": ["Telecom", "FMCG"],
                "progression": "Grew from analyst to ops lead over 5 years; strong execution, weak strategy framing"
            },
            "core_strengths": ["execution follow-through", "stakeholder coordination", "data-driven ops", "problem solving"],
            "behavioral_traits": ["conversational", "team-first", "avoids self-promotion", "context-heavy"],
            "typical_thinking_patterns": ["execution-first", "pragmatic", "not always structured"],
            "common_gaps": ["over-explains team context", "uses 'we' heavily", "avoids quantifying outcomes unless pushed"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "starts with team context and 'we' before any personal detail",
            "level_of_detail": "moderate but vague on outcomes",
            "star_naturally": "does not naturally follow STAR",
            "response_to_probing": "cooperates but repeats context first",
            "response_to_pressure": "slightly defensive, says she doesn't recall exact numbers",
            "typical_mistakes": ["says 'we' instead of 'I'", "skips result", "gives generic statements"]
        },
        "response_style_rules": [
            "Use a mix of 'we' (60%) and 'I' (40%) by default.",
            "Do not give complete STAR answers.",
            "Leave result gap intentionally.",
            "Limit to 5 sentences max.",
            "Sound like a real person recalling, not presenting."
        ],
        "hidden_competencies": [
            {"competency": "Strategic Thinking", "scenario_seed": "conflicting priorities, partial data"},
            {"competency": "Stakeholder Management", "scenario_seed": "alignment without authority"},
            {"competency": "Execution", "scenario_seed": "delivery under ambiguity"}
        ],
        "sample_answer_bank": [
            {"competency": "Problem Solving", "sample_style": "starts broadly, stops short of result"},
            {"competency": "Execution", "sample_style": "high effort, weak strategic framing"}
        ]
    }


def _persona_arjun():
    return {
        "name": "Arjun Mehta — Senior Software Engineer, FinTech",
        "gender": "male",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["Backend Engineer", "Tech Lead", "Platform Architect"],
                "industries": ["FinTech", "Banking Tech", "SaaS"],
                "progression": "8 years in engineering; strong technical depth, uncomfortable with ambiguous behavioral questions"
            },
            "core_strengths": ["technical problem solving", "system design", "code quality ownership", "team mentoring"],
            "behavioral_traits": ["precise", "logical", "slightly terse", "uncomfortable with open-ended soft questions"],
            "typical_thinking_patterns": ["systems-first", "data-driven", "solution-oriented"],
            "common_gaps": ["too technical in behavioral answers", "skips emotional/people aspects", "gives implementation detail instead of behavioral evidence"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "jumps into technical context, misses the behavioral layer",
            "level_of_detail": "high on technical, low on interpersonal",
            "star_naturally": "structures answers like a technical brief, not STAR",
            "response_to_probing": "gives more technical detail when probed, not more behavioral evidence",
            "response_to_pressure": "gets more precise and technical, not more reflective",
            "typical_mistakes": ["says 'we built' instead of 'I decided'", "no result in human terms", "avoids talking about conflict or challenges with people"]
        },
        "response_style_rules": [
            "Lead with the system or technical problem, not personal decision.",
            "Use 'we built' or 'the team shipped' mixed with occasional 'I handled' or 'I noticed'.",
            "Avoid discussing interpersonal dynamics unless directly asked.",
            "Do not mention measurable business outcomes unless probed.",
            "Max 5 sentences. Sound like an engineer, not a consultant."
        ],
        "hidden_competencies": [
            {"competency": "Problem Solving", "scenario_seed": "production incident, unclear ownership"},
            {"competency": "Stakeholder Management", "scenario_seed": "pushing back on product timelines"},
            {"competency": "Strategic Thinking", "scenario_seed": "tech debt vs. feature delivery trade-off"}
        ],
        "sample_answer_bank": [
            {"competency": "Execution", "sample_style": "describes what was built, not why or what changed"},
            {"competency": "Problem Solving", "sample_style": "technical root cause focus, no behavioral evidence"}
        ]
    }


def _persona_sarah():
    return {
        "name": "Sarah O'Brien — HR Business Partner, Retail",
        "gender": "female",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["HR Generalist", "L&D Coordinator", "HRBP"],
                "industries": ["Retail", "Hospitality", "Healthcare"],
                "progression": "6 years in HR; strong people skills, overly narrative in answers"
            },
            "core_strengths": ["relationship building", "conflict resolution", "change management", "communication"],
            "behavioral_traits": ["empathetic", "verbose", "story-heavy", "people-focused"],
            "typical_thinking_patterns": ["people-first", "process-second", "emotionally intelligent"],
            "common_gaps": ["tells long stories without clear individual action", "avoids talking about business metrics", "hard to extract specific decisions from narrative"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "launches into a narrative about the team or organisational situation",
            "level_of_detail": "high on people dynamics, low on personal decisions and business outcomes",
            "star_naturally": "tells stories but loses the action/result thread",
            "response_to_probing": "adds more narrative context when probed, not more specific actions",
            "response_to_pressure": "gets more personal and emotional rather than more specific",
            "typical_mistakes": ["uses 'we worked through it together'", "no result stated", "credits the team constantly"]
        },
        "response_style_rules": [
            "Lead with the people situation and how the team felt.",
            "Use 'we worked together' and collective language, mixed with some personal observations.",
            "Be narrative and warm, not crisp or outcome-focused.",
            "Do not mention measurable outcomes unless asked directly.",
            "Max 5 sentences. Sound caring and reflective, not strategic."
        ],
        "hidden_competencies": [
            {"competency": "Stakeholder Management", "scenario_seed": "managing difficult line managers during restructure"},
            {"competency": "Problem Solving", "scenario_seed": "high attrition in one department"},
            {"competency": "Execution", "scenario_seed": "rolling out a new performance process under resistance"}
        ],
        "sample_answer_bank": [
            {"competency": "Stakeholder Management", "sample_style": "warm narrative, no clear individual action"},
            {"competency": "Problem Solving", "sample_style": "describes the human issue well, skips solution and outcome"}
        ]
    }


def _persona_liang():
    return {
        "name": "Liang Wei — Supply Chain Manager, Manufacturing",
        "gender": "male",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["Procurement Analyst", "Logistics Coordinator", "Supply Chain Manager"],
                "industries": ["Automotive", "Electronics Manufacturing", "FMCG"],
                "progression": "10 years in supply chain; strong process discipline, conservative communicator"
            },
            "core_strengths": ["process optimisation", "vendor management", "risk mitigation", "cost reduction"],
            "behavioral_traits": ["methodical", "conservative", "understates achievements", "dislikes exaggeration"],
            "typical_thinking_patterns": ["process-first", "risk-aware", "data-driven but conservative with claims"],
            "common_gaps": ["underplays personal role", "hedges outcomes with 'it was a team effort'", "avoids making bold claims about impact"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "describes the process or system before mentioning any personal action",
            "level_of_detail": "high on process, low on personal decision-making",
            "star_naturally": "describes what happened procedurally, not what he decided personally",
            "response_to_probing": "provides more process detail, not more personal agency",
            "response_to_pressure": "hedges further, credits the system or team",
            "typical_mistakes": ["'the process required us to...'", "no personal initiative stated", "outcome described as 'we improved things somewhat'"]
        },
        "response_style_rules": [
            "Describe the process or system as the driver, not yourself.",
            "Use passive voice where possible: 'the decision was made', 'it was identified'.",
            "Do not claim outcomes without heavy hedging.",
            "Avoid mentioning specific numbers unless directly asked.",
            "Max 5 sentences. Sound precise, careful, understated."
        ],
        "hidden_competencies": [
            {"competency": "Problem Solving", "scenario_seed": "supplier failure during peak season"},
            {"competency": "Execution", "scenario_seed": "cost reduction initiative under budget pressure"},
            {"competency": "Strategic Thinking", "scenario_seed": "building resilience into the supply chain"}
        ],
        "sample_answer_bank": [
            {"competency": "Execution", "sample_style": "process-heavy, personal ownership unclear"},
            {"competency": "Problem Solving", "sample_style": "systematic approach described, no individual hero moment"}
        ]
    }


def _persona_fatima():
    return {
        "name": "Fatima Al-Rashid — Marketing Director, FMCG",
        "gender": "female",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["Brand Manager", "Category Lead", "Marketing Director"],
                "industries": ["FMCG", "Consumer Goods", "Retail Media"],
                "progression": "12 years; strong brand instinct, strategic thinker but uses business jargon heavily"
            },
            "core_strengths": ["brand strategy", "campaign management", "consumer insight", "P&L ownership"],
            "behavioral_traits": ["confident", "articulate", "jargon-heavy", "result-oriented but vague on personal actions"],
            "typical_thinking_patterns": ["consumer-first", "brand-led", "commercially minded"],
            "common_gaps": ["uses strategy language to sound impressive but hides lack of personal detail", "hard to pin down on specific individual actions", "results framed as brand performance, not personal decisions"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "leads with strategic framing and market context",
            "level_of_detail": "high on strategy, low on 'what did I specifically do'",
            "star_naturally": "goes from context straight to outcome, skips action",
            "response_to_probing": "adds more market context when probed",
            "response_to_pressure": "deflects to brand performance data rather than personal decisions",
            "typical_mistakes": ["'the brand needed to...'", "no clear individual decision", "jumps to result without action"]
        },
        "response_style_rules": [
            "Lead with market context and brand strategy language.",
            "Use business jargon: 'portfolio', 'consumer franchise', 'penetration'.",
            "Skip the personal action and go to outcome.",
            "Frame results as brand/business performance, not your decision.",
            "Max 5 sentences. Sound senior and strategic."
        ],
        "hidden_competencies": [
            {"competency": "Strategic Thinking", "scenario_seed": "declining brand share, competitor aggression"},
            {"competency": "Stakeholder Management", "scenario_seed": "aligning sales and marketing on a new launch"},
            {"competency": "Execution", "scenario_seed": "delivering a campaign under budget and timeline pressure"}
        ],
        "sample_answer_bank": [
            {"competency": "Strategic Thinking", "sample_style": "confident framing, no personal decision visible"},
            {"competency": "Stakeholder Management", "sample_style": "talks about alignment broadly, not what she specifically did"}
        ]
    }


def _persona_marcus():
    return {
        "name": "Marcus Thompson — Junior Consultant, Management Consulting",
        "gender": "male",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["Business Analyst", "Associate Consultant"],
                "industries": ["Management Consulting", "Public Sector"],
                "progression": "3 years post-MBA; eager, over-prepared, gives textbook answers"
            },
            "core_strengths": ["structured thinking", "data analysis", "slide communication", "client-facing work"],
            "behavioral_traits": ["eager to impress", "over-structured", "uses frameworks unnecessarily", "slightly nervous under follow-up"],
            "typical_thinking_patterns": ["framework-first", "hypothesis-driven", "problem-structured"],
            "common_gaps": ["sounds rehearsed, not authentic", "over-structures behavioral answers like a case", "no personal vulnerability or honest gaps"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "uses a structured opener like 'So there were three main challenges...'",
            "level_of_detail": "high on frameworks, low on genuine behavioral evidence",
            "star_naturally": "tries to do STAR but sounds rehearsed and hollow",
            "response_to_probing": "gives another structured point, not more honest detail",
            "response_to_pressure": "doubles down on structure, becomes more formal",
            "typical_mistakes": ["'the situation had three dimensions'", "sounds like a textbook", "no genuine emotion or difficulty admitted"]
        },
        "response_style_rules": [
            "Open with a structured or numbered framing.",
            "Sound slightly rehearsed and formal.",
            "Avoid genuine emotion or admitting real difficulty.",
            "Give technically correct but hollow answers.",
            "Max 5 sentences. Sound like someone who prepped too hard."
        ],
        "hidden_competencies": [
            {"competency": "Problem Solving", "scenario_seed": "client pushback on recommendation"},
            {"competency": "Execution", "scenario_seed": "under-resourced project delivery"},
            {"competency": "Stakeholder Management", "scenario_seed": "navigating senior client skepticism"}
        ],
        "sample_answer_bank": [
            {"competency": "Problem Solving", "sample_style": "structured, sounds rehearsed, lacks authentic evidence"},
            {"competency": "Execution", "sample_style": "textbook answer, no personal struggle admitted"}
        ]
    }


def _persona_elena():
    return {
        "name": "Elena Vasquez — Product Manager, SaaS Startup",
        "gender": "female",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["UX Researcher", "Product Owner", "Senior PM"],
                "industries": ["B2B SaaS", "EdTech", "HealthTech"],
                "progression": "7 years; customer-obsessed, strong product intuition, struggles with ownership language"
            },
            "core_strengths": ["user research", "roadmap prioritisation", "cross-functional leadership", "data-informed decisions"],
            "behavioral_traits": ["customer-centric", "collaborative", "humble", "avoids claiming credit"],
            "typical_thinking_patterns": ["user-first", "data-informed", "hypothesis-driven"],
            "common_gaps": ["deflects credit to engineering or design", "uses 'we decided' even for decisions she drove", "hard to pin down on her own POV"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "leads with customer problem or user insight",
            "level_of_detail": "high on problem framing, low on personal decision-making",
            "star_naturally": "tells product stories but loses the 'I' thread completely",
            "response_to_probing": "adds more customer context, not more personal action",
            "response_to_pressure": "genuinely confused why she needs to separate herself from the team",
            "typical_mistakes": ["'we shipped it'", "'the team aligned on...'", "no personal stake or decision visible"]
        },
        "response_style_rules": [
            "Lead with customer problem and user insight.",
            "Use 'we' and 'the team' frequently but include occasional 'I noticed' or 'I felt'.",
            "Sound genuinely collaborative, not evasive.",
            "Do not claim personal decisions even when you drove them.",
            "Max 5 sentences. Sound like a PM who loves the product."
        ],
        "hidden_competencies": [
            {"competency": "Strategic Thinking", "scenario_seed": "pivoting roadmap under investor pressure"},
            {"competency": "Execution", "scenario_seed": "shipping under-resourced with competing priorities"},
            {"competency": "Stakeholder Management", "scenario_seed": "engineering saying no to a key feature"}
        ],
        "sample_answer_bank": [
            {"competency": "Strategic Thinking", "sample_style": "great framing, no personal stance"},
            {"competency": "Execution", "sample_style": "team-focused, personal contribution invisible"}
        ]
    }


def _persona_rohan():
    return {
        "name": "Rohan Kapoor — Finance Manager, Private Equity",
        "gender": "male",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["Financial Analyst", "Associate", "Finance Manager"],
                "industries": ["Private Equity", "Investment Banking", "Corporate Finance"],
                "progression": "9 years; sharp with numbers, guarded in interviews, very conscious of what he shares"
            },
            "core_strengths": ["financial modelling", "deal analysis", "due diligence", "stakeholder reporting"],
            "behavioral_traits": ["guarded", "precise", "measured", "uncomfortable with ambiguity in questions"],
            "typical_thinking_patterns": ["numbers-first", "risk-aware", "detail-oriented"],
            "common_gaps": ["gives very short answers", "withholds context unless directly asked", "avoids committing to a clear personal stance"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "gives a very brief, clipped opener before waiting for a follow-up",
            "level_of_detail": "deliberately sparse; shares only what is asked",
            "star_naturally": "gives the bare minimum — situation only, nothing more",
            "response_to_probing": "unlocks more detail when probed but still keeps answers brief",
            "response_to_pressure": "becomes slightly more formal and precise, not warmer",
            "typical_mistakes": ["'it was a standard deal process'", "no emotion or difficulty mentioned", "'I can't go into too much detail on that one'"]
        },
        "response_style_rules": [
            "Give a clipped, minimal opener.",
            "Do not volunteer context unless directly asked.",
            "Use formal, finance-adjacent language.",
            "Sound measured and deliberate, not evasive.",
            "Max 4 sentences. Sound like someone trained to be careful."
        ],
        "hidden_competencies": [
            {"competency": "Problem Solving", "scenario_seed": "model error found before board presentation"},
            {"competency": "Strategic Thinking", "scenario_seed": "recommending against a deal under pressure"},
            {"competency": "Stakeholder Management", "scenario_seed": "managing partner expectations on a slow deal"}
        ],
        "sample_answer_bank": [
            {"competency": "Problem Solving", "sample_style": "minimal, requires heavy probing to get the story"},
            {"competency": "Stakeholder Management", "sample_style": "formal language, no emotion, very guarded"}
        ]
    }


def _persona_nadia():
    return {
        "name": "Nadia Osei — NGO Programme Director, Development Sector",
        "gender": "female",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["Field Officer", "Programme Coordinator", "Programme Director"],
                "industries": ["International Development", "Humanitarian Aid", "Social Sector"],
                "progression": "14 years in development; strong mission commitment, speaks in collective and systemic terms"
            },
            "core_strengths": ["programme design", "community engagement", "donor management", "cross-cultural leadership"],
            "behavioral_traits": ["mission-driven", "systemic thinker", "collective ownership", "resistant to individual credit-taking"],
            "typical_thinking_patterns": ["systems-level", "equity-oriented", "community-first"],
            "common_gaps": ["describes collective action, not personal decision", "resists framing herself as the hero", "outcomes are community-level, hard to attribute personally"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "starts with the community problem or systemic barrier",
            "level_of_detail": "high on context and community dynamics, low on personal initiative",
            "star_naturally": "talks about what 'we as a team' or 'the community' did, not herself",
            "response_to_probing": "gets slightly uncomfortable with personal credit questions, adds more system context",
            "response_to_pressure": "gently pushes back: 'it really was a collective effort'",
            "typical_mistakes": ["'the programme achieved...'", "no personal decision visible", "credits the community constantly"]
        },
        "response_style_rules": [
            "Lead with the systemic problem or community context.",
            "Use 'we', 'the programme', 'the community' frequently but include occasional personal observation.",
            "Sound genuinely committed, not evasive.",
            "Resist personal credit-taking even when probed.",
            "Max 5 sentences. Sound like a veteran development professional."
        ],
        "hidden_competencies": [
            {"competency": "Stakeholder Management", "scenario_seed": "navigating government and donor priorities in conflict"},
            {"competency": "Execution", "scenario_seed": "delivering under political and logistical constraints"},
            {"competency": "Strategic Thinking", "scenario_seed": "rethinking programme theory of change mid-cycle"}
        ],
        "sample_answer_bank": [
            {"competency": "Execution", "sample_style": "collective language, no personal hero moment"},
            {"competency": "Stakeholder Management", "sample_style": "system-level framing, personal role invisible"}
        ]
    }


def _persona_james():
    return {
        "name": "James Okafor — Sales Director, B2B Technology",
        "gender": "male",
        "idealized_candidate_profile": {
            "professional_background": {
                "roles": ["Account Executive", "Regional Sales Manager", "Sales Director"],
                "industries": ["Enterprise Software", "Cloud Services", "B2B Tech"],
                "progression": "11 years in sales; target-driven, confident, but over-attributes success to charm and relationships"
            },
            "core_strengths": ["client relationship management", "deal closing", "pipeline management", "team motivation"],
            "behavioral_traits": ["outgoing", "confident", "anecdote-heavy", "sometimes oversimplifies complex decisions"],
            "typical_thinking_patterns": ["outcome-first", "relationship-driven", "instinct-led"],
            "common_gaps": ["says 'I just picked up the phone' without explaining the decision behind it", "oversimplifies process", "hard to extract structured thinking from conversational answers"]
        },
        "interviewee_behaviour_model": {
            "answer_start_style": "launches with a confident anecdote or punchline",
            "level_of_detail": "high on story energy, low on decision logic",
            "star_naturally": "jumps to result first, then backtracks to context",
            "response_to_probing": "adds more relationship colour, not more structured thinking",
            "response_to_pressure": "stays confident, slightly dismissive of over-analysis",
            "typical_mistakes": ["'so I called the CEO directly'", "no structured thinking visible", "outcome stated upfront, evidence missing"]
        },
        "response_style_rules": [
            "Lead with a punchy anecdote or the result.",
            "Sound confident and energetic.",
            "Skip the decision logic — just say what you did.",
            "Use relationship language: 'called', 'sat down with', 'trusted me'.",
            "Max 5 sentences. Sound like a sales person, not an analyst."
        ],
        "hidden_competencies": [
            {"competency": "Stakeholder Management", "scenario_seed": "a key account threatening to churn"},
            {"competency": "Execution", "scenario_seed": "rebuilding a declining territory"},
            {"competency": "Problem Solving", "scenario_seed": "deal stalled due to internal procurement issues"}
        ],
        "sample_answer_bank": [
            {"competency": "Stakeholder Management", "sample_style": "great anecdote, no structured thinking"},
            {"competency": "Execution", "sample_style": "result stated first, decision-making process missing"}
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Seed all personas on import
# ─────────────────────────────────────────────────────────────────────────────
PersonaStore.ensure_sample_personas()