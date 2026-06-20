from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from pdfminer.high_level import extract_text
import docx
from openai import OpenAI
from fpdf import FPDF
from flask import send_file
import io
# Load environment variables from .env file
load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = "secret123"

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DB_FILE = "database.db"

# Custom dictionary factory for SQLite
def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

# Cursor wrapper to transparently convert SQLite '?' to PostgreSQL '%s'
class PostgreSQLCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, params=None):
        if params is None:
            params = ()
        # Convert SQLite '?' placeholders to PostgreSQL '%s'
        query = query.replace('?', '%s')
        return self.cursor.execute(query, params)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def close(self):
        self.cursor.close()

    def __getattr__(self, name):
        return getattr(self.cursor, name)

# Connection wrapper to ensure .cursor() returns the wrapped cursor
class PostgreSQLConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn

    def cursor(self, *args, **kwargs):
        if 'cursor_factory' not in kwargs:
            from psycopg2.extras import RealDictCursor
            kwargs['cursor_factory'] = RealDictCursor
        cursor = self.conn.cursor(*args, **kwargs)
        return PostgreSQLCursorWrapper(cursor)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)

def get_db():
    if DATABASE_URL:
        # PostgreSQL (Render)
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        return PostgreSQLConnectionWrapper(conn)
    else:
        # SQLite (Local)
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = dict_factory
        return conn

def get_cursor(conn):
    if DATABASE_URL:
        from psycopg2.extras import RealDictCursor
        return conn.cursor(cursor_factory=RealDictCursor)
    else:
        return conn.cursor()

def execute_query(cursor, query, params=None):
    if params is None:
        params = ()
    if DATABASE_URL:
        # Convert SQLite '?' placeholders to PostgreSQL '%s'
        query = query.replace('?', '%s')
    cursor.execute(query, params)
    return cursor

def init_db():
    conn = get_db()
    cursor = get_cursor(conn)
    
    # Tables with syntax compatible for both or handled via replacement
    # Using SERIAL for Postgres and AUTOINCREMENT for SQLite
    
    users_table = """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """ if DATABASE_URL else """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """
    
    eval_table = """
        CREATE TABLE IF NOT EXISTS resume_evaluations (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            score INTEGER,
            matched_skills TEXT,
            missing_skills TEXT,
            suggestions TEXT,
            full_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """ if DATABASE_URL else """
        CREATE TABLE IF NOT EXISTS resume_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            score INTEGER,
            matched_skills TEXT,
            missing_skills TEXT,
            suggestions TEXT,
            full_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """

    interview_table = """
        CREATE TABLE IF NOT EXISTS interview_scores (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            result_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """ if DATABASE_URL else """
        CREATE TABLE IF NOT EXISTS interview_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            result_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """

    cursor.execute(users_table)
    cursor.execute(eval_table)
    cursor.execute(interview_table)
    
    conn.commit()
    conn.close()

# Initialize DB on start
init_db()

# OpenRouter Client Configuration
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

# AI Models Priority List (High Consistency)
MODELS = [
    "meta-llama/llama-3.1-8b-instruct",      # High consistency, currently working
    "meta-llama/llama-3-8b-instruct",        # Very similar output
    "mistralai/mistral-7b-instruct",         # Reliable alternative
    "google/gemma-2-9b-it",                  # Strong logic
    "openrouter/auto"                        # Absolute last resort
]

# Helper function for stable AI completions with smart fallback logic
def get_ai_completion(prompt, temperature=0.0):
    last_error = None
    
    for model in MODELS:
        try:
            print(f"[*] Trying model: {model}...")
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                timeout=30  # Add timeout to prevent hanging
            )
            print(f"[+] Success with {model}")
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            print(f"[!] {model} failed: {e}")
            # Continue to next model
            continue
            
    # If all models fail
    print("[-] ALL MODELS FAILED. Final error:", last_error)
    return f"AI Error: All models are currently unresponsive. Please try again later. Details: {last_error}"

# Helper function to extract text from PDF or DOCX
def extract_text_from_file(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.pdf':
        return extract_text(filepath)
    elif ext == '.docx':
        doc = docx.Document(filepath)
        return "\n".join([para.text for para in doc.paragraphs])
    else:
        return ""

# DEFAULT PAGE → SIGNUP
@app.route('/', methods=['GET', 'POST'])
def signup():
    message = ""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cursor.fetchone()

        if user:
            message = "User already exists!"
        else:
            hashed_pw = generate_password_hash(password)
            cursor.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, hashed_pw)
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
            "SELECT * FROM users WHERE username=?",
            (username,)
        )

        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session["user"] = username
            return redirect(url_for("dashboard"))
        else:
            message = "Invalid Credentials"

    return render_template('login.html', message=message)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

