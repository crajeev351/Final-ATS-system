from flask import Flask, render_template, request, redirect, url_for, session
import pymysql
import os
from pdfminer.high_level import extract_text
import google.generativeai as genai
app = Flask(__name__)
app.secret_key = "secret123"

genai.configure(api_key="AIzaSyBb0Uhryzwv65Aj67I1oOz2dR3pORqC1UY")
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

if __name__ == '__main__':
    app.run(debug=True)