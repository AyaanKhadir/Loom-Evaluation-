"""
Loom Video Evaluator – Professional Streamlit App

Features:
- Visual dashboard with radar & bar charts
- Progress & time estimates
- Streaming AI responses
- Transcript cleaning
- Clean, modern UI
"""

import streamlit as st
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

import requests
import plotly.graph_objects as go

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
    """Clean transcript text: remove filler words, extra spaces, and fix punctuation."""
    # Remove filler words (simple list)
    filler_words = r'\b(um|uh|er|ah|like|you know|so|actually|basically|literally)\b'
    text = re.sub(filler_words, '', text, flags=re.IGNORECASE)
    # Remove multiple spaces
    text = re.sub(r'\s+', ' ', text)
    # Remove spaces before punctuation
    text = re.sub(r'\s([.,!?;:])', r'\1', text)
    # Ensure space after punctuation
    text = re.sub(r'([.,!?;:])([^\s])', r'\1 \2', text)
    # Remove extra newlines
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()

# ─── Core Functions ─────────────────────────────────────────────────────────

def extract_video_id(loom_url: str) -> str:
    pattern = r"loom\.com/share/([a-f0-9-]+)"
    match = re.search(pattern, loom_url)
    if not match:
        raise ValueError(f"Invalid Loom URL: {loom_url}")
    return match.group(1)

def fetch_transcript_url(loom_url: str):
    req = urllib.request.Request(loom_url, headers=LOOM_PAGE_HEADERS)
    resp = urllib.request.urlopen(req, timeout=30)
    html = resp.read().decode("utf-8")
    title_match = re.search(r"<title>([^<]+)</title>", html)
    video_title = title_match.group(1).strip() if title_match else "Unknown Title"
    video_title = re.sub(r"\s*\|\s*Loom$", "", video_title)
    transcript_url = ""
    transcript_match = re.search(
        r'source_url":"(https://cdn\.loom\.com/mediametadata/transcription/[^"\\]+)',
        html,
    )
    if transcript_match:
        transcript_url = urllib.parse.unquote(transcript_match.group(1))
    captions_url = ""
    captions_match = re.search(
        r'captions_source_url":"(https://cdn\.loom\.com/mediametadata/captions/[^"\\]+)',
        html,
    )
    if captions_match:
        captions_url = urllib.parse.unquote(captions_match.group(1))
    if not transcript_url and not captions_url:
        raise RuntimeError("No transcript or captions URL found.")
    return video_title, transcript_url, captions_url

def fetch_transcript_json(transcript_url: str) -> str:
    req = urllib.request.Request(transcript_url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read().decode("utf-8"))
    phrases = data.get("phrases", [])
    if not phrases:
        raise RuntimeError("Transcript JSON has no 'phrases' data.")
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
    resp = urllib.request.urlopen(req, timeout=30)
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