import base64

VISION_MODELS = [
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.2-11b-vision-instruct",
    "google/gemini-1.5-flash",
    "openrouter/auto"
]

@app.route('/extract-jd-photo', methods=['POST'])
def extract_jd_photo():
    if "user" not in session:
        return {"error": "Unauthorized"}, 401

    file = request.files.get('jd_photo')
    if not file or file.filename == "":
        return {"error": "No file uploaded"}, 400

    try:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp']:
            return {"error": "Unsupported image format. Please upload PNG, JPG, JPEG, or WEBP."}, 400

        mime_type = "image/png"
        if ext in ['.jpg', '.jpeg']:
            mime_type = "image/jpeg"
        elif ext == '.webp':
            mime_type = "image/webp"

        image_bytes = file.read()
        base64_data = base64.b64encode(image_bytes).decode('utf-8')

        prompt = """
You are an expert OCR and Document Processing system.
Analyze the provided image of a job description.
Extract ONLY the text that represents the Job Description itself (including job titles, duties, responsibilities, requirements, skills, qualifications, and background information about the role).
Do NOT include website navigation, ads, headers, footers, page numbers, or unrelated sidebars.
Strictly return ONLY the extracted job description text. Do not write any conversational intro or outro text (e.g., "Here is the text:").
"""

        extracted_text = None
        last_error = None

        for model in VISION_MODELS:
            try:
                print(f"[*] Extracting JD from photo using model: {model}...")
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": prompt
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{base64_data}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=2000,
                    temperature=0.0,
                    timeout=35
                )
                extracted_text = response.choices[0].message.content
                print(f"[+] OCR extraction success with {model}")
                break
            except Exception as e:
                print(f"[!] OCR model {model} failed: {e}")
                last_error = e
                continue

        if not extracted_text:
            return {"error": f"Failed to extract text from photo. Details: {last_error}"}, 500

        return {"text": extracted_text.strip()}

    except Exception as e:
        print(f"OCR Endpoint Error: {e}")
        return {"error": f"Error processing image: {e}"}, 500


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
                flash("Please upload a resume")
                return redirect(url_for('dashboard'))

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filepath)

            resume_text = extract_text_from_file(filepath)

            prompt = f"""
You are an expert Technical Recruiter and ATS Optimization Specialist.

Analyze the provided Resume against the Job Description.

CRITICAL PRECISION RULES FOR SKILL MATCHING AND GAP ANALYSIS:
1. TECHNICAL SKILLS MATCHED: List ONLY technical skills (CAD software, programming languages, engineering methodologies, tools) that are EXPLICITLY mentioned in BOTH the Resume AND the Job Description.
2. SOFT SKILLS MATCHED: List ONLY professional/soft skills EXPLICITLY found in the Resume.
3. MISSING SKILLS (GAP ANALYSIS):
   - First, list all required technical skills, software, and methods explicitly mentioned in the Job Description.
   - Second, list all skills/software mentioned in the Resume (check all sections including Skills, Education, Technical Courses, and Work Experience).
   - Third, identify which skills required by the Job Description are ABSENT from the Resume.
   - DO NOT list skills that the candidate has (like SOLIDWORKS, PTC Creo Parametric, Electric Vehicles Basics, Vehicle Powertrain, etc. which are explicitly listed in their Resume) as missing skills.
   - DO NOT list skills that are NOT mentioned in the Job Description as missing skills. The "missing_skills" list must only contain requirements from the Job Description that the candidate does not have.
4. STRATEGIC ADVICE: Provide 3-5 highly specific, actionable suggestions. Recommend how the candidate can highlight their matching skills, or address actual gaps. Do not suggest learning software they already know.
5. NO HALLUCINATIONS: Do not assume the candidate has a skill unless it is written.

Format strictly as JSON:
{{
  "match_percentage": <number>,
  "technical_skills_matched": ["skill", "skill"],
  "soft_skills_matched": ["skill", "skill"],
  "missing_skills": ["skill", "skill"],
  "strategic_advice": ["suggestion 1", "suggestion 2"]
}}

Resume Content:
{resume_text}

Job Description:
{job_desc}
"""

            result = get_ai_completion(prompt)

            try:
                # Robust JSON extraction
                import re
                match = re.search(r'\{.*\}', result, re.DOTALL)
                if match:
                    result = match.group(0)
                
                data = json.loads(result)
                score = data.get("match_percentage", 0)
                matched_tech = data.get("technical_skills_matched", [])
                matched_soft = data.get("soft_skills_matched", [])
                missing = data.get("missing_skills", [])
                suggestions = data.get("strategic_advice", [])
                
                # Format suggestions (Bold to strong)
                suggestions = [re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', str(sug)) for sug in suggestions]

            except Exception as parse_err:
                print(f"JSON Parsing Error: {parse_err}")
                score = 0
                matched_tech = []
                matched_soft = []
                missing = []
                suggestions = ["AI Error: Failed to parse evaluation data. Please try again."]

            # Save to history
            try:
                full_data_dict = {
                    "score": score,
                    "matched_tech": matched_tech,
                    "matched_soft": matched_soft,
                    "missing": missing,
                    "suggestions": suggestions,
                    "resume_text": resume_text,
                    "job_desc": job_desc
                }
                full_data_json = json.dumps(full_data_dict)

                conn = get_db()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO resume_evaluations (username, score, matched_skills, missing_skills, suggestions, full_data) VALUES (?, ?, ?, ?, ?, ?)",
                    (session['user'], score, "\n".join(matched_tech + matched_soft), "\n".join(missing), "\n".join(suggestions), full_data_json)
                )
                conn.commit()
                conn.close()
            except Exception as db_err:
                print(f"Database error: {db_err}")

            return render_template(
                "result.html",
                score=score,
                matched_tech=matched_tech,
                matched_soft=matched_soft,
                missing=missing,
                suggestions=suggestions,
                resume_text=resume_text,
                job_desc=job_desc
            )

        except Exception as e:
            return f"Error: {e}"

    # Fetch history for the GET request
    history = []
    interview_history = []
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Resume Analysis History
        cursor.execute(
            "SELECT id, score, created_at FROM resume_evaluations WHERE username=? ORDER BY created_at DESC LIMIT 5",
            (session['user'],)
        )
        history = list(cursor.fetchall())
        for row in history:
            if isinstance(row['created_at'], str):
                try:
                    row['created_at'] = datetime.strptime(row['created_at'], '%Y-%m-%d %H:%M:%S')
                except:
                    pass
        
        # Interview History
        cursor.execute(
            "SELECT id, result_json, created_at FROM interview_scores WHERE username=? ORDER BY created_at DESC LIMIT 5",
            (session['user'],)
        )
        raw_interviews = cursor.fetchall()
        for row in raw_interviews:
            data = json.loads(row['result_json'])
            created_at = row['created_at']
            if isinstance(created_at, str):
                try:
                    created_at = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                except:
                    pass
            interview_history.append({
                "id": row['id'],
                "score": data.get('overall_score', 0),
                "verdict": data.get('final_verdict', 'N/A'),
                "date": created_at
            })
            
        conn.close()

        # Prepare Chart Data
        chart_data = {
            "resume_labels": [row['created_at'].strftime('%b %d') if hasattr(row['created_at'], 'strftime') else str(row['created_at']) for row in reversed(history)],
            "resume_scores": [row['score'] for row in reversed(history)],
            "interview_labels": [row['date'].strftime('%b %d') if hasattr(row['date'], 'strftime') else str(row['date']) for row in reversed(interview_history)],
            "interview_scores": [row['score'] for row in reversed(interview_history)]
        }

    except Exception as e:
        print(f"Error fetching history: {e}")
        chart_data = {"resume_labels": [], "resume_scores": [], "interview_labels": [], "interview_scores": []}

    return render_template("dashboard.html", history=history, interview_history=interview_history, chart_data=json.dumps(chart_data))


