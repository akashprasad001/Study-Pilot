from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from dotenv import load_dotenv
from groq import Groq
from datetime import date, datetime, timedelta
import os
import json
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "studypilot-dev-secret-change-this")


# =========================
# DATABASE
# =========================

def get_db():
    conn = sqlite3.connect("studypilot.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Study plans table
    # user_id links every study plan to the logged-in student.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS study_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_date TEXT NOT NULL,
            days_remaining INTEGER NOT NULL,
            study_hours TEXT NOT NULL,
            syllabus TEXT NOT NULL,
            created_at TEXT NOT NULL,
            user_id INTEGER
        )
    """)

    # Add user_id to older study_plans tables created before authentication.
    cursor.execute("PRAGMA table_info(study_plans)")
    study_plan_columns = [row["name"] for row in cursor.fetchall()]

    if "user_id" not in study_plan_columns:
        cursor.execute("ALTER TABLE study_plans ADD COLUMN user_id INTEGER")

    # Progress table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            day_number INTEGER NOT NULL,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY (plan_id) REFERENCES study_plans(id),
            UNIQUE(plan_id, day_number)
        )
    """)

    # Quiz table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (plan_id) REFERENCES study_plans(id)
        )
    """)

    # Quiz results table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            score INTEGER NOT NULL,
            total_questions INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id),
            FOREIGN KEY (plan_id) REFERENCES study_plans(id)
        )
    """)

    conn.commit()
    conn.close()


# =========================
# GROQ
# =========================

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)


# =========================
# HOME
# =========================

@app.route("/")
def home():
    user = session.get("user")

    return render_template(
        "index.html",
        user=user,
        user_name=user.get("name") if user else None
    )


# =========================
# AUTHENTICATION
# =========================

