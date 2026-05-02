from flask import Flask, render_template, request, redirect, url_for, session
import pymysql
import os
import json
from dotenv import load_dotenv
from pdfminer.high_level import extract_text
import google.generativeai as genai

# Load environment variables from .env file
load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = "secret123"

# API key loaded securely from .env
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-flash-latest')

# Database connection
def get_db():
    return pymysql.connect(
        host="localhost",
        user="root",
        password="",  # XAMPP default
        database="ats_project",
        cursorclass=pymysql.cursors.DictCursor
    )

# DEFAULT PAGE → SIGNUP
@app.route('/', methods=['GET', 'POST'])
def signup():
    message = ""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cursor.fetchone()

        if user:
            message = "User already exists!"
        else:
            cursor.execute(
                "INSERT INTO users (username, password) VALUES (%s, %s)",
                (username, password)
            )
            conn.commit()
            conn.close()
            return redirect(url_for('login'))

        conn.close()

    return render_template('signup.html', message=message)


# LOGIN PAGE
@app.route('/login', methods=['GET', 'POST'])
def login():
    message = ""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM users WHERE username=%s AND password=%s",
            (username, password)
        )

        user = cursor.fetchone()
        conn.close()

        if user:
            session["user"] = username
            return redirect(url_for("dashboard"))
        else:
            message = "Invalid Credentials"

    return render_template('login.html', message=message)

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# DASHBOARD PAGE
@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if "user" not in session:
        return redirect(url_for('login'))

    if request.method == "POST":
        try:
            job_desc = request.form['job_desc']
            file = request.files.get('resume')

            if not file or file.filename == "":
                return "Please upload a resume"

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filepath)

            resume_text = extract_text(filepath)

            prompt = f"""
You are an ATS system.

Analyze the resume and job description below.

Return output in this exact format:

Match Percentage: <number>

Matched Skills:
- skill1
- skill2

Missing Skills:
- skill1
- skill2

Suggestions:
- suggestion1
- suggestion2

Resume:
{resume_text}

Job Description:
{job_desc}
"""

            response = model.generate_content(prompt)
            result = response.text

            score = 0
            matched = []
            missing = []
            suggestions = []

            lines = result.split("\n")
            current_section = None

            for line in lines:
                line = line.strip()

                if "Match Percentage" in line:
                    try:
                        score = int(''.join(filter(str.isdigit, line)))
                    except:
                        score = 0

                elif "Matched Skills" in line:
                    current_section = "matched"

                elif "Missing Skills" in line:
                    current_section = "missing"

                elif "Suggestions" in line:
                    current_section = "suggestions"

                elif line.startswith("-"):
                    item = line[1:].strip()

                    if current_section == "matched":
                        matched.append(item)
                    elif current_section == "missing":
                        missing.append(item)
                    elif current_section == "suggestions":
                        suggestions.append(item)

            return render_template(
                "result.html",
                score=score,
                matched=matched,
                missing=missing,
                suggestions=suggestions
            )

        except Exception as e:
            return f"Error: {e}"

    return render_template("dashboard.html")


@app.route('/generate-questions', methods=['POST'])
def generate_questions():
    if "user" not in session:
        return redirect(url_for('login'))

    try:
        file = request.files.get('resume')

        if not file or file.filename == "":
            return "Please upload a resume"

        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)

        resume_text = extract_text(filepath)
        resume_text = resume_text[:3000]

        prompt = f"""
You are a professional technical interviewer.

Based on the resume below, generate EXACTLY 5 interview questions.

IMPORTANT RULES:
- ONLY return questions
- DO NOT return headings or categories
- DO NOT include explanations
- Each question must be specific to the resume
- Keep questions practical and interview-ready

Format strictly like:
1. Question text?
2. Question text?
3. Question text?
4. Question text?
5. Question text?

Resume:
{resume_text}
"""

        response = model.generate_content(prompt)
        raw_text = response.text

        questions = []
        for line in raw_text.split("\n"):
            line = line.strip()
            if line.startswith(tuple(str(i) + "." for i in range(1, 6))):
                questions.append(line)

        if not questions:
            questions = ["Tell me about your project"]

        session["questions"] = questions

        return render_template("questions.html", questions=questions)

    except Exception as e:
        return f"Error: {e}"