@app.route('/generate-questions', methods=['POST'])
def generate_questions():
    if "user" not in session:
        return redirect(url_for('login'))

    try:
        resume_text = ""
        file = request.files.get('resume')

        if file and file.filename != "":
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filepath)
            resume_text = extract_text_from_file(filepath)
        else:
            # Check if text was passed directly (from result page)
            resume_text = request.form.get('resume_text', "")

        if not resume_text:
            flash("Please upload a resume or provide resume text")
            return redirect(url_for('dashboard'))

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

        raw_text = get_ai_completion(prompt)

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

    raw_output = get_ai_completion(prompt)
    basic_q = [q.strip() for q in raw_output.split("\n") if q.strip()]

    resume_q = session.get("questions", [])
    all_q = basic_q + resume_q

    session["all_questions"] = all_q
    session["interview_start_time"] = datetime.utcnow().timestamp()

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
You are a HIGHLY CRITICAL Senior Technical Interviewer at a Tier-1 Tech Company (Google/Meta).
Evaluate this candidate's answer with extreme strictness. 

SCORING RUBRIC (STRICT):
- 0/10: Irrelevant, extremely short (1-5 words), evasive, or "I don't know" style answers.
- 1-2/10: Answers that show total lack of understanding or are logically incorrect.
- 3-4/10: High effort but technically wrong or fundamentally flawed.
- 5-6/10: Correct but very brief/surface-level. Lacks depth or examples.
- 7-8/10: Solid technical answer with good explanation.
- 9-10/10: Expert level. Precise, nuanced, and includes real-world application or optimization details.