@app.route("/signup", methods=["GET", "POST"])
def signup():

    if request.method == "GET":
        return render_template("signup.html")

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not name or not email or not password:
        return render_template("signup.html", error="All fields are required.")

    if len(password) < 6:
        return render_template("signup.html", error="Password must be at least 6 characters.")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    existing_user = cursor.fetchone()

    if existing_user:
        conn.close()
        return render_template("signup.html", error="An account with this email already exists.")

    cursor.execute(
        """
        INSERT INTO users (name, email, password_hash, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (name, email, generate_password_hash(password), str(datetime.now()))
    )

    user_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Attach any existing legacy study plans to the first account.
    conn = get_db()
    conn.execute(
        "UPDATE study_plans SET user_id = ? WHERE user_id IS NULL",
        (user_id,)
    )
    conn.commit()
    conn.close()

    session["user_id"] = user_id
    session["user"] = {"id": user_id, "name": name, "email": email}
    session["user_name"] = name

    return redirect(url_for("home"))


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE email = ?",
        (email,)
    )

    user = cursor.fetchone()
    conn.close()

    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Invalid email or password.")

    session["user_id"] = user["id"]
    session["user"] = {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"]
    }
    session["user_name"] = user["name"]

    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


def login_required():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return None


# =========================
# CREATE STUDY PLAN
# =========================

@app.route("/create-plan", methods=["POST"])
def create_plan():

    auth_redirect = login_required()
    if auth_redirect:
        return auth_redirect

    syllabus = request.form["syllabus"].strip()
    exam_date = request.form["exam_date"]
    study_hours = request.form["study_hours"]

    today = date.today()
    exam_day = datetime.strptime(
        exam_date,
        "%Y-%m-%d"
    ).date()

    days_remaining = (exam_day - today).days

    if days_remaining < 0:
        return """
        <h1>Invalid Exam Date ❌</h1>
        <p>Please select a future exam date.</p>
        <a href="/">Go Back</a>
        """

    # -------------------------
    # Create study dates
    # -------------------------

    study_dates = []

    for i in range(days_remaining):

        study_date = today + timedelta(days=i)

        study_dates.append(
            str(study_date)
        )

    if days_remaining == 0:
        study_dates = [str(today)]

    available_dates = "\n".join(
        f"Day {i + 1}: {d}"
        for i, d in enumerate(study_dates)
    )

    # -------------------------
    # AI Prompt
    # -------------------------

    prompt = f"""
You are StudyPilot, an AI-powered personal study planning assistant.

Create a realistic study plan for a college student.

TODAY:
{today}

EXAM DATE:
{exam_day}

DAYS AVAILABLE BEFORE EXAM:
{days_remaining}

EXACT STUDY DATES:

{available_dates}

IMPORTANT DATE RULES:

- Use only the dates provided above.
- Do not invent dates.
- Do not create dates before today.
- Do not create dates after the exam date.
- Each study day must have one day number.
- The number of plan days must match the number of available study dates.

STUDENT SYLLABUS:

{syllabus}

DAILY STUDY TIME:

{study_hours} hours

TASK:

Divide the syllabus into logical study topics.

A topic may take more than one day if necessary.

For every study day provide:

1. Main topic
2. Study time
3. Practice activity
4. Revision activity

Make the workload realistic.

IMPORTANT:

- Do not exceed the student's daily study time.
- Include practice questions.
- Include revision.
- Use the final available day mainly for revision when appropriate.
- Do not add topics that are not present in the syllabus.
- Do not create unnecessary duplicate topics.
- Return ONLY the requested structured JSON.
"""

    # -------------------------
    # Study Plan Schema
    # -------------------------

    schema = {
        "type": "object",
        "properties": {

            "days": {
                "type": "array",
                "items": {

                    "type": "object",

                    "properties": {

                        "day": {
                            "type": "integer"
                        },

                        "date": {
                            "type": "string"
                        },

                        "topic": {
                            "type": "string"
                        },

                        "study_time": {
                            "type": "string"
                        },

                        "practice": {
                            "type": "string"
                        },

                        "revision": {
                            "type": "string"
                        }

                    },

                    "required": [
                        "day",
                        "date",
                        "topic",
                        "study_time",
                        "practice",
                        "revision"
                    ],

                    "additionalProperties": False
                }
            },

            "revision_strategy": {
                "type": "string"
            }

        },

        "required": [
            "days",
            "revision_strategy"
        ],

        "additionalProperties": False
    }

    # -------------------------
    # Groq Call
    # -------------------------

    response = client.chat.completions.create(

        model="openai/gpt-oss-20b",

        messages=[

            {
                "role": "system",
                "content":
                "You are a practical and accurate AI study planner. "
                "Always follow the requested JSON schema exactly."
            },

            {
                "role": "user",
                "content": prompt
            }

        ],

        max_completion_tokens=4096,

        reasoning_effort="low",

        response_format={
            "type": "json_schema",

            "json_schema": {

                "name": "study_plan",

                "strict": True,

                "schema": schema
            }
        }
    )

    ai_result = response.choices[0].message.content

    if not ai_result:

        return """
        <h1>AI Error ❌</h1>
        <p>StudyPilot did not receive a valid response.</p>
        <a href="/">Try Again</a>
        """

    # -------------------------
    # Parse JSON
    # -------------------------

    try:

        ai_plan = json.loads(ai_result)

    except json.JSONDecodeError:

        return """
        <h1>AI Response Error ❌</h1>
        <p>The AI returned an invalid study plan.</p>
        <a href="/">Try Again</a>
        """

    # -------------------------
    # Save Study Plan
    # -------------------------

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO study_plans
        (
            exam_date,
            days_remaining,
            study_hours,
            syllabus,
            created_at,
            user_id
        )

        VALUES (?, ?, ?, ?, ?, ?)
        """,

        (
            str(exam_day),
            days_remaining,
            study_hours,
            syllabus,
            str(datetime.now()),
            session["user_id"]
        )
    )

    plan_id = cursor.lastrowid

    # -------------------------
    # Save Progress Records
    # -------------------------

    for day in ai_plan["days"]:

        cursor.execute(
            """
            INSERT INTO progress
            (
                plan_id,
                day_number,
                completed
            )

            VALUES (?, ?, ?)
            """,

            (
                plan_id,
                day["day"],
                0
            )
        )

    conn.commit()

    # -------------------------
    # Get Completed Days
    # -------------------------

    cursor.execute(
        """
        SELECT day_number
        FROM progress

        WHERE plan_id = ?
        AND completed = 1
        """,

        (plan_id,)
    )

    completed_days = [
        row["day_number"]
        for row in cursor.fetchall()
    ]

    conn.close()

    # -------------------------
    # Get Unique Topics
    # -------------------------

    topics = []

    for day in ai_plan["days"]:

        topic = day["topic"]

        if topic not in topics:
            topics.append(topic)

    # -------------------------
    # Final Plan Object
    # -------------------------

    plan = {

        "id": plan_id,

        "exam_date": str(exam_day),

        "days_remaining": days_remaining,

        "study_hours": study_hours,

        "days": ai_plan["days"],

        "revision_strategy":
            ai_plan["revision_strategy"],

        "completed_days":
            completed_days,

        "topics":
            topics
    }

    return render_template(
        "plan.html",
        plan=plan
    )


# =========================
# TOGGLE PROGRESS
# =========================