@app.route("/ai-interview")
def ai_interview():
    if "user" not in session:
        return redirect(url_for("login"))

    prompt = """
Generate 3 simple HR interview questions.

Rules:
- Very basic
- General questions
- No numbering
- Return only the questions, one per line
"""

    response = model.generate_content(prompt)
    basic_q = [q.strip() for q in response.text.split("\n") if q.strip()]

    resume_q = session.get("questions", [])
    all_q = basic_q + resume_q

    session["all_questions"] = all_q

    return render_template("ai_interview.html")


@app.route("/evaluate-answer", methods=["POST"])
def evaluate_answer():
    data = request.get_json()

    answer = data.get("answer")
    face = data.get("face", {})

    stability = face.get("stability", 0)
    frames = face.get("frames", 1)
    face_score = round((stability / frames) * 5, 2)

    prompt = f"""
You are a CRITICAL Technical Recruiter at a Fortune 500 company. 
Evaluate this candidate's answer strictly. 

STRICT SCORING RULES:
1. If the answer is irrelevant, extremely short (e.g., "hi", "hello", "ok", "I don't know"), or evasive, you MUST give an Overall Score of 0 or 1.
2. Do not be polite. If the answer doesn't demonstrate technical knowledge for the question asked, it is a fail.
3. High effort but wrong technical content = 2-3/10.
4. Correct but brief = 5-6/10.
5. Expert level with examples = 9-10/10.

Answer:
{answer}

Facial Behavior:
- Stability Score: {face_score}

Give:
Overall Score: out of 10
Technical Accuracy: ...
Relevance: ...
Communication: ...
Suggestions: ...
"""

    response = model.generate_content(prompt)
    return {"result": response.text}


@app.route("/get-all-questions")
def get_questions():
    return {"questions": session.get("all_questions", [])}


