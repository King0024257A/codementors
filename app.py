from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
import sqlite3
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from langchain_google_genai import ChatGoogleGenerativeAI
from io import BytesIO
from xhtml2pdf import pisa

app = Flask(__name__)
app.secret_key = "your_super_secret_key"

llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, id_, username):
        self.id = id_
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect('progress.db')
    c = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    if user:
        return User(user[0], user[1])
    return None

def init_db():
    conn = sqlite3.connect('progress.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password_hash TEXT,
            secret_question TEXT,
            secret_answer TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            topic TEXT,
            question TEXT,
            user_answer TEXT,
            correct_answer TEXT,
            is_correct INTEGER,
            quiz_id INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS deleted_results (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            topic TEXT,
            question TEXT,
            user_answer TEXT,
            correct_answer TEXT,
            is_correct INTEGER,
            quiz_id INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        secret_q = request.form["secret_question"]
        secret_a = request.form["secret_answer"]
        hash_pw = generate_password_hash(password)

        conn = sqlite3.connect('progress.db')
        c = conn.cursor()
        try:
            c.execute(
                "INSERT INTO users (username, password_hash, secret_question, secret_answer) VALUES (?, ?, ?, ?)",
                (username, hash_pw, secret_q, secret_a)
            )
            conn.commit()
            flash("Registered successfully! Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists.", "danger")
            return redirect(url_for("register"))
        finally:
            conn.close()
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = sqlite3.connect('progress.db')
        c = conn.cursor()
        c.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user[1], password):
            user_obj = User(user[0], username)
            login_user(user_obj)
            flash("Logged in successfully!", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid credentials.", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        username = request.form["username"]
        conn = sqlite3.connect('progress.db')
        c = conn.cursor()
        c.execute("SELECT id, secret_question FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()
        if user:
            session["reset_user_id"] = user[0]
            return render_template("answer_secret.html", question=user[1])
        else:
            flash("Username not found.", "danger")
            return redirect(url_for("forgot"))
    return render_template("forgot.html")

@app.route("/answer_secret", methods=["POST"])
def answer_secret():
    answer = request.form["secret_answer"]
    conn = sqlite3.connect('progress.db')
    c = conn.cursor()
    c.execute("SELECT secret_answer FROM users WHERE id = ?", (session["reset_user_id"],))
    real_answer = c.fetchone()[0]
    conn.close()
    if answer.strip().lower() == real_answer.strip().lower():
        return render_template("reset_password.html")
    else:
        flash("Incorrect answer.", "danger")
        return redirect(url_for("forgot"))

@app.route("/reset_password", methods=["POST"])
def reset_password():
    new_pw = request.form["new_password"]
    hash_pw = generate_password_hash(new_pw)
    conn = sqlite3.connect('progress.db')
    c = conn.cursor()
    c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_pw, session["reset_user_id"]))
    conn.commit()
    conn.close()
    flash("Password reset successfully.", "success")
    return redirect(url_for("login"))

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("login"))

@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        topic = request.form["topic"]
        quiz = generate_quiz(topic)
        quiz_id = generate_quiz_id()
        session['quiz'] = quiz
        session['topic'] = topic
        session['quiz_id'] = quiz_id
        return redirect(url_for("quiz"))

    conn = sqlite3.connect('progress.db')
    c = conn.cursor()
    # Fetch all quizzes taken by this user
    c.execute("""
        SELECT DISTINCT quiz_id, topic FROM results
        WHERE user_id = ?
        ORDER BY quiz_id DESC
    """, (current_user.id,))
    quizzes = c.fetchall()

    quizzes_with_scores = []
    for quiz_id, topic in quizzes:
        c.execute("""
            SELECT COUNT(*) FROM results WHERE user_id = ? AND quiz_id = ?
        """, (current_user.id, quiz_id))
        total = c.fetchone()[0]

        c.execute("""
            SELECT COUNT(*) FROM results WHERE user_id = ? AND quiz_id = ? AND is_correct = 1
        """, (current_user.id, quiz_id))
        correct = c.fetchone()[0]

        quizzes_with_scores.append({
            "id": quiz_id,
            "topic": topic,
            "correct": correct,
            "total": total
        })

    conn.close()
    return render_template("index.html", username=current_user.username, quizzes=quizzes_with_scores)


def generate_quiz_id():
    import time
    return int(time.time())

@app.route("/quiz", methods=["GET", "POST"])
@login_required
def quiz():
    quiz = session.get('quiz')
    topic = session.get('topic')
    quiz_id = session.get('quiz_id')
    if not quiz:
        return redirect(url_for("index"))

    if request.method == "POST":
        conn = sqlite3.connect('progress.db')
        c = conn.cursor()
        for idx, q in enumerate(quiz):
            correct = q["answer"]
            user = request.form.get(f"q{idx}")
            is_correct = 1 if user.strip().upper() == correct.upper() else 0
            c.execute(
                "INSERT INTO results (user_id, topic, question, user_answer, correct_answer, is_correct, quiz_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (current_user.id, topic, q["question"], user, correct, is_correct, quiz_id)
            )
        conn.commit()
        conn.close()
        return redirect(url_for("report") + f"?quiz_id={quiz_id}")

    return render_template("quiz.html", topic=topic, quiz=quiz)