Answer to Evaluate:
"{answer}"

Facial Behavior:
- Stability Score: {face_score}/5 (Lower indicates significant movement/distraction)

Return your evaluation in this format:
Overall Score: <number>/10
Technical Accuracy: <brief critical assessment>
Relevance: <how well it answered the specific question>
Communication: <clarity and professional tone>
Suggestions: <1-2 specific points for improvement>
"""

    result = get_ai_completion(prompt)
    return {"result": result}


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

    # Anti-Cheat Timing Logic
    start_time = session.get("interview_start_time", 0)
    duration = datetime.utcnow().timestamp() - start_time
    total_words = sum(len(str(ans).split()) for ans in answers)

    # Biometric Analytics
    frames     = face.get("frames", 1)
    stability  = round((face.get("stability", 0) / frames) * 10, 2)
    blink_rate = face.get("blinkCount", 0)
    smile      = round((face.get("smileScore", 0) / frames) * 10, 2)
    articulation = round((face.get("mouthOpening", 0) / frames) * 100, 2)
    
    no_face      = cheating.get("noFace", 0)
    looking_away = cheating.get("lookingAway", 0)
    reading      = cheating.get("readingDetection", 0)
    phone        = cheating.get("phoneDetected", 0)
    book         = cheating.get("bookDetected", 0)
    extra_people = cheating.get("extraPersons", 0)

    # Build Q&A string for the prompt
    qa_pairs = ""
    for i, ans in enumerate(answers):
        q = questions_list[i] if i < len(questions_list) else f"Question {i+1}"
        qa_pairs += f"Q{i+1}: {q}\nA{i+1}: {ans}\n\n"

    prompt = f"""
You are a SENIOR TECHNICAL RECRUITER and INTEGRITY SPECIALIST. Evaluate this mock interview.

INTEGRITY DATA (Biometrics & Object Detection):
- Interview Duration: {round(duration, 1)} seconds
- Phone Detections: {phone}
- Book Detections: {book}
- Multiple People Detections: {extra_people}
- Reading Script Detection: {reading}
- Looking Away Detections: {looking_away}
- Stability Score: {stability}/10

STRICT SCORING CRITERIA:
1. CHEATING DISQUALIFICATION: You must ONLY set "final_verdict" to "Failed (Cheating)" and "cheating_risk" to "High" if there is clear evidence of physical cheating from the INTEGRITY DATA (e.g., Phone Detections > 2, Book Detections > 2, Multiple People > 2, Reading Script Detection > 2, or Looking Away Detections > 10).
   Do NOT classify the candidate as "Failed (Cheating)" or "High" cheating risk based on their answers alone, even if the answers are completely irrelevant, short, or nonsensical (like answering "hii" or "I don't know"). If the candidate answers poorly but did not trigger physical cheating detections, the verdict should be "Failed" or "Needs Improvement", and cheating risk must be "Low".
2. ANSWERS: Penalize "I don't know" or irrelevant answers (0-2/10).

Return EXACTLY this JSON structure:

{{
  "overall_score": <0-10>,
  "final_verdict": "<Failed (Cheating) | Failed | Needs Improvement | Average | Good | Excellent>",
  "metrics": {{ "tech": <0-10>, "comm": <0-10>, "conf": <0-10> }},
  "behavioral_analysis": {{
    "observations": "Strict 2-3 sentence summary focusing on integrity and performance.",
    "cheating_risk": "Low | High"
  }},
  "qa_analysis": [
    {{ 
      "question": "...", 
      "answer": "...", 
      "score": <0-10>,
      "expert_answer": "..."
    }}
  ],
  "suggestions": ["...", "..."]
}}

