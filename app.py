"""
Loom Video Evaluator – Production‑Ready
- Robust JSON parsing
- HTML report download
- Clean UI with progress
"""

import streamlit as st
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
import base64

import requests
import plotly.graph_objects as go
import plotly.io as pio

# ─── Configuration ───────────────────────────────────────────────────────────

API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not API_KEY:
    st.error("Missing OPENROUTER_API_KEY environment variable. Please set it and restart.")
    st.stop()

MODEL = "openrouter/free"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
LOOM_PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ─── Rubric ──────────────────────────────────────────────────────────────────

RUBRIC = {
    "Alignment with LO": {
        "description": "What was your intended outcome - include how your activity links to an LO",
        "levels": {
            1: {"label": "Emerging", "description": "Mentions a goal, but doesn't explicitly name the LO as well as the desired outcome."},
            2: {"label": "Proficient", "description": "Clearly identifies the LO and desired outcome and how the activity relates to it."},
            3: {"label": "Outstanding", "description": "Demonstrates total alignment between the digital activity and the specific learning outcome."},
        },
    },
    "Tool Choice": {
        "description": "Why did you choose that particular tool?",
        "levels": {
            1: {"label": "Emerging", "description": "The reason for choosing the tool is not made completely clear."},
            2: {"label": "Proficient", "description": "Explains why the tool is a good match for the type of activity."},
            3: {"label": "Outstanding", "description": "Explains specific features or aspects of the tool that provide opportunities for students to engage and generate evidence that help you to assess if students have achieved the desired outcome."},
        },
    },
    "Activity Overview": {
        "description": "Give a quick overview of the activity?",
        "levels": {
            1: {"label": "Emerging", "description": "Gives a general overview of the activity from the point of view of the tool used."},
            2: {"label": "Proficient", "description": "Gives a step by step account of the activity which would allow someone else to adapt it for their context."},
            3: {"label": "Outstanding", "description": "Shares the student journey through the activity that could be replicated or adapted and includes what to be mindful or especially careful of from an instructor point of view."},
        },
    },
    "Reflection": {
        "description": "Reflect - why it worked / what you learned?",
        "levels": {
            1: {"label": "Emerging", "description": "Shares a general reflection but nothing actionable."},
            2: {"label": "Proficient", "description": "Shares what worked in relation to the LO/desired outcome and improvement suggestions where relevant."},
            3: {"label": "Outstanding", "description": "Shares exactly how the activity and chosen tool led to the desired outcome being achieved/partially achieved or not achieved and actionable insights that others can learn from."},
        },
    },
    "Transferability": {
        "description": "How easily could this activity be adapted to a different context?",
        "levels": {
            1: {"label": "Emerging", "description": "Not easy to see how the activity could be used or adapted to a different context."},
            2: {"label": "Proficient", "description": "It is easy to see how a faculty member in another field could adapt the activity for their context."},
            3: {"label": "Outstanding", "description": "It is explicitly mentioned how the activity could be scaled or modified for different class sizes/levels or topics."},
        },
    },
}

CRITERIA_NAMES = list(RUBRIC.keys())

# ─── Transcript Cleaning ──────────────────────────────────────────────────