@app.route(
    "/toggle-progress",
    methods=["POST"]
)
def toggle_progress():

    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required."}), 401

    data = request.get_json()

    plan_id = data.get("plan_id")
    day_number = data.get("day_number")
    completed = data.get("completed")

    if (
        plan_id is None
        or day_number is None
        or completed is None
    ):

        return jsonify({

            "success": False,

            "message":
            "Invalid request data."

        }), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT progress.id
        FROM progress
        JOIN study_plans ON study_plans.id = progress.plan_id
        WHERE progress.plan_id = ?
        AND progress.day_number = ?
        AND study_plans.user_id = ?
        """,

        (
            plan_id,
            day_number,
            session["user_id"]
        )
    )

    record = cursor.fetchone()

    if record is None:

        conn.close()

        return jsonify({

            "success": False,

            "message":
            "Progress record not found."

        }), 404

    cursor.execute(
        """
        UPDATE progress

        SET completed = ?

        WHERE plan_id = ?
        AND day_number = ?
        AND EXISTS (
            SELECT 1 FROM study_plans
            WHERE study_plans.id = progress.plan_id
            AND study_plans.user_id = ?
        )
        """,

        (
            1 if completed else 0,
            plan_id,
            day_number,
            session["user_id"]
        )
    )

    conn.commit()

    # -------------------------
    # Completed Count
    # -------------------------

    cursor.execute(
        """
        SELECT COUNT(*) AS completed_count

        FROM progress

        WHERE plan_id = ?
        AND completed = 1
        """,

        (plan_id,)
    )

    completed_count = cursor.fetchone()[
        "completed_count"
    ]

    # -------------------------
    # Total Count
    # -------------------------

    cursor.execute(
        """
        SELECT COUNT(*) AS total_count

        FROM progress

        WHERE plan_id = ?
        """,

        (plan_id,)
    )

    total_count = cursor.fetchone()[
        "total_count"
    ]

    conn.close()

    # -------------------------
    # Percentage
    # -------------------------

    if total_count == 0:

        percentage = 0

    else:

        percentage = (
            completed_count
            / total_count
        ) * 100

    return jsonify({

        "success": True,

        "completed_count":
            completed_count,

        "total_count":
            total_count,

        "percentage":
            percentage
    })


# ============================================================
# GENERATE QUIZ
# ============================================================

@app.route(
    "/generate-quiz",
    methods=["POST"]
)
def generate_quiz():

    auth_redirect = login_required()
    if auth_redirect:
        return auth_redirect

    plan_id = request.form.get("plan_id")
    topic = request.form.get("topic")

    if not plan_id or not topic:

        return """
        <h1>Quiz Error ❌</h1>
        <p>Plan or topic was not selected.</p>
        <a href="/">Go Back</a>
        """

    # -------------------------
    # Get Study Plan
    # -------------------------

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *

        FROM study_plans

        WHERE id = ?
        AND user_id = ?
        """,

        (plan_id, session["user_id"])
    )

    plan = cursor.fetchone()

    if plan is None:

        conn.close()

        return """
        <h1>Plan Not Found ❌</h1>
        <p>The study plan does not exist.</p>
        <a href="/">Go Back</a>
        """

    syllabus = plan["syllabus"]

    # -------------------------
    # Quiz Prompt
    # -------------------------

    quiz_prompt = f"""
You are StudyPilot, an AI quiz generator.

Create a quiz for a college student based ONLY on the provided syllabus.

SYLLABUS:

{syllabus}

SELECTED TOPIC:

{topic}

TASK:

Create exactly 5 multiple-choice questions about the selected topic.

Rules:

- Each question must have exactly 4 options.
- Only one option must be correct.
- correct_answer must be the zero-based index of the correct option.
- correct_answer must be 0, 1, 2, or 3.
- Questions must be based only on the selected topic.
- Do not introduce unrelated topics.
- Mix conceptual and application-based questions.
- Avoid duplicate questions.
- Provide a short explanation for every correct answer.
- Return ONLY the requested JSON.
"""

    # -------------------------
    # Quiz Schema
    # -------------------------

    quiz_schema = {

        "type": "object",

        "properties": {

            "questions": {

                "type": "array",

                "items": {

                    "type": "object",

                    "properties": {

                        "question": {
                            "type": "string"
                        },

                        "options": {

                            "type": "array",

                            "items": {
                                "type": "string"
                            }
                        },

                        "correct_answer": {
                            "type": "integer"
                        },

                        "explanation": {
                            "type": "string"
                        }

                    },

                    "required": [
                        "question",
                        "options",
                        "correct_answer",
                        "explanation"
                    ],

                    "additionalProperties": False
                }
            }

        },

        "required": [
            "questions"
        ],

        "additionalProperties": False
    }

    # -------------------------
    # Groq Call
    # -------------------------

    response = client.chat.completions.create(

        model="openai/gpt-oss-20b",

        messages=[

            {
                "role": "system",

                "content":
                "You are an accurate AI quiz generator. "
                "Follow the requested JSON schema exactly."
            },

            {
                "role": "user",

                "content": quiz_prompt
            }

        ],

        max_completion_tokens=4096,

        reasoning_effort="low",

        response_format={

            "type": "json_schema",

            "json_schema": {

                "name": "study_quiz",

                "strict": True,

                "schema": quiz_schema
            }
        }
    )

    ai_result = response.choices[0].message.content

    if not ai_result:

        conn.close()

        return """
        <h1>AI Quiz Error ❌</h1>
        <p>No quiz was generated.</p>
        <a href="/">Try Again</a>
        """

    # -------------------------
    # Parse Quiz
    # -------------------------

    try:

        quiz_data = json.loads(ai_result)

    except json.JSONDecodeError:

        conn.close()

        return """
        <h1>Quiz Response Error ❌</h1>
        <p>The AI returned invalid quiz data.</p>
        <a href="/">Try Again</a>
        """

    questions = quiz_data.get(
        "questions",
        []
    )

    # -------------------------
    # Validate Quiz
    # -------------------------

    if len(questions) != 5:

        conn.close()

        return """
        <h1>Quiz Error ❌</h1>
        <p>The AI did not generate exactly 5 questions.</p>
        <a href="/">Try Again</a>
        """

    for question in questions:

        if len(question["options"]) != 4:

            conn.close()

            return """
            <h1>Quiz Error ❌</h1>
            <p>Every question must have 4 options.</p>
            <a href="/">Try Again</a>
            """

        if question["correct_answer"] not in [0, 1, 2, 3]:

            conn.close()

            return """
            <h1>Quiz Error ❌</h1>
            <p>Invalid correct answer received.</p>
            <a href="/">Try Again</a>
            """

    # -------------------------
    # Save Quiz
    # -------------------------

    cursor.execute(
        """
        INSERT INTO quizzes
        (
            plan_id,
            topic,
            questions_json,
            created_at
        )

        VALUES (?, ?, ?, ?)
        """,

        (
            plan_id,
            topic,
            json.dumps(questions),
            str(datetime.now())
        )
    )

    quiz_id = cursor.lastrowid

    conn.commit()
    conn.close()

    quiz = {

        "id": quiz_id,

        "plan_id": plan_id,

        "topic": topic,

        "questions": questions
    }

    return render_template(
        "quiz.html",
        quiz=quiz
    )