Q&A:
{qa_pairs}
"""

    try:
        raw = get_ai_completion(prompt).strip()
        
        # Robust JSON extraction
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            raw = match.group(0)
            
        result_data = json.loads(raw)

        # Force actual questions and answers from session to be used in qa_analysis to prevent AI from using generic placeholders
        if "qa_analysis" in result_data and isinstance(result_data["qa_analysis"], list):
            for idx, item in enumerate(result_data["qa_analysis"]):
                if idx < len(questions_list):
                    item["question"] = questions_list[idx]
                if idx < len(answers):
                    item["answer"] = answers[idx]

        # HARDCODE OVERRIDE: Ensure integrity detections are absolute
        is_physical_cheating = (phone > 2 or book > 2 or extra_people > 2 or reading > 2 or looking_away > 10)
        if is_physical_cheating:
            result_data["overall_score"] = min(result_data["overall_score"], 1)
            result_data["final_verdict"] = "Failed (Cheating)"
            result_data["behavioral_analysis"]["cheating_risk"] = "High"
            
            reasons = []
            if phone > 2: reasons.append("Mobile phone usage")
            if book > 2: reasons.append("Reference material/book usage")
            if extra_people > 2: reasons.append("Multiple people detected")
            if reading > 2: reasons.append("Reading from a script")
            if looking_away > 10: reasons.append("Looking away repeatedly")
            
            result_data["behavioral_analysis"]["observations"] = f"Integrity Breach: {', '.join(reasons)} detected during session. Disqualified for cheating."
        else:
            # Prevent false-positive cheating verdicts based on bad/short answers
            if result_data.get("final_verdict") == "Failed (Cheating)":
                result_data["final_verdict"] = "Failed"
            if result_data.get("behavioral_analysis", {}).get("cheating_risk") == "High":
                result_data["behavioral_analysis"]["cheating_risk"] = "Low"

    except Exception as e:
        print(f"Final evaluation parse error: {e}")
        # Use a fail-safe but realistic score for failed answers
        result_data = {
            "overall_score": 2,
            "final_verdict": "Failed",
            "metrics": {"tech": 1, "comm": 2, "conf": 3},
            "behavioral_analysis": {
                "observations": "Candidate provided insufficient or irrelevant answers to technical questions.",
                "cheating_risk": "Low"
            },
            "qa_analysis": [{"question": questions_list[i] if i < len(questions_list) else f"Q{i+1}", "answer": ans, "score": 1} for i, ans in enumerate(answers)],
            "suggestions": ["Improve technical knowledge.", "Provide detailed answers."]
        }

    return result_data


# Custom FPDF subclass to enforce a dark background on all pages
class DarkThemePDF(FPDF):
    def header(self):
        # Fill the entire page with a dark slate background (#090d16)
        self.set_fill_color(9, 13, 22)
        self.rect(0, 0, self.w, self.h, 'F')

@app.route("/download-report")
def download_report():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT result_json FROM interview_scores WHERE username=? ORDER BY id DESC LIMIT 1", (username,))
        row = cursor.fetchone()
        conn.close()

        if not row: return "No report found."
        data = json.loads(row["result_json"])

        # Helper to clean special characters that cause Latin-1 encoding errors in FPDF
        def clean_pdf_text(text):
            if not isinstance(text, str):
                return str(text)
            replacements = {
                '\u201c': '"',  # Left double quote
                '\u201d': '"',  # Right double quote
                '\u2018': "'",  # Left single quote
                '\u2019': "'",  # Right single quote
                '\u2013': '-',  # En dash
                '\u2014': '-',  # Em dash
                '\u2022': '*',  # Bullet point
                '\u2026': '...', # Ellipsis
            }
            for orig, rep in replacements.items():
                text = text.replace(orig, rep)
            return text.encode('latin-1', 'replace').decode('latin-1')

        # Generate Dark Theme PDF
        pdf = DarkThemePDF()
        pdf.set_margins(15, 15, 15)
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        
        # Calculate effective page width dynamically
        epw = pdf.w - pdf.l_margin - pdf.r_margin

        # Theme Colors (Matching the dark-theme website)
        c_indigo = (99, 102, 241)
        c_dark_card = (17, 24, 39)
        c_text_white = (255, 255, 255)
        c_text_gray = (156, 163, 175)
        c_border = (31, 41, 55)
        c_success = (16, 185, 129)
        c_danger = (239, 68, 68)

        # Header branding
        pdf.set_font("Arial", 'B', 16)
        pdf.set_text_color(*c_indigo)
        pdf.cell(50, 10, "ATS.AI", ln=False)
        
        pdf.set_font("Arial", '', 10)
        pdf.set_text_color(*c_text_gray)
        pdf.cell(0, 10, "INTERVIEW PERFORMANCE REPORT", ln=True, align='R')
        
        # Divider Line
        pdf.set_draw_color(*c_border)
        pdf.set_line_width(0.2)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(8)

        # Document Title
        pdf.set_font("Arial", 'B', 20)
        pdf.set_text_color(*c_text_white)
        pdf.cell(epw, 12, "Interview Performance Report", ln=True)
        
        pdf.set_font("Arial", '', 11)
        pdf.set_text_color(*c_text_gray)
        pdf.cell(epw, 6, clean_pdf_text(f"Candidate: {username}   |   Generated: {datetime.now().strftime('%B %d, %Y')}"), ln=True)
        pdf.ln(6)

        # Score & Verdict Card (styled block matching website dashboard)
        pdf.set_fill_color(*c_dark_card)
        pdf.set_draw_color(*c_border)
        card_height = 25
        pdf.rect(pdf.l_margin, pdf.get_y(), epw, card_height, 'DF')
        
        # Write score & verdict inside card
        current_y = pdf.get_y()
        pdf.set_xy(pdf.l_margin + 5, current_y + 4)
        pdf.set_font("Arial", 'B', 9)
        pdf.set_text_color(*c_text_gray)
        pdf.cell(45, 6, "OVERALL SCORE", ln=False)
        pdf.cell(70, 6, "VERDICT", ln=False)
        pdf.cell(0, 6, "INTEGRITY / CHEATING RISK", ln=True, align='R')
        
        pdf.set_x(pdf.l_margin + 5)
        pdf.set_font("Arial", 'B', 14)
        
        # Overall Score
        pdf.set_text_color(*c_indigo)
        pdf.cell(45, 8, f"{data['overall_score']}/10", ln=False)
        
        # Verdict
        verdict = data['final_verdict']
        if "Cheating" in verdict or "Failed" in verdict:
            pdf.set_text_color(*c_danger)
        else:
            pdf.set_text_color(*c_success)
        pdf.cell(70, 8, clean_pdf_text(verdict.upper()), ln=False)
        
        # Cheating Risk
        risk = data['behavioral_analysis'].get('cheating_risk', 'Low')
        if risk.lower() == 'high':
            pdf.set_text_color(*c_danger)
        else:
            pdf.set_text_color(*c_success)
        pdf.cell(0, 8, risk.upper(), ln=True, align='R')
        
        pdf.set_xy(pdf.l_margin, current_y + card_height + 8)

        # Behavioral Observations Section
        pdf.set_font("Arial", 'B', 13)
        pdf.set_text_color(*c_text_white)
        pdf.cell(epw, 8, "Behavioral Observations", ln=True)
        pdf.ln(2)
        
        pdf.set_font("Arial", '', 10.5)
        pdf.set_text_color(*c_text_white)
        pdf.multi_cell(epw, 6, clean_pdf_text(data['behavioral_analysis']['observations']))
        pdf.ln(6)

        # Q&A Breakdown Section
        pdf.set_font("Arial", 'B', 13)
        pdf.set_text_color(*c_text_white)
        pdf.cell(epw, 10, "Question & Answer Breakdown", ln=True)
        pdf.ln(2)

        for i, item in enumerate(data['qa_analysis']):
            # Keep Q&A blocks grouped on page if possible
            if pdf.get_y() > 230:
                pdf.add_page()
                
            q_num = i + 1
            question_text = item['question']
            answer_text = item['answer']
            expert_text = item.get('expert_answer', 'Technical depth and specific examples recommended.')
            score_val = item.get('score', 0)
            
            start_y = pdf.get_y()
            pdf.ln(3) # Top padding inside card
            
            # Question Header
            pdf.set_x(pdf.l_margin + 5)
            pdf.set_font("Arial", 'B', 11)
            pdf.set_text_color(*c_text_white)
            pdf.multi_cell(epw - 30, 5, clean_pdf_text(f"Q{q_num}: {question_text}"))
            
            end_q_y = pdf.get_y()
            
            # Score badge in top-right of the block
            pdf.set_xy(pdf.w - pdf.r_margin - 20, start_y + 3)
            pdf.set_fill_color(*c_indigo)
            pdf.rect(pdf.w - pdf.r_margin - 20, start_y + 3, 20, 7, 'F')
            pdf.set_text_color(*c_text_white)
            pdf.set_font("Arial", 'B', 9.5)
            pdf.cell(20, 7, f"{score_val}/10", ln=True, align='C')
            
            # Reset X to indent content inside card
            pdf.set_xy(pdf.l_margin + 5, end_q_y + 4)
            
            # Expert Ideal Answer block
            pdf.set_font("Arial", 'B', 8.5)
            pdf.set_text_color(*c_indigo)
            pdf.cell(epw - 10, 4, "EXPERT IDEAL ANSWER", ln=True)
            
            pdf.set_x(pdf.l_margin + 5)
            pdf.set_font("Arial", 'I', 10)
            pdf.set_text_color(*c_text_gray)
            pdf.multi_cell(epw - 10, 5, clean_pdf_text(expert_text))
            pdf.ln(3)
            
            # Captured Response block
            pdf.set_x(pdf.l_margin + 5)
            pdf.set_font("Arial", 'B', 8.5)
            pdf.set_text_color(*c_text_gray)
            pdf.cell(epw - 10, 4, "YOUR CAPTURED RESPONSE", ln=True)
            
            pdf.set_x(pdf.l_margin + 5)
            pdf.set_font("Arial", '', 10)
            pdf.set_text_color(*c_text_white)
            pdf.multi_cell(epw - 10, 5, clean_pdf_text(answer_text))
            
            # Pad bottom of card
            pdf.ln(4)
            end_y = pdf.get_y()
            
            # Draw card outline box dynamically
            # Draw left thick accent bar
            pdf.set_draw_color(*c_indigo)
            pdf.set_line_width(1.2)
            pdf.line(pdf.l_margin, start_y, pdf.l_margin, end_y)
            
            # Draw top, bottom, and right card borders in dark gray
            pdf.set_draw_color(*c_border)
            pdf.set_line_width(0.2)
            pdf.line(pdf.l_margin, start_y, pdf.w - pdf.r_margin, start_y)
            pdf.line(pdf.l_margin, end_y, pdf.w - pdf.r_margin, end_y)
            pdf.line(pdf.w - pdf.r_margin, start_y, pdf.w - pdf.r_margin, end_y)
            
            # Add separation space for next card
            pdf.set_xy(pdf.l_margin, end_y + 6)

        # Suggestions Section
        if pdf.get_y() > 220:
            pdf.add_page()
            
        pdf.set_font("Arial", 'B', 13)
        pdf.set_text_color(*c_text_white)
        pdf.cell(epw, 8, "Expert Suggestions", ln=True)
        pdf.ln(2)
        
        pdf.set_font("Arial", '', 10.5)
        pdf.set_text_color(*c_text_white)
        for sug in data.get('suggestions', []):
            pdf.multi_cell(epw, 6, clean_pdf_text(f"- {sug}"))

        # Output to buffer
        pdf_content = pdf.output(dest='S')
        if isinstance(pdf_content, str):
            pdf_content = pdf_content.encode('latin-1')
        output = io.BytesIO(pdf_content)
        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name=f"Interview_Report_{username}.pdf",
            mimetype="application/pdf"
        )
    except Exception as e:
        return f"PDF Error: {e}"


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
            "INSERT INTO interview_scores (username, result_json) VALUES (?, ?)",
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
            "SELECT result_json FROM interview_scores WHERE username=? ORDER BY id DESC LIMIT 1",
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


@app.route('/optimize-resume', methods=['POST'])
def optimize_resume():
    if "user" not in session:
        return redirect(url_for('login'))

    try:
        resume_text = request.form.get('resume_text')
        job_desc = request.form.get('job_desc')

        if not resume_text or not job_desc:
            return "Missing data for optimization"

        prompt = f"""