def clean_transcript(text: str) -> str:
    filler = r'\b(um|uh|er|ah|like|you know|so|actually|basically|literally)\b'
    text = re.sub(filler, '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s([.,!?;:])', r'\1', text)
    text = re.sub(r'([.,!?;:])([^\s])', r'\1 \2', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()

# ─── Core Functions ─────────────────────────────────────────────────────────

def extract_video_id(loom_url: str) -> str:
    pattern = r"loom\.com/share/([a-f0-9-]+)"
    match = re.search(pattern, loom_url)
    if not match:
        raise ValueError("Invalid Loom URL")
    return match.group(1)

def fetch_transcript_url(loom_url: str):
    req = urllib.request.Request(loom_url, headers=LOOM_PAGE_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8")
    title_match = re.search(r"<title>([^<]+)</title>", html)
    video_title = title_match.group(1).strip() if title_match else "Unknown Title"
    video_title = re.sub(r"\s*\|\s*Loom$", "", video_title)
    transcript_url = ""
    captions_url = ""
    match = re.search(r'source_url":"(https://cdn\.loom\.com/mediametadata/transcription/[^"\\]+)', html)
    if match:
        transcript_url = urllib.parse.unquote(match.group(1))
    match = re.search(r'captions_source_url":"(https://cdn\.loom\.com/mediametadata/captions/[^"\\]+)', html)
    if match:
        captions_url = urllib.parse.unquote(match.group(1))
    if not transcript_url and not captions_url:
        raise RuntimeError("No transcript found.")
    return video_title, transcript_url, captions_url

def fetch_transcript_json(transcript_url: str) -> str:
    req = urllib.request.Request(transcript_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    phrases = data.get("phrases", [])
    if not phrases:
        raise RuntimeError("No phrases in transcript.")
    lines = []
    for p in phrases:
        ts = p.get("ts", 0)
        minutes = int(ts // 60)
        seconds = int(ts % 60)
        text = p.get("value", "").strip()
        if text:
            lines.append(f"[{minutes:02d}:{seconds:02d}] {text}")
    raw = "\n".join(lines)
    return clean_transcript(raw)

def fetch_transcript_vtt(captions_url: str) -> str:
    req = urllib.request.Request(captions_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        vtt_text = resp.read().decode("utf-8")
    lines = []
    in_cue = False
    for line in vtt_text.split("\n"):
        line = line.strip()
        if line and "-->" in line:
            in_cue = True
            continue
        if in_cue and line:
            lines.append(line)
        elif in_cue and not line:
            in_cue = False
    raw = "\n".join(lines)
    return clean_transcript(raw)

def build_evaluation_prompt(transcript: str, video_title: str) -> str:
    rubric_text = ""
    for criterion, details in RUBRIC.items():
        rubric_text += f"\n### {criterion}\n"
        rubric_text += f"Question: {details['description']}\n\n"
        for score, level_info in details["levels"].items():
            rubric_text += f"- **Score {score} ({level_info['label']})**: {level_info['description']}\n"
        rubric_text += "\n"
    prompt = f"""You are an expert evaluator for faculty digital activity submissions. You evaluate video submissions where faculty members explain a digital activity they have done in their online session.

## Video Being Evaluated
**Title**: {video_title}

## Transcript of the Video
Below is the full transcript (with timestamps) of the faculty member's explanation:

---
{transcript}
---

## Evaluation Rubric
{rubric_text}
## Your Task

Evaluate this submission against EACH of the 5 criteria above. For each criterion:

1. **Score**: Assign 1 (Emerging), 2 (Proficient), or 3 (Outstanding)
2. **Evidence**: Quote specific parts of the transcript that support your scoring
3. **Justification**: Explain in detail WHY you gave this score. Reference the rubric level descriptions explicitly.
4. **Gap Analysis**: If you did NOT give the maximum score, explain EXACTLY what was missing or what could be improved. Be specific about what the faculty member would need to add or change to reach the next level.
5. **Strengths**: Note what was done well even if the score is low

## Important Rules
- Base your evaluation ONLY on what is said in the transcript
- Be fair, constructive, and specific
- Quote the transcript directly when giving evidence
- For gap analysis, always reference the specific rubric description for the next higher level
- Do not inflate or deflate scores

## Output Format
Return your evaluation as a valid JSON object with this EXACT structure:
{{
  "video_title": "...",
  "total_score": <sum of all scores out of 15>,
  "criteria": [
    {{
      "criterion": "Alignment with LO",
      "score": <1 or 2 or 3>,
      "label": "<Emerging/Proficient/Outstanding>",
      "evidence": "<direct quotes from transcript>",
      "justification": "<detailed explanation>",
      "gap_analysis": "<what's missing to reach next level, or 'Maximum score achieved' if 3>",
      "strengths": "<what was done well>"
    }},
    ... (repeat for all 5 criteria)
  ],
  "overall_feedback": "<2-3 sentence summary of the submission's strengths and areas for improvement>"
}}

**IMPORTANT**: Your entire response must consist ONLY of the JSON object. Do not include any introductory sentences, explanations, or markdown formatting. Start with "{{" and end with "}}".
"""
    return prompt

# ─── Robust JSON Parser ──────────────────────────────────────────────────

def parse_evaluation_json(content: str) -> dict:
    """Attempt multiple strategies to extract and parse JSON."""
    # Remove markdown fences
    content = re.sub(r"```(?:json)?\s*", "", content)
    content = re.sub(r"```\s*", "", content)

    # Try to find the first '{' and then balance braces.
    # We'll also try to fix common issues like trailing commas.
    def try_parse(s):
        s = s.strip()
        # Remove trailing commas inside objects/arrays
        s = re.sub(r',\s*}', '}', s)
        s = re.sub(r',\s*]', ']', s)
        # Replace single quotes with double quotes for keys
        s = re.sub(r"(\w+):", r'"\1":', s)
        # Ensure keys are double-quoted
        # Already handled by previous, but we'll also try to fix unquoted keys
        s = re.sub(r'(\w+)(?=\s*:)', r'"\1"', s)
        try:
            return json.loads(s, strict=False)
        except json.JSONDecodeError:
            return None

    # Strategy 1: direct
    parsed = try_parse(content)
    if parsed is not None:
        return parsed

    # Strategy 2: extract substring from first '{' to matching '}'
    start = content.find('{')
    if start != -1:
        depth = 0
        end = -1
        for i, ch in enumerate(content[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            candidate = content[start:end+1]
            parsed = try_parse(candidate)
            if parsed is not None:
                return parsed

    # Strategy 3: regex fallback
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        candidate = match.group(0)
        parsed = try_parse(candidate)
        if parsed is not None:
            return parsed

    raise RuntimeError(f"Could not parse JSON. Preview: {content[:500]}\nFull length: {len(content)}")

def evaluate_with_openrouter(prompt: str) -> dict:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://your-app-url.com",
        "X-Title": "Loom Evaluator",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are an expert evaluator. Always output valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"API error {response.status_code}: {response.text[:500]}")
    result = response.json()
    content = result["choices"][0]["message"]["content"]
    return parse_evaluation_json(content)

# ─── Visualisation Functions ──────────────────────────────────────────────

def create_radar_chart(scores: dict):
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=list(scores.values()),
        theta=list(scores.keys()),
        fill='toself',
        line_color='#2E86C1',
        fillcolor='rgba(46, 134, 193, 0.3)'
    ))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(range=[0, 3], tickvals=[1,2,3], ticktext=['1','2','3'])
        ),
        showlegend=False,
        height=400,
        margin=dict(l=80, r=80, t=40, b=80)
    )
    return fig

def create_bar_chart(scores: dict):
    cats = list(scores.keys())
    vals = list(scores.values())
    colors = ['#27AE60' if v>=2.5 else '#F39C12' if v>=1.5 else '#E74C3C' for v in vals]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=vals, y=cats, orientation='h',
        marker_color=colors, text=vals, textposition='outside'
    ))
    fig.update_layout(
        xaxis=dict(range=[0,3.2], tickvals=[1,2,3], title='Score'),
        yaxis=dict(title=''),
        height=300,
        margin=dict(l=0, r=40, t=30, b=20),
        showlegend=False
    )
    return fig

