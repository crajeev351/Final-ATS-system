from flask import Flask, render_template, request, redirect, url_for, session
import pymysql
import os
from dotenv import load_dotenv
from pdfminer.high_level import extract_text
import google.generativeai as genai

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = "secret123"



# API key loaded securely from .env
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash-lite')

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
            resume_text = extract_text(filepath)

            # AI PROMPT
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

            # Parse AI Output
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

        return render_template("questions.html", questions=questions)

    except Exception as e:
        return f"Error: {e}"


@app.route("/ai-interview")
def ai_interview():
    if "user" not in session:
        return redirect(url_for("login"))

    

    # Generate basic questions
    prompt = """
Generate 3 simple HR interview questions.

Rules:
- Very basic
- General questions
- No numbering
"""

    response = model.generate_content(prompt)
    basic_q = [q.strip() for q in response.text.split("\n") if q.strip()]

    # Get resume questions
    resume_q = session.get("questions", [])

    # Merge both
    all_q = basic_q + resume_q

    session["all_questions"] = all_q

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


@app.route("/get-all-questions")
def get_questions():
    return {"questions": session.get("all_questions", [])}

@app.route("/final-evaluation", methods=["POST"])
def final_evaluation():
    data = request.get_json()

    answers = data.get("answers", [])
    face = data.get("face", {})
    cheating = data.get("cheating", {})

    stability = face.get("stability", 0)
    frames = face.get("frames", 1)

    face_score = round((stability / frames) * 10, 2)

    cheating_score = (
        cheating.get("noFace", 0) +
        cheating.get("multipleFaces", 0) +
        cheating.get("lookingAway", 0)
    )

    prompt = f"""
You are an AI interviewer.

Evaluate the candidate completely.

Answers:
{answers}

Face Confidence Score: {face_score}/10

Cheating Indicators:
- No Face Count: {cheating.get("noFace")}
- Multiple Faces: {cheating.get("multipleFaces")}
- Looking Away: {cheating.get("lookingAway")}

Give output in this format:

Overall Score: /10

Technical Skills:
- ...

Communication:
- ...

Confidence:
- ...

Cheating Analysis:
- ...

Final Verdict:
- ...

Suggestions for Improvement:
- ...
"""

    response = model.generate_content(prompt)

    result_text = response.text

    # Temporary values (you can improve later)
    data = {
        "text": result_text,
        "tech": 7,
        "comm": 6,
        "conf": 8
    }

    return data

@app.route("/show-final-report", methods=["POST"])
def show_final_report():
    if "user" not in session:
        return {"status": "error", "message": "User not logged in"}, 401

    data = request.get_json()
    username = session["user"]

    try:
        import json
        conn = get_db()
        cursor = conn.cursor()

        # Store the final result in the database for this user
        # We use LONGTEXT to store the JSON string
        result_json = json.dumps(data)
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


@app.route("/final-report")
def final_report():
    if "user" not in session:
        return redirect(url_for("login"))

    username = session["user"]

    try:
        conn = get_db()
        cursor = conn.cursor()

        # Fetch the latest interview result for this user
        cursor.execute(
            "SELECT result_json FROM interview_scores WHERE username=%s ORDER BY created_at DESC LIMIT 1",
            (username,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            import json
            result_data = json.loads(row["result_json"])
            return render_template(
                "final_report.html",
                result=result_data
            )
        else:
            return "No interview results found for this user. Please complete an interview first."

    except Exception as e:
        return f"Error retrieving report: {e}"



@app.route('/logout')
def logout():
    session.pop("user", None)
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)