You are an expert Resume Optimizer. 

Based on the Job Description, suggest up to 5 (but at least 1) specific, unique sentence rewrites for this resume to make it more impactful and ATS-friendly. 
If the resume is already highly optimized and has a good match, only suggest rewrites for sentences that can genuinely be improved. Do NOT repeat the same original sentence or make up redundant duplicates just to reach 5 suggestions. If there are only 1, 2, or 3 improvements needed, only return those. Do not return more than 5 under any circumstances.

RETURN ONLY VALID JSON. Do not include any conversational text or markdown formatting before or after the JSON.

Format:
[
  {{
    "original": "original sentence from resume",
    "improved": "impactful rewrite",
    "reason": "why it is better"
  }}
]

Resume:
{resume_text[:3000]}

Job Description:
{job_desc[:2000]}
"""

        raw_output = get_ai_completion(prompt).strip()

        # Robust JSON extraction
        import re
        # Find the first '[' and last ']'
        match = re.search(r'\[.*\]', raw_output, re.DOTALL)
        if match:
            json_str = match.group(0)
            suggestions_list = json.loads(json_str)
        else:
            # Fallback if no array brackets are found
            suggestions_list = json.loads(raw_output)

        # Remove duplicate suggestions and limit to at most 5
        unique_suggestions = []
        seen_originals = set()
        for sug in suggestions_list:
            orig = sug.get("original", "").strip()
            if orig and orig.lower() not in seen_originals:
                seen_originals.add(orig.lower())
                unique_suggestions.append(sug)
        unique_suggestions = unique_suggestions[:5]

        return render_template("optimize_result.html", suggestions=unique_suggestions)

    except Exception as e:
        print(f"Optimization Error: {e}")
        # Fallback empty list if parsing fails
        return render_template("optimize_result.html", suggestions=[])



@app.route('/view-analysis/<int:analysis_id>')
def view_analysis(analysis_id):
    if "user" not in session:
        return redirect(url_for('login'))

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT full_data FROM resume_evaluations WHERE id=? AND username=?",
            (analysis_id, session['user'])
        )
        row = cursor.fetchone()
        conn.close()

        if row and row['full_data']:
            data = json.loads(row['full_data'])
            return render_template(
                "result.html",
                score=data['score'],
                matched_tech=data['matched_tech'],
                matched_soft=data['matched_soft'],
                missing=data['missing'],
                suggestions=data['suggestions'],
                resume_text=data.get('resume_text', ''),
                job_desc=data.get('job_desc', '')
            )
        else:
            return "Analysis not found or data missing."
    except Exception as e:
        return f"Error: {e}"


@app.route('/delete-analysis/<int:analysis_id>')
def delete_analysis(analysis_id):
    if "user" not in session:
        return redirect(url_for('login'))

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM resume_evaluations WHERE id=? AND username=?",
            (analysis_id, session['user'])
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Delete Error: {e}")

    return redirect(url_for('dashboard'))


@app.route('/generate-roadmap', methods=['POST'])
def generate_roadmap():
    if "user" not in session:
        return {"error": "Unauthorized"}, 401

    try:
        data = request.get_json()
        skills = data.get("skills", [])

        if not skills:
            return {"roadmap": "No missing skills identified. You're on the right track!"}

        prompt = f"""