# ─── Report Generation ────────────────────────────────────────────────────

def generate_html_report(evaluation: dict, scores_dict: dict, video_title: str, video_id: str) -> str:
    total = sum(scores_dict.values()) if scores_dict else 0
    avg = total/5 if scores_dict else 0

    # Create plotly figures as HTML
    radar_html = pio.to_html(create_radar_chart(scores_dict), include_plotlyjs='cdn', full_html=False)
    bar_html = pio.to_html(create_bar_chart(scores_dict), include_plotlyjs='cdn', full_html=False)

    criteria_html = ""
    for i, crit in enumerate(evaluation.get("criteria", []), 1):
        criteria_html += f"""
        <div style="margin-bottom: 2rem; border-bottom: 1px solid #ddd; padding-bottom: 1rem;">
            <h3 style="color: #2E86C1;">{i}. {crit.get('criterion')} – Score: {crit.get('score')}/3 ({crit.get('label')})</h3>
            <p><strong>Evidence:</strong><br>{crit.get('evidence', 'N/A')}</p>
            <p><strong>Justification:</strong><br>{crit.get('justification', 'N/A')}</p>
            <p><strong>Gap Analysis:</strong><br>{crit.get('gap_analysis', 'N/A')}</p>
            <p><strong>Strengths:</strong><br>{crit.get('strengths', 'N/A')}</p>
        </div>
        """

    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Loom Evaluation Report</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 900px; margin: 2rem auto; padding: 1rem; color: #2C3E50; }}
        h1 {{ color: #2E86C1; border-bottom: 2px solid #2E86C1; padding-bottom: 0.5rem; }}
        .summary {{ display: flex; gap: 2rem; background: #F8F9FA; padding: 1.5rem; border-radius: 8px; margin: 1.5rem 0; }}
        .metric {{ flex: 1; text-align: center; }}
        .metric-value {{ font-size: 2.2rem; font-weight: 700; color: #2E86C1; }}
        .metric-label {{ font-size: 0.9rem; color: #5D6D7E; }}
        .charts {{ display: flex; flex-wrap: wrap; gap: 1rem; margin: 2rem 0; }}
        .charts > div {{ flex: 1; min-width: 300px; }}
        .feedback {{ background: #E8F8F5; padding: 1rem; border-radius: 8px; margin: 1.5rem 0; border-left: 4px solid #1ABC9C; }}
        .footer {{ margin-top: 3rem; font-size: 0.8rem; color: #95A5A6; border-top: 1px solid #ddd; padding-top: 1rem; text-align: center; }}
    </style>
</head>
<body>
    <h1>📊 Loom Activity Evaluation Report</h1>
    <p><strong>Video Title:</strong> {video_title}</p>
    <p><strong>Evaluated on:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>

    <div class="summary">
        <div class="metric"><div class="metric-value">{total}/15</div><div class="metric-label">Total Score</div></div>
        <div class="metric"><div class="metric-value">{avg:.1f}/3</div><div class="metric-label">Average Score</div></div>
    </div>

    <div class="charts">
        <div>{radar_html}</div>
        <div>{bar_html}</div>
    </div>

    <h2>📝 Detailed Criteria</h2>
    {criteria_html}

    <div class="feedback">
        <h3 style="margin-top:0;">💬 Overall Feedback</h3>
        <p>{evaluation.get('overall_feedback', 'N/A')}</p>
    </div>

    <div class="footer">
        Generated by Loom Video Evaluator • Report ID: {video_id}
    </div>
</body>
</html>
"""
    return html

# ─── Streamlit UI ────────────────────────────────────────────────────────────

st.set_page_config(page_title="Loom Digital Activity Evaluator", layout="wide", page_icon="🎥")

st.markdown("""
<style>
    .main-header { font-size: 2.5rem; font-weight: 700; color: #2E86C1; margin-bottom: 0; }
    .sub-header { font-size: 1.1rem; color: #5D6D7E; margin-top: -5px; }
    .score-card { background: #F8F9FA; padding: 1.5rem; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    .stProgress > div > div { background-color: #2E86C1; }
    .stAlert { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-header">🎥 Loom Digital Activity Evaluator</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Paste a Loom share URL to evaluate against our rubric</p>', unsafe_allow_html=True)

# Session state for persistence
if "evaluation" not in st.session_state:
    st.session_state.evaluation = None
if "transcript" not in st.session_state:
    st.session_state.transcript = None
if "video_id" not in st.session_state:
    st.session_state.video_id = None
if "video_title" not in st.session_state:
    st.session_state.video_title = None

# ─── Input Form ─────────────────────────────────────────────────────────────

with st.container():
    with st.form("eval_form"):
        col1, col2 = st.columns([4, 1])
        with col1:
            loom_url = st.text_input("Loom Video URL", placeholder="https://www.loom.com/share/...", label_visibility="collapsed")
        with col2:
            submitted = st.form_submit_button("🚀 Evaluate", use_container_width=True)

# ─── Evaluation Flow ──────────────────────────────────────────────────────

if submitted and loom_url:
    # Reset previous
    st.session_state.evaluation = None
    st.session_state.transcript = None

    progress_bar = st.progress(0, text="Starting...")
    status_text = st.empty()

    with st.status("📡 Fetching video data...", expanded=True) as status:
        try:
            progress_bar.progress(10, text="Extracting video ID...")
            video_id = extract_video_id(loom_url)
            status.write(f"✅ Video ID: `{video_id}`")
        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Invalid URL: {e}")
            st.stop()

        try:
            progress_bar.progress(25, text="Downloading page...")
            video_title, transcript_url, captions_url = fetch_transcript_url(loom_url)
            status.write(f"📹 Title: **{video_title}**")
        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Page error: {e}")
            st.stop()

        try:
            progress_bar.progress(50, text="Fetching transcript...")
            if transcript_url:
                transcript = fetch_transcript_json(transcript_url)
            elif captions_url:
                transcript = fetch_transcript_vtt(captions_url)
            else:
                raise RuntimeError("No transcript source found.")
            status.write(f"📄 Transcript length: {len(transcript)} characters")
        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Transcript error: {e}")
            st.stop()

        progress_bar.progress(75, text="Building prompt...")
        prompt = build_evaluation_prompt(transcript, video_title)
        status.write("✅ Prompt built")
        progress_bar.progress(100, text="Done")
        elapsed = time.time() - start_time
        status.write(f"⏱️ Fetch completed in {elapsed:.1f}s")

    # AI Evaluation
    status_text.empty()
    st.markdown("---")
    st.subheader("🤖 AI Evaluation")

    ai_progress = st.progress(0, text="⏳ Connecting to AI...")
    ai_status = st.empty()
    start_eval = time.time()

    try:
        # Simple non‑streaming with progress simulation
        # We'll update progress based on elapsed time (assume ~60s)
        import threading
        stop = False
        def update_ai_progress():
            t = 0
            while not stop:
                time.sleep(0.5)
                t += 0.5
                pct = min(90, int((t / 60) * 100))
                ai_progress.progress(pct, text=f"⏳ Evaluating... {t:.0f}s elapsed")
                if t > 60:
                    ai_status.warning("Still working... This may take a moment.")
        thread = threading.Thread(target=update_ai_progress)
        thread.start()

        evaluation = evaluate_with_openrouter(prompt)

        stop = True
        thread.join(timeout=1)
        elapsed_eval = time.time() - start_eval
        ai_progress.progress(100, text=f"✅ Completed in {elapsed_eval:.1f}s")
        ai_status.success(f"✅ Evaluation finished in {elapsed_eval:.1f}s")

        # Store in session state
        st.session_state.evaluation = evaluation
        st.session_state.transcript = transcript
        st.session_state.video_id = video_id
        st.session_state.video_title = video_title

    except Exception as e:
        stop = True
        ai_progress.empty()
        st.error(f"Evaluation failed: {e}")
        if st.button("🔄 Retry Evaluation"):
            st.rerun()
        st.stop()

    # --- Display Results ---
    if st.session_state.evaluation:
        evaluation = st.session_state.evaluation
        transcript = st.session_state.transcript
        video_id = st.session_state.video_id
        video_title = st.session_state.video_title

        st.markdown("---")
        st.subheader(f"📊 Evaluation: {evaluation.get('video_title', video_title)}")

        scores_dict = {}
        for crit in evaluation.get("criteria", []):
            name = crit.get("criterion")
            score = crit.get("score", 0)
            if name in CRITERIA_NAMES:
                scores_dict[name] = score
        total_score = sum(scores_dict.values()) if scores_dict else 0

        col1, col2 = st.columns([2, 1])
        with col1:
            if scores_dict:
                fig_radar = create_radar_chart(scores_dict)
                st.plotly_chart(fig_radar, use_container_width=True)
        with col2:
            st.markdown('<div class="score-card">', unsafe_allow_html=True)
            st.metric("Total Score", f"{total_score}/15")
            st.metric("Average Score", f"{total_score/5:.1f}/3")
            st.markdown('</div>', unsafe_allow_html=True)
            if scores_dict:
                fig_bar = create_bar_chart(scores_dict)
                st.plotly_chart(fig_bar, use_container_width=True)

        st.markdown("### 📝 Detailed Criteria Evaluation")
        for i, crit in enumerate(evaluation.get("criteria", []), 1):
            with st.expander(f"{i}. {crit.get('criterion')} – Score: {crit.get('score')}/3 ({crit.get('label')})", expanded=(i==1)):
                st.markdown(f"**📌 Evidence:**\n{crit.get('evidence', 'N/A')}")
                st.markdown(f"**📖 Justification:**\n{crit.get('justification', 'N/A')}")
                st.markdown(f"**🔍 Gap Analysis:**\n{crit.get('gap_analysis', 'N/A')}")
                st.markdown(f"**💪 Strengths:**\n{crit.get('strengths', 'N/A')}")

        st.markdown("### 💬 Overall Feedback")
        st.success(evaluation.get("overall_feedback", "N/A"))

        # ─── Download Report Button ──────────────────────────────────────
        if scores_dict:
            html_report = generate_html_report(evaluation, scores_dict, video_title, video_id)
            st.download_button(
                label="📄 Download Neat Report (HTML)",
                data=html_report,
                file_name=f"evaluation_report_{video_id}.html",
                mime="text/html",
                use_container_width=True,
            )

        # Download JSON
        json_output = {
            "evaluated_at": datetime.now().isoformat(),
            "evaluation": evaluation,
            "transcript": transcript,
        }
        st.download_button(
            label="📥 Download Raw JSON",
            data=json.dumps(json_output, indent=2, ensure_ascii=False),
            file_name=f"evaluation_{video_id}.json",
            mime="application/json",
            use_container_width=True,
        )

        with st.expander("📄 Show Full Transcript"):
            st.text_area("Transcript", transcript, height=300)

        ai_progress.empty()
        ai_status.empty()

elif submitted and not loom_url:
    st.warning("Please enter a Loom URL.")

# If there is a previous evaluation in session state (e.g., after refresh)
elif st.session_state.evaluation is not None:
    # Re‑display results (same as above, but we can keep it simple)
    evaluation = st.session_state.evaluation
    transcript = st.session_state.transcript
    video_id = st.session_state.video_id
    video_title = st.session_state.video_title

    st.markdown("---")
    st.subheader(f"📊 Evaluation: {evaluation.get('video_title', video_title)}")

    scores_dict = {}
    for crit in evaluation.get("criteria", []):
        name = crit.get("criterion")
        score = crit.get("score", 0)
        if name in CRITERIA_NAMES:
            scores_dict[name] = score
    total_score = sum(scores_dict.values()) if scores_dict else 0

    col1, col2 = st.columns([2, 1])
    with col1:
        if scores_dict:
            fig_radar = create_radar_chart(scores_dict)
            st.plotly_chart(fig_radar, use_container_width=True)
    with col2:
        st.metric("Total Score", f"{total_score}/15")
        st.metric("Average Score", f"{total_score/5:.1f}/3")
        if scores_dict:
            fig_bar = create_bar_chart(scores_dict)
            st.plotly_chart(fig_bar, use_container_width=True)

    st.markdown("### 📝 Detailed Criteria Evaluation")
    for i, crit in enumerate(evaluation.get("criteria", []), 1):
        with st.expander(f"{i}. {crit.get('criterion')} – Score: {crit.get('score')}/3 ({crit.get('label')})", expanded=False):
            st.markdown(f"**Evidence:**\n{crit.get('evidence', 'N/A')}")
            st.markdown(f"**Justification:**\n{crit.get('justification', 'N/A')}")
            st.markdown(f"**Gap Analysis:**\n{crit.get('gap_analysis', 'N/A')}")
            st.markdown(f"**Strengths:**\n{crit.get('strengths', 'N/A')}")

    st.success(evaluation.get("overall_feedback", "N/A"))

    if scores_dict:
        html_report = generate_html_report(evaluation, scores_dict, video_title, video_id)
        st.download_button(
            label="📄 Download Neat Report (HTML)",
            data=html_report,
            file_name=f"evaluation_report_{video_id}.html",
            mime="text/html",
            use_container_width=True,
        )
