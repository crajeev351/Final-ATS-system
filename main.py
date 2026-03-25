from flask import Flask, render_template, request, redirect, url_for
app = Flask(__name__)
import pymysql
def get_db():
    return pymysql.connect(
        host="localhost",
        user="root",
        password="",  # XAMPP default
        database="ats_project",
        cursorclass=pymysql.cursors.DictCursor
    )

# DEAULT PAGE → SIGNUP
@app.route('/', methods=['GET', 'POST'])
def signup():
    message = ""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if username in users:
            message = "User already exists!"
        else:
            users[username] = password
            return redirect(url_for('login'))

    return render_template('signup.html', message=message)

# LOGIN PAGE
@app.route('/login', methods=['GET', 'POST'])
def login():
    message = ""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if username in users and users[username] == password:
            message = "Login Successful!"
        else:
            message = "Invalid Credentials"

    return render_template('login.html', message=message)

if __name__ == '__main__':
    app.run(debug=True)