You are a Senior Career Mentor and Technical Instructor.

Generate a highly structured 4-Week Learning Roadmap to help a candidate master these missing skills:
{", ".join(skills)}

STRICT FORMATTING RULES:
- Break it down by Week 1, Week 2, Week 3, Week 4.
- For each week, provide 2-3 specific topics to study.
- Include 1-2 free resource names (like "Official Docs" or "FreeCodeCamp").
- Keep it concise and professional.
- Use **bold** for key topics.

Candidate's goal: Mastering these gaps to qualify for their target job.
"""

        roadmap = get_ai_completion(prompt)
        return {"roadmap": roadmap}

    except Exception as e:
        print(f"Roadmap Error: {e}")
        return {"roadmap": "Error generating roadmap. Please try again later."}, 500


@app.route('/view-interview/<int:interview_id>')
def view_interview(interview_id):
    if "user" not in session:
        return redirect(url_for('login'))

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT result_json FROM interview_scores WHERE id=? AND username=?",
            (interview_id, session['user'])
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            result_data = json.loads(row["result_json"])
            return render_template("final_report.html", result=result_data)
        else:
            return "Interview record not found."
    except Exception as e:
        return f"Error: {e}"


@app.route('/delete-interview/<int:interview_id>')
def delete_interview(interview_id):
    if "user" not in session:
        return redirect(url_for('login'))

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM interview_scores WHERE id=? AND username=?",
            (interview_id, session['user'])
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Delete Error: {e}")

    return redirect(url_for('dashboard'))


if __name__ == '__main__':
    app.run(debug=True)
