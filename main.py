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

# SQLite Database setup
DB_FILE = "database.db"

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Create Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    # Create Resume Evaluations table
    cursor.execute("""
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
    """)
    # Create Interview Scores table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interview_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            result_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# Initialize DB on start
init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = dict_factory
    return conn

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
                flash("Please upload a resume")
                return redirect(url_for('dashboard'))

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filepath)

            resume_text = extract_text_from_file(filepath)

            prompt = f"""
You are an expert Technical Recruiter and ATS Optimization Specialist.

Analyze the provided Resume against the Job Description.

CRITICAL PRECISION RULES:
1. TECHNICAL SKILLS MATCHED: List ONLY technical skills (languages, frameworks, tools) that are EXPLICITLY mentioned in the Resume AND relevant to the Job Description.
2. SOFT SKILLS MATCHED: List ONLY professional traits EXPLICITLY found in the Resume.
3. MISSING SKILLS: List critical technical requirements from the Job Description that are NOT in the Resume.
4. STRATEGIC ADVICE: Provide 3-5 highly specific, actionable suggestions. Provide actual examples based on the resume.
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
- Stability Score: {stability}/10

STRICT SCORING CRITERIA:
1. CHEATING: If Phone Detections > 0, Book Detections > 0, or Multiple People > 0, you MUST set "cheating_risk" to "HIGH" and "overall_score" to 0 or 1.
2. DISQUALIFICATION: In "observations", state clearly if unauthorized objects or people were detected.
3. ANSWERS: Penalize "I don't know" or irrelevant answers (0-2/10).

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

        # Override if timing is suspicious (Short time + Detailed answers)
        if duration < 15 and total_words > 20:
            result_data["overall_score"] = 0
            result_data["final_verdict"] = "Failed (Cheating)"
            result_data["behavioral_analysis"]["cheating_risk"] = "High"
            result_data["behavioral_analysis"]["observations"] = f"Suspiciously fast submission ({round(duration, 1)}s) with detailed answers. Integrity breach detected."

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

        # Generate PDF
        pdf = FPDF()
        pdf.set_margins(15, 15, 15)
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        
        epw = pdf.epw # Effective page width

        # Header
        pdf.set_font("Arial", 'B', 24)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(epw, 20, "Interview Performance Report", ln=True, align='C')
        
        pdf.set_font("Arial", '', 12)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(epw, 10, f"Candidate: {username}", ln=True, align='C')
        pdf.ln(10)

        # Overall Score Card
        pdf.set_fill_color(241, 245, 249)
        pdf.rect(pdf.l_margin, pdf.get_y(), epw, 40, 'F')
        
        current_y = pdf.get_y()
        pdf.set_xy(pdf.l_margin, current_y + 10)
        pdf.set_font("Arial", 'B', 16)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(epw, 10, f"Overall Score: {data['overall_score']}/10", ln=True, align='C')
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(epw, 10, f"Verdict: {data['final_verdict']}", ln=True, align='C')
        
        pdf.set_xy(pdf.l_margin, current_y + 45) # Move below scorecard

        # Behavioral Analysis
        pdf.set_font("Arial", 'B', 14)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(epw, 10, "Behavioral Observations", ln=True)
        pdf.set_font("Arial", '', 11)
        pdf.multi_cell(epw, 8, data['behavioral_analysis']['observations'])
        pdf.ln(5)
        pdf.cell(epw, 10, f"Cheating Risk: {data['behavioral_analysis']['cheating_risk']}", ln=True)
        pdf.ln(10)

        # Q&A Breakdown
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(epw, 10, "Question & Answer Breakdown", ln=True)
        pdf.ln(5)
        
        for item in data['qa_analysis']:
            # Ensure we don't break in the middle of a Q&A block if possible
            if pdf.get_y() > 250: pdf.add_page()

            pdf.set_font("Arial", 'B', 11)
            pdf.multi_cell(epw, 8, f"Q: {item['question']}")
            
            pdf.set_font("Arial", 'I', 10)
            pdf.set_text_color(99, 102, 241)
            pdf.multi_cell(epw, 7, f"Expert Answer: {item.get('expert_answer', 'N/A')}")
            pdf.ln(2)
            
            pdf.set_font("Arial", '', 11)
            pdf.set_text_color(15, 23, 42)
            pdf.multi_cell(epw, 8, f"Your Answer: {item['answer']}")
            
            pdf.set_font("Arial", 'B', 11)
            pdf.set_text_color(34, 197, 94)
            pdf.cell(epw, 8, f"Score: {item['score']}/10", ln=True)
            pdf.set_text_color(15, 23, 42)
            pdf.ln(5)

        # Suggestions
        pdf.ln(10)
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(epw, 10, "Expert Suggestions", ln=True)
        pdf.set_font("Arial", '', 11)
        for sug in data['suggestions']:
            pdf.multi_cell(epw, 8, f"- {sug}")

        # Output to buffer
        pdf_content = pdf.output(dest='S')
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


@app.route("/send-email-report", methods=["POST"])
def send_email_report():
    if "user" not in session:
        return {"status": "error", "message": "Unauthorized"}, 401

    try:
        data = request.get_json()
        recipient_email = data.get("email")
        if not recipient_email:
            return {"status": "error", "message": "Email is required"}, 400

        username = session["user"]
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT result_json FROM interview_scores WHERE username=? ORDER BY id DESC LIMIT 1", (username,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return {"status": "error", "message": "No report found"}, 404

        report_data = json.loads(row["result_json"])

        # Generate PDF (Reusing simplified version of download_report logic)
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "Interview Performance Report", ln=True, align='C')
        pdf.ln(10)
        pdf.set_font("Arial", '', 12)
        pdf.cell(0, 10, f"Candidate: {username}", ln=True)
        pdf.cell(0, 10, f"Overall Score: {report_data['overall_score']}/10", ln=True)
        pdf.cell(0, 10, f"Verdict: {report_data['final_verdict']}", ln=True)
        pdf.ln(10)
        pdf.multi_cell(0, 10, f"Observations: {report_data['behavioral_analysis']['observations']}")

        pdf_content = pdf.output(dest='S')
        
        # Email Configuration
        sender_email = os.getenv("SENDER_EMAIL")
        sender_password = os.getenv("SENDER_PASSWORD") # App Password
        
        if not sender_email or not sender_password:
            return {"status": "error", "message": "SMTP not configured on server"}, 500

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = recipient_email
        msg['Subject'] = f"Your ATS.AI Interview Report - {username}"

        body = f"Hello {username},\n\nPlease find attached your detailed AI Interview Performance Report.\n\nBest Regards,\nATS.AI Team"
        msg.attach(MIMEText(body, 'plain'))

        part = MIMEBase('application', 'octet-stream')
        part.set_payload(pdf_content)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f"attachment; filename= Interview_Report_{username}.pdf")
        msg.attach(part)

        # Send Email
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()

        return {"status": "ok", "message": "Report emailed successfully!"}

    except Exception as e:
        print(f"Email Error: {e}")
        return {"status": "error", "message": str(e)}, 500


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

Based on the Job Description, suggest 5 specific sentence rewrites for this resume to make it more impactful and ATS-friendly.

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

        return render_template("optimize_result.html", suggestions=suggestions_list)

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