# ─────────────────────────────────────────────────────────────
@app.route("/final-evaluation", methods=["POST"])
def final_evaluation():
    data = request.get_json()

    answers        = data.get("answers", [])
    face           = data.get("face", {})
    cheating       = data.get("cheating", {})
    questions_list = session.get("all_questions", [])

    # Biometric Analytics
    frames     = face.get("frames", 1)
    stability  = round((face.get("stability", 0) / frames) * 10, 2)
    blink_rate = face.get("blinkCount", 0)
    smile      = round((face.get("smileScore", 0) / frames) * 10, 2)
    articulation = round((face.get("mouthOpening", 0) / frames) * 100, 2)
    
    no_face      = cheating.get("noFace", 0)
    looking_away = cheating.get("lookingAway", 0)
    reading      = cheating.get("readingDetection", 0)

    # Build Q&A string for the prompt
    qa_pairs = ""
    for i, ans in enumerate(answers):
        q = questions_list[i] if i < len(questions_list) else f"Question {i+1}"
        qa_pairs += f"Q{i+1}: {q}\nA{i+1}: {ans}\n\n"

    prompt = f"""
You are a SENIOR TECHNICAL RECRUITER and BIOMETRIC ANALYST. 
Evaluate this candidate's mock interview results using their answers and facial behavior data.

BIOMETRIC DATA INTERPRETATION:
- Stability: {stability}/10 (Higher is more professional/calm)
- Blink Count: {blink_rate} (Normal is 15-20 per minute. High indicates anxiety. Low indicates script reading.)
- Smile/Engagement: {smile}/10 (Shows personality and confidence)
- Mouth Articulation: {articulation} (Confirms active speaking vs mumbling)
- Reading Detection Flags: {reading} (High flags suggest candidate was reading from a script)
- Looking Away Flags: {looking_away}
- No Face Flags: {no_face}

STRICT EVALUATION CRITERIA:
1. TRUTHFULNESS: If "Reading Detection" is high (>20% of frames), penalize the "conf" and "overall_score" metrics significantly.
2. CONFIDENCE: Use Blink Count and Stability. High blinks + low stability = Low confidence.
3. ENGAGEMENT: Use Smile and Articulation scores.
4. ANSWER QUALITY: As before, Irrelevant/Short answers = 0 or 1.

Q&A:
{qa_pairs}

Return ONLY valid JSON.

Return exactly this JSON structure:
{{
  "overall_score": <0-10>,
  "final_verdict": "<Excellent | Good | Average | Needs Improvement | Failed>",
  "metrics": {{
    "tech": <0-10>,
    "comm": <0-10>,
    "conf": <0-10>
  }},
  "behavioral_analysis": {{
    "observations": "<Strict 2-3 sentence summary incorporating biometric findings like reading detection or anxiety.>",
    "cheating_risk": "<Low | Medium | High>"
  }},
  "qa_analysis": [
    {{ "question": "...", "answer": "...", "score": <0-10> }}
  ],
  "suggestions": [
    "<suggestion 1>",
    "<suggestion 2>",
    "<suggestion 3>"
  ]
}}
"""

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()

        # Strip markdown fences if Gemini adds them
        if raw.startswith("```"):
            parts = raw.split("```")
            # parts[1] is the block content (may start with "json\n")
            raw = parts[1]
            if raw.lower().startswith("json"):
                raw = raw[4:]

        result_data = json.loads(raw.strip())

    except Exception as e:
        print(f"Final evaluation parse error: {e}")
        result_data = {
            "overall_score": 5,
            "final_verdict": "Average",
            "metrics": {
                "tech": 5,
                "comm": 5,
                "conf": int(face_score)
            },
            "behavioral_analysis": {
                "observations": "Automated analysis could not be parsed. Please retry the interview.",
                "cheating_risk": "Low" if cheating_total < 10 else "High"
            },
            "qa_analysis": [
                {
                    "question": questions_list[i] if i < len(questions_list) else f"Question {i+1}",
                    "answer": ans,
                    "score": 5
                }
                for i, ans in enumerate(answers)
            ],
            "suggestions": [
                "Review your answers carefully.",
                "Practice speaking clearly and confidently.",
                "Maintain eye contact with the camera."
            ]
        }

    return result_data


# ─────────────────────────────────────────────────────────────
# SAVE REPORT TO DB  –  uses correct table name: interview_scores
# ─────────────────────────────────────────────────────────────
@app.route("/show-final-report", methods=["POST"])
def show_final_report():
    if "user" not in session:
        return {"status": "error", "message": "User not logged in"}, 401

    data     = request.get_json()
    username = session["user"]

    try:
        conn   = get_db()
        cursor = conn.cursor()

        result_json = json.dumps(data)

        # Table name matches what you created in phpMyAdmin: interview_scores
        cursor.execute(
            "INSERT INTO interview_scores (username, result_json) VALUES (%s, %s)",
            (username, result_json)
        )
        conn.commit()
        conn.close()

        return {"status": "ok"}

    except Exception as e:
        print(f"Error saving report: {e}")
        return {"status": "error", "message": str(e)}, 500


# ─────────────────────────────────────────────────────────────
# SHOW FINAL REPORT PAGE
# ─────────────────────────────────────────────────────────────
@app.route("/final-report")
def final_report():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]

    try:
        conn   = get_db()
        cursor = conn.cursor()

        # Table name matches what you created in phpMyAdmin: interview_scores
        cursor.execute(
            "SELECT result_json FROM interview_scores WHERE username=%s ORDER BY id DESC LIMIT 1",
            (username,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            result_data = json.loads(row["result_json"])
            return render_template("final_report.html", result=result_data)
        else:
            return "No interview results found. Please complete an interview first."

    except Exception as e:
        return f"Error retrieving report: {e}"


@app.route('/logout')
def logout():
    session.pop("user", None)
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)