# ============================================================
# SUBMIT QUIZ
# ============================================================

@app.route(
    "/submit-quiz",
    methods=["POST"]
)
def submit_quiz():

    auth_redirect = login_required()
    if auth_redirect:
        return auth_redirect

    quiz_id = request.form.get(
        "quiz_id"
    )

    if not quiz_id:

        return """
        <h1>Quiz Error ❌</h1>
        <p>Quiz ID is missing.</p>
        <a href="/">Go Back</a>
        """

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT quizzes.*, study_plans.user_id
        FROM quizzes
        JOIN study_plans ON study_plans.id = quizzes.plan_id
        WHERE quizzes.id = ?
        AND study_plans.user_id = ?
        """,

        (quiz_id, session["user_id"])
    )

    quiz_row = cursor.fetchone()

    if quiz_row is None:

        conn.close()

        return """
        <h1>Quiz Not Found ❌</h1>
        <p>This quiz does not exist.</p>
        <a href="/">Go Back</a>
        """

    questions = json.loads(
        quiz_row["questions_json"]
    )

    score = 0
    results = []

    # -------------------------
    # Check Answers
    # -------------------------

    for index, question in enumerate(questions):

        selected_answer = request.form.get(
            f"question_{index}"
        )

        if selected_answer is None:

            selected_index = -1

        else:

            selected_index = int(
                selected_answer
            )

        correct_index = question[
            "correct_answer"
        ]

        is_correct = (
            selected_index == correct_index
        )

        if is_correct:
            score += 1

        if selected_index >= 0:

            selected_text = question[
                "options"
            ][selected_index]

        else:

            selected_text = "Not answered"

        correct_text = question[
            "options"
        ][correct_index]

        results.append({

            "question":
                question["question"],

            "selected":
                selected_text,

            "correct":
                correct_text,

            "is_correct":
                is_correct,

            "explanation":
                question["explanation"]

        })

    total_questions = len(
        questions
    )

    percentage = (
        score / total_questions
    ) * 100

    # -------------------------
    # Save Result
    # -------------------------

    cursor.execute(
        """
        INSERT INTO quiz_results
        (
            quiz_id,
            plan_id,
            topic,
            score,
            total_questions,
            created_at
        )

        VALUES (?, ?, ?, ?, ?, ?)
        """,

        (
            quiz_row["id"],
            quiz_row["plan_id"],
            quiz_row["topic"],
            score,
            total_questions,
            str(datetime.now())
        )
    )

    conn.commit()
    conn.close()

    return render_template(

        "quiz_result.html",

        topic=quiz_row["topic"],

        score=score,

        total_questions=
            total_questions,

        percentage=
            percentage,

        results=results
    )


# =========================
# START SERVER
# =========================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True
    )