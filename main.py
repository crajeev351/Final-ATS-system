from flask import Flask, render_template, request, redirect, url_for, session
import pymysql
import os
from pdfminer.high_level import extract_text
import google.generativeai as genai
app = Flask(__name__)
app.secret_key = "secret123"

genai.configure(api_key="AIzaSyApYBHuEnoYFQrv5_gnW-ryVCldWyta9pE")
model = genai.GenerativeModel('gemini-3-flash-preview')

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

        # Check if user already exists
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

        # Check user credentials
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

            # Extract text
            from pdfminer.high_level import extract_text
            resume_text = extract_text(filepath)

            # 🔥 AI PROMPT
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

            # 🔥 Parse AI Output
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

        # Save file
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)

        # Extract text
        from pdfminer.high_level import extract_text
        resume_text = extract_text(filepath)

        # Limit size
        resume_text = resume_text[:3000]

        # Prompt
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

        # AI Call
        response = model.generate_content(prompt)
        raw_text = response.text

        # Parse questions
        questions = []
        for line in raw_text.split("\n"):
            line = line.strip()
            if line.startswith(tuple(str(i) + "." for i in range(1, 6))):
                questions.append(line)

        # Fallback safety
        if not questions:
            questions = ["Tell me about your project"]

        # Store in session
        session["questions"] = questions

        # ✅ FIXED LINE (ADDED RETURN)
        return render_template("questions.html", questions=questions)

    except Exception as e:
        return f"Error: {e}"
    

    
@app.route("/ai-interview")
def ai_interview():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("ai_interview.html")    


@app.route("/evaluate-answer", methods=["POST"])
def evaluate_answer():
    data = request.get_json()

    answer = data.get("answer")
    face = data.get("face")

    stability = face.get("stability", 0)
    movement = face.get("movement", 0)
    frames = face.get("frames", 1)

    face_score = round((stability / frames) * 5, 2)

    prompt = f"""
You are an AI interviewer.

Evaluate candidate based on:

Answer:
{answer}

Facial Behavior:
- Stability Score: {face_score}
- Movement Level: {movement}

Give:

Overall Score: out of 10

Technical:
- ...

Communication:
- ...

Facial Confidence:
- ...

Suggestions:
- ...
"""

    response = model.generate_content(prompt)

    return {"result": response.text}

@app.route("/get-questions")
def get_questions():
    return {"questions": session.get("questions", [])}

if __name__ == '__main__':
    app.run(debug=True)