def parse_evaluation_json(content: str) -> dict:
    """Robust JSON extraction."""
    content = re.sub(r"```(?:json)?\s*", "", content)
    content = re.sub(r"```\s*", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Find first '{' and matching '}'
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
            try:
                return json.loads(content[start:end+1])
            except json.JSONDecodeError:
                pass
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise RuntimeError(f"Could not parse JSON. Preview: {content[:500]}")

def evaluate_with_openrouter_streaming(prompt: str):
    """Stream the response from OpenRouter and parse final JSON."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://your-app-url.com",
        "X-Title": "Loom Evaluator",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are an expert evaluator… Always output valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
        "stream": True,
    }
    response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, stream=True, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"API error {response.status_code}: {response.text[:500]}")
    full_content = ""
    # Placeholder for streaming display
    stream_placeholder = st.empty()
    raw_text = ""
    for chunk in response.iter_lines():
        if chunk:
            chunk_str = chunk.decode("utf-8")
            if chunk_str.startswith("data: "):
                data_str = chunk_str[6:]  # remove "data: "
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        raw_text += content
                        # Update streaming display
                        stream_placeholder.text_area("🔄 AI Response (streaming)", raw_text, height=200)
                except json.JSONDecodeError:
                    pass
    stream_placeholder.empty()
    # Parse final JSON
    return parse_evaluation_json(raw_text)

# ─── Visualisation Functions ──────────────────────────────────────────────

def create_radar_chart(scores: dict, title: str = "Score Dashboard"):
    """Create a radar chart of criterion scores."""
    categories = list(scores.keys())
    values = list(scores.values())
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values,
        theta=categories,
        fill='toself',
        name='Scores',
        line_color='#2E86C1',
        fillcolor='rgba(46, 134, 193, 0.3)'
    ))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 3],
                tickvals=[1, 2, 3],
                ticktext=['1 (Emerging)', '2 (Proficient)', '3 (Outstanding)']
            ),
        ),
        showlegend=False,
        title=title,
        height=400,
        margin=dict(l=80, r=80, t=60, b=80)
    )
    return fig

def create_bar_chart(scores: dict, title: str = "Scores per Criterion"):
    """Create a horizontal bar chart."""
    categories = list(scores.keys())
    values = list(scores.values())
    colors = ['#27AE60' if v >= 2.5 else '#F39C12' if v >= 1.5 else '#E74C3C' for v in values]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=values,
        y=categories,
        orientation='h',
        marker_color=colors,
        text=values,
        textposition='outside',
        hovertemplate='%{y}: %{x}/3<extra></extra>'
    ))
    fig.update_layout(
        xaxis=dict(range=[0, 3.2], tickvals=[1, 2, 3], title='Score'),
        yaxis=dict(title=''),
        height=300,
        margin=dict(l=0, r=40, t=30, b=20),
        showlegend=False
    )
    return fig

# ─── Streamlit UI ────────────────────────────────────────────────────────────

st.set_page_config(page_title="Loom Digital Activity Evaluator", layout="wide", page_icon="🎥")

# Custom CSS for professional look
st.markdown("""
<style>
    .main-header { font-size: 2.5rem; font-weight: 700; color: #2E86C1; margin-bottom: 0; }
    .sub-header { font-size: 1.1rem; color: #5D6D7E; margin-top: -5px; }
    .score-card { background: #F8F9FA; padding: 1.5rem; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    .metric-label { font-weight: 600; color: #2C3E50; }
    .metric-value { font-size: 2rem; font-weight: 700; color: #2E86C1; }
    .divider { border-top: 2px solid #E5E7EB; margin: 1.5rem 0; }
    .stProgress > div > div { background-color: #2E86C1; }
    .stSpinner > div { border-color: #2E86C1 !important; }
    .stAlert { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ─── Header ──────────────────────────────────────────────────────────────────

st.markdown('<p class="main-header">🎥 Loom Digital Activity Evaluator</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Paste a Loom share URL to evaluate against our rubric</p>', unsafe_allow_html=True)

# ─── Input Form ─────────────────────────────────────────────────────────────

with st.container():
    with st.form("eval_form", clear_on_submit=False):
        col1, col2 = st.columns([4, 1])
        with col1:
            loom_url = st.text_input("Loom Video URL", placeholder="https://www.loom.com/share/...", label_visibility="collapsed")
        with col2:
            submitted = st.form_submit_button("🚀 Evaluate", use_container_width=True)

# ─── Evaluation Flow ──────────────────────────────────────────────────────

if submitted and loom_url:
    # --- Step 1: Fetch transcript with progress ---
    status_placeholder = st.empty()
    progress_bar = st.progress(0, text="Starting...")
    start_time = time.time()

    with st.status("📡 Fetching video data...", expanded=True) as status:
        progress_bar.progress(10, text="Extracting video ID...")
        try:
            video_id = extract_video_id(loom_url)
            status.write(f"✅ Video ID: `{video_id}`")
        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Invalid URL: {e}")
            st.stop()

        progress_bar.progress(25, text="Downloading page...")
        try:
            video_title, transcript_url, captions_url = fetch_transcript_url(loom_url)
            status.write(f"📹 Title: **{video_title}**")
        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Failed to fetch page: {e}")
            st.stop()

        progress_bar.progress(50, text="Fetching transcript...")
        try:
            if transcript_url:
                transcript = fetch_transcript_json(transcript_url)
            elif captions_url:
                transcript = fetch_transcript_vtt(captions_url)
            else:
                raise RuntimeError("No transcript source found.")
            status.write(f"📄 Transcript length: {len(transcript)} characters")
            # Show a preview
            with st.expander("📝 Preview transcript"):
                st.text(transcript[:500] + "..." if len(transcript) > 500 else transcript)
        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Transcript error: {e}")
            st.stop()

        progress_bar.progress(75, text="Building prompt...")
        prompt = build_evaluation_prompt(transcript, video_title)
        status.write("✅ Prompt ready")
        progress_bar.progress(100, text="Done")
        elapsed = time.time() - start_time
        status.write(f"⏱️ Transcript fetch completed in {elapsed:.1f}s")

    # --- Step 2: AI Evaluation with streaming ---
    status_placeholder.empty()
    st.markdown("---")
    st.subheader("🤖 AI Evaluation")

    # Show time estimate
    st.info("⏳ Evaluation may take 30–90 seconds. The response will stream below.")

    # Create a progress bar for the AI call (simulated time)
    ai_progress = st.progress(0, text="Connecting to AI...")
    ai_status = st.empty()

    # We'll use a status container for streaming
    stream_container = st.container()

    try:
        # Start the streaming evaluation
        start_eval = time.time()
        # We'll use a custom function that updates progress based on chunks
        # Since we don't know chunk count, we'll use a timer-based progress
        import threading
        stop_progress = False

        def update_progress():
            elapsed = 0
            while not stop_progress:
                time.sleep(0.5)
                elapsed += 0.5
                # Estimate 60 seconds total
                pct = min(90, int((elapsed / 60) * 100))
                ai_progress.progress(pct, text=f"⏳ Evaluating... {elapsed:.0f}s elapsed")
                if elapsed > 60:
                    # show that it's taking longer
                    ai_status.warning("⏳ Still working... This may take a moment.")
            ai_progress.progress(100, text="✅ Done")

        # Start progress thread
        thread = threading.Thread(target=update_progress)
        thread.start()

        # Perform streaming call
        evaluation = evaluate_with_openrouter_streaming(prompt)

        # Stop progress
        stop_progress = True
        thread.join(timeout=1)
        ai_progress.progress(100, text="✅ Evaluation complete")
        elapsed_eval = time.time() - start_eval
        ai_status.success(f"✅ Evaluation completed in {elapsed_eval:.1f}s")

    except Exception as e:
        stop_progress = True
        ai_progress.empty()
        st.error(f"Evaluation failed: {e}")
        st.stop()

    # --- Step 3: Display results ---
    st.markdown("---")
    st.subheader(f"📊 Evaluation Results: {evaluation.get('video_title', video_title)}")

    # Extract scores
    scores_dict = {}
    for crit in evaluation.get("criteria", []):
        name = crit.get("criterion")
        score = crit.get("score", 0)
        if name in CRITERIA_NAMES:
            scores_dict[name] = score

    total_score = sum(scores_dict.values()) if scores_dict else 0

    # Dashboard layout
    col1, col2 = st.columns([2, 1])

    with col1:
        # Radar chart
        if scores_dict:
            fig_radar = create_radar_chart(scores_dict)
            st.plotly_chart(fig_radar, use_container_width=True)

    with col2:
        # Metric cards
        st.markdown('<div class="score-card">', unsafe_allow_html=True)
        st.metric("Total Score", f"{total_score}/15")
        st.metric("Average Score", f"{total_score/5:.1f}/3")
        st.markdown('</div>', unsafe_allow_html=True)

        # Bar chart
        if scores_dict:
            fig_bar = create_bar_chart(scores_dict)
            st.plotly_chart(fig_bar, use_container_width=True)

    # Detailed criteria expanders
    st.markdown("### 📝 Detailed Criteria Evaluation")
    for i, crit in enumerate(evaluation.get("criteria", []), 1):
        with st.expander(f"{i}. {crit.get('criterion')} – Score: {crit.get('score')}/3 ({crit.get('label')})", expanded=(i==1)):
            st.markdown(f"**📌 Evidence:**\n{crit.get('evidence', 'N/A')}")
            st.markdown(f"**📖 Justification:**\n{crit.get('justification', 'N/A')}")
            st.markdown(f"**🔍 Gap Analysis:**\n{crit.get('gap_analysis', 'N/A')}")
            st.markdown(f"**💪 Strengths:**\n{crit.get('strengths', 'N/A')}")

    # Overall feedback
    st.markdown("### 💬 Overall Feedback")
    st.success(evaluation.get("overall_feedback", "N/A"))

    # Download button
    json_output = {
        "evaluated_at": datetime.now().isoformat(),
        "evaluation": evaluation,
        "transcript": transcript,
        "cleaned": True,
    }
    st.download_button(
        label="📥 Download Full Results (JSON)",
        data=json.dumps(json_output, indent=2, ensure_ascii=False),
        file_name=f"evaluation_{video_id}.json",
        mime="application/json",
        use_container_width=True,
    )

    # Optional transcript viewer
    with st.expander("📄 Show Full Transcript"):
        st.text_area("Transcript", transcript, height=300)

    # Clean up progress
    ai_progress.empty()
    ai_status.empty()

elif submitted and not loom_url:
    st.warning("Please enter a Loom URL.")