@app.route("/report")
@login_required
def report():
    quiz_id = request.args.get("quiz_id")
    if not quiz_id:
        flash("Invalid quiz report.", "warning")
        return redirect(url_for("index"))

    conn = sqlite3.connect('progress.db')
    c = conn.cursor()
    c.execute("""
        SELECT topic, question, user_answer, correct_answer, is_correct
        FROM results
        WHERE user_id = ? AND quiz_id = ?
    """, (current_user.id, quiz_id))
    data = c.fetchall()
    conn.close()
    return render_template("report.html", data=data, quiz_id=quiz_id)

@app.route("/download_pdf")
@login_required
def download_pdf():
    quiz_id = request.args.get("quiz_id")
    if not quiz_id:
        flash("Invalid quiz.", "warning")
        return redirect(url_for("index"))
    conn = sqlite3.connect('progress.db')
    c = conn.cursor()
    c.execute("""
        SELECT topic, question, user_answer, correct_answer, is_correct
        FROM results
        WHERE user_id = ? AND quiz_id = ?
    """, (current_user.id, quiz_id))
    data = c.fetchall()
    conn.close()
    html = render_template("report_pdf.html", data=data)
    pdf = BytesIO()
    pisa.CreatePDF(html, dest=pdf)
    pdf.seek(0)
    return send_file(pdf, download_name=f"quiz_{quiz_id}.pdf")

@app.route("/delete_quiz/<int:quiz_id>", methods=["POST"])
@login_required
def delete_quiz(quiz_id):
    conn = sqlite3.connect('progress.db')
    c = conn.cursor()
    c.execute("""
        INSERT INTO deleted_results (user_id, topic, question, user_answer, correct_answer, is_correct, quiz_id)
        SELECT user_id, topic, question, user_answer, correct_answer, is_correct, quiz_id
        FROM results
        WHERE user_id = ? AND quiz_id = ?
    """, (current_user.id, quiz_id))
    c.execute("DELETE FROM results WHERE user_id = ? AND quiz_id = ?", (current_user.id, quiz_id))
    conn.commit()
    conn.close()
    session['deleted_quiz_id'] = quiz_id
    flash(f"Quiz deleted. <a href='{url_for('undo_delete')}' class='alert-link'>Undo</a>", "warning")
    return redirect(url_for("index"))

@app.route("/undo_delete")
@login_required
def undo_delete():
    quiz_id = session.get('deleted_quiz_id')
    if not quiz_id:
        flash("Nothing to undo.", "info")
        return redirect(url_for("index"))
    conn = sqlite3.connect('progress.db')
    c = conn.cursor()
    c.execute("""
        INSERT INTO results (user_id, topic, question, user_answer, correct_answer, is_correct, quiz_id)
        SELECT user_id, topic, question, user_answer, correct_answer, is_correct, quiz_id
        FROM deleted_results
        WHERE user_id = ? AND quiz_id = ?
    """, (current_user.id, quiz_id))
    c.execute("DELETE FROM deleted_results WHERE user_id = ? AND quiz_id = ?", (current_user.id, quiz_id))
    conn.commit()
    conn.close()
    session.pop('deleted_quiz_id', None)
    flash("Undo successful.", "success")
    return redirect(url_for("index"))

def generate_quiz(topic):
    prompt = f"""
    You are a coding tutor. For the topic "{topic}", create 15 multiple choice quiz questions.
    Each question must have:
    - Question text
    - 4 options labeled A), B), C), D)
    - Correct Answer (just the letter)

    Format:
    For each question:
    Q: [question]
    A) ...
    B) ...
    C) ...
    D) ...
    Answer: [A/B/C/D]

    Make sure they are clear and concise.
    """

    response = llm.invoke(prompt)
    content = response.content

    lines = content.splitlines()
    quiz = []
    current = {}

    for line in lines:
        line = line.strip()
        if line.startswith("Q:"):
            # Save any existing question
            if current:
                quiz.append(current)
            current = {"question": line[2:].strip(), "options": []}
        elif line.startswith(("A)", "B)", "C)", "D)")):
            # Defensive: Make sure options exist
            if "options" not in current:
                current["options"] = []
            current["options"].append(line)
        elif line.startswith("Answer:"):
            current["answer"] = line.split(":")[1].strip()

    # Save last question if any
    if current:
        quiz.append(current)

    return quiz[:15]


if __name__ == "__main__":
    app.run(debug=True)
