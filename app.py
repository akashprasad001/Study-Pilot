from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from dotenv import load_dotenv
from groq import Groq
from datetime import date, datetime, timedelta
import os
import json
import re
import sqlite3
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
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
- Return ONLY valid JSON.
- The top-level JSON object MUST contain exactly these keys: days and revision_strategy.
- days MUST be an array. Each day MUST contain exactly these keys: day, date, topic, study_time, practice, revision.
- Use strings for date, topic, study_time, practice and revision.
- Use an integer for day.

OUTPUT REQUIREMENTS:
- Return ONLY one valid JSON object.
- Do not include Markdown, code fences, or any text outside the JSON.
- The top-level object must contain: days and revision_strategy.
- Each item in days must contain: day, date, topic, study_time, practice, revision.
- day must be an integer. All other fields must be strings.

Return ONLY the JSON object.
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

        # Use JSON Object Mode here. The prompt requires the exact JSON
        # structure, and the response is parsed and validated below.
        response_format={
            "type": "json_object"
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
        <p>The AI returned invalid JSON.</p>
        <a href="/">Try Again</a>
        """

    # Some JSON-object responses may wrap the actual plan inside a
    # "study_plan" or "plan" object. Unwrap it safely.
    if isinstance(ai_plan, dict) and isinstance(ai_plan.get("study_plan"), dict):
        ai_plan = ai_plan["study_plan"]
    elif isinstance(ai_plan, dict) and isinstance(ai_plan.get("plan"), dict):
        ai_plan = ai_plan["plan"]

    days = ai_plan.get("days") if isinstance(ai_plan, dict) else None
    if days is None and isinstance(ai_plan, dict):
        days = ai_plan.get("study_days") or ai_plan.get("schedule")

    if not isinstance(days, list) or not days:
        return """
        <h1>AI Response Error ❌</h1>
        <p>The AI did not return any study days.</p>
        <a href="/">Try Again</a>
        """

    # Normalize each day so small variations in the AI response do not break
    # the application. The actual dates/day numbers always come from our
    # trusted server-side date list.
    normalized_days = []

    for index, day in enumerate(days):

        if not isinstance(day, dict):
            continue

        normalized_days.append({
            "day": index + 1,
            "date": study_dates[index] if index < len(study_dates) else str(exam_day),
            "topic": str(day.get("topic") or day.get("main_topic") or day.get("subject") or "Revision"),
            "study_time": str(day.get("study_time") or day.get("time") or f"{study_hours} hours"),
            "practice": str(day.get("practice") or day.get("practice_activity") or "Practice questions from this topic."),
            "revision": str(day.get("revision") or day.get("revision_activity") or "Review the key concepts."),
        })

    # Keep exactly the available study days.
    normalized_days = normalized_days[:len(study_dates)]

    if not normalized_days:
        return """
        <h1>AI Response Error ❌</h1>
        <p>The AI returned an empty study plan.</p>
        <a href="/">Try Again</a>
        """

    revision_strategy = ""
    if isinstance(ai_plan, dict):
        revision_strategy = ai_plan.get("revision_strategy") or ai_plan.get("revision") or "Use the final available days for revision and practice."

    ai_plan = {
        "days": normalized_days,
        "revision_strategy": str(revision_strategy)
    }

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

        # Use JSON Object Mode here. The quiz response is validated below.
        response_format={
            "type": "json_object"
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
        <p>The AI returned invalid quiz JSON.</p>
        <a href="/">Try Again</a>
        """

    if not isinstance(quiz_data, dict):
        conn.close()
        return """
        <h1>Quiz Response Error ❌</h1>
        <p>The AI returned an invalid quiz structure.</p>
        <a href="/">Try Again</a>
        """

    if isinstance(quiz_data.get("quiz"), dict):
        quiz_data = quiz_data["quiz"]

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
# QUIZ HISTORY
# =========================

@app.route("/quiz-history")
def quiz_history():

    auth_redirect = login_required()
    if auth_redirect:
        return auth_redirect

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            quiz_results.id,
            quiz_results.topic,
            quiz_results.score,
            quiz_results.total_questions,
            quiz_results.created_at
        FROM quiz_results
        JOIN study_plans
            ON study_plans.id = quiz_results.plan_id
        WHERE study_plans.user_id = ?
        ORDER BY quiz_results.created_at DESC
    """, (session["user_id"],))

    history = cursor.fetchall()

    conn.close()

    return render_template(
        "quiz_history.html",
        history=history
    )



# =========================
# WEAK TOPIC DETECTION
# =========================

@app.route("/weak-topics")
def weak_topics():

    auth_redirect = login_required()
    if auth_redirect:
        return auth_redirect

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            quiz_results.topic,
            COUNT(quiz_results.id) AS attempts,
            SUM(quiz_results.score) AS total_score,
            SUM(quiz_results.total_questions) AS total_questions,
            ROUND(
                (CAST(SUM(quiz_results.score) AS REAL)
                / NULLIF(SUM(quiz_results.total_questions), 0)) * 100,
                1
            ) AS percentage
        FROM quiz_results
        JOIN study_plans
            ON study_plans.id = quiz_results.plan_id
        WHERE study_plans.user_id = ?
        GROUP BY quiz_results.topic
        ORDER BY percentage ASC, attempts DESC
    """, (session["user_id"],))

    topic_rows = cursor.fetchall()
    conn.close()

    weak = [row for row in topic_rows if float(row[4] or 0) < 60]

    return render_template(
        "weak_topics.html",
        topics=topic_rows,
        weak_topics=weak
    )



# =========================
# ADAPTIVE STUDY PLAN
# =========================

@app.route("/adaptive-plan")
def adaptive_plan():

    auth_redirect = login_required()
    if auth_redirect:
        return auth_redirect

    conn = get_db()
    cursor = conn.cursor()

    # Get the student's latest study plan.
    cursor.execute("""
        SELECT id, exam_date, study_hours, syllabus
        FROM study_plans
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (session["user_id"],))

    latest_plan = cursor.fetchone()

    if not latest_plan:
        conn.close()
        return """
        <h1>No Study Plan Found ❌</h1>
        <p>Create a study plan first.</p>
        <a href="/">Go to StudyPilot</a>
        """

    # Calculate topic performance from this user's quiz history.
    cursor.execute("""
        SELECT
            quiz_results.topic,
            COUNT(quiz_results.id) AS attempts,
            SUM(quiz_results.score) AS total_score,
            SUM(quiz_results.total_questions) AS total_questions,
            ROUND(
                (CAST(SUM(quiz_results.score) AS REAL)
                / NULLIF(SUM(quiz_results.total_questions), 0)) * 100,
                1
            ) AS percentage
        FROM quiz_results
        JOIN study_plans
            ON study_plans.id = quiz_results.plan_id
        WHERE study_plans.user_id = ?
        GROUP BY quiz_results.topic
        ORDER BY percentage ASC, attempts DESC
    """, (session["user_id"],))

    topic_rows = cursor.fetchall()
    weak_rows = [row for row in topic_rows if float(row[4] or 0) < 60]

    if not weak_rows:
        conn.close()
        return render_template(
            "adaptive_plan.html",
            adaptive_days=[],
            weak_topics=[],
            message="No weak topics detected yet. Complete a quiz below 60% to trigger an adaptive plan.",
            plan=latest_plan
        )

    # Use the remaining days up to the exam date, capped at 7 for a focused plan.
    today = date.today()
    exam_day = datetime.strptime(latest_plan["exam_date"], "%Y-%m-%d").date()
    remaining_days = max(1, (exam_day - today).days + 1)
    plan_days = min(remaining_days, 7)

    weak_topic_text = "\n".join(
        f"- {row[0]}: {row[4]}% average, {row[1]} attempt(s), {row[2]}/{row[3]} correct"
        for row in weak_rows
    )

    adaptive_dates = "\n".join(
        f"Day {i + 1}: {today + timedelta(days=i)}"
        for i in range(plan_days)
    )

    prompt = f"""
You are StudyPilot, an adaptive learning assistant.

Create a focused adaptive study plan using the student's actual quiz performance.

WEAK TOPICS:
{weak_topic_text}

LATEST SYLLABUS:
{latest_plan['syllabus']}

DAILY STUDY TIME:
{latest_plan['study_hours']} hours

AVAILABLE ADAPTIVE DATES:
{adaptive_dates}

RULES:
- Prioritize the weakest topics first.
- Give extra practice and revision to weak topics.
- Do not add topics that are not in the syllabus or weak-topic list.
- Keep each day within the student's daily study time.
- Include practice questions and revision.
- Return ONLY valid JSON.
- The top-level object must contain exactly one key: adaptive_days.
- adaptive_days must be an array.
- Every item must contain exactly these keys: day, date, focus, study_time, practice, revision.
- All values must be strings except day, which must be an integer.
"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": "You return only valid JSON objects with no markdown."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            max_completion_tokens=4096,
            reasoning_effort="low",
            response_format={"type": "json_object"}
        )

        ai_result = response.choices[0].message.content
        adaptive_data = json.loads(ai_result)
        adaptive_days = adaptive_data.get("adaptive_days", [])

    except Exception:
        conn.close()
        return """
        <h1>Adaptive Plan Error ❌</h1>
        <p>StudyPilot could not generate the adaptive plan right now.</p>
        <a href="/weak-topics">Back to Weak Topics</a>
        """

    conn.close()

    return render_template(
        "adaptive_plan.html",
        adaptive_days=adaptive_days,
        weak_topics=weak_rows,
        message=None,
        plan=latest_plan
    )

# =========================
# AI HANDWRITTEN NOTES + PDF
# =========================

def _build_handwritten_pdf(notes_data):
    buffer = BytesIO()

    # Use an installed cursive-style font when available. Fall back safely.
    font_name = "Helvetica-Oblique"
    try:
        cursive_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        ]
        for font_path in cursive_candidates:
            if os.path.exists(font_path):
                pdfmetrics.registerFont(TTFont("StudyPilotHand", font_path))
                font_name = "StudyPilotHand"
                break
    except Exception:
        pass

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=45,
        leftMargin=55,
        topMargin=50,
        bottomMargin=45,
        title=str(notes_data.get("title", "StudyPilot Notes")),
        author="StudyPilot"
    )

    title_style = ParagraphStyle(
        "HandTitle", fontName=font_name, fontSize=22, leading=28,
        spaceAfter=14, textColor=colors.HexColor("#1d4ed8")
    )
    section_style = ParagraphStyle(
        "HandSection", fontName=font_name, fontSize=14, leading=19,
        spaceBefore=8, spaceAfter=7, textColor=colors.HexColor("#1e40af")
    )
    body_style = ParagraphStyle(
        "HandBody", fontName=font_name, fontSize=11.5, leading=19,
        spaceAfter=6, textColor=colors.HexColor("#1f3b64")
    )
    small_style = ParagraphStyle(
        "HandSmall", fontName=font_name, fontSize=9, leading=14,
        textColor=colors.HexColor("#64748b")
    )

    story = []
    story.append(Paragraph(escape(str(notes_data.get("title", "StudyPilot Notes"))), title_style))
    story.append(Paragraph("AI-generated revision notes • StudyPilot", small_style))
    story.append(Spacer(1, 8))

    overview = str(notes_data.get("overview", ""))
    if overview:
        story.append(Paragraph("Overview", section_style))
        story.append(Paragraph(escape(overview), body_style))

    sections = [
        ("✦ Key Points", notes_data.get("key_points", [])),
        ("✎ Examples", notes_data.get("examples", [])),
        ("⚠ Common Mistakes", notes_data.get("common_mistakes", [])),
        ("✓ Quick Revision", notes_data.get("quick_revision", [])),
    ]

    for heading, items in sections:
        if not isinstance(items, list) or not items:
            continue
        story.append(Paragraph(heading, section_style))
        for item in items:
            story.append(Paragraph("• " + escape(str(item)), body_style))

    def notebook_background(canvas, doc):
        canvas.saveState()
        width, height = A4
        # Notebook-style horizontal lines
        canvas.setStrokeColor(colors.HexColor("#dbeafe"))
        canvas.setLineWidth(0.35)
        y = 42
        while y < height - 35:
            canvas.line(35, y, width - 35, y)
            y += 20
        # Red margin line
        canvas.setStrokeColor(colors.HexColor("#fecaca"))
        canvas.setLineWidth(0.7)
        canvas.line(48, 35, 48, height - 35)
        canvas.restoreState()

    doc.build(story, onFirstPage=notebook_background, onLaterPages=notebook_background)
    buffer.seek(0)
    return buffer


@app.route("/notes", methods=["GET", "POST"])
def notes():

    auth_redirect = login_required()
    if auth_redirect:
        return auth_redirect

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, syllabus
        FROM study_plans
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (session["user_id"],))

    latest_plan = cursor.fetchone()
    conn.close()

    if not latest_plan:
        return """
        <h1>No Study Plan Found ❌</h1>
        <p>Create a study plan first.</p>
        <a href="/">Go to StudyPilot</a>
        """

    if request.method == "GET":
        return render_template("notes.html", syllabus=latest_plan["syllabus"])

    topic = request.form.get("topic", "").strip()

    if not topic:
        return render_template(
            "notes.html",
            syllabus=latest_plan["syllabus"],
            error="Please enter a topic."
        )

    prompt = f"""
You are StudyPilot, an AI study-notes assistant.

Create concise, exam-focused revision notes for this topic:

TOPIC:
{topic}

STUDENT SYLLABUS:
{latest_plan["syllabus"]}

Only generate notes related to the requested topic.
Keep the notes suitable for a college student preparing for an exam.
Include definitions, formulas, algorithms, steps, examples, and important points when relevant.
Return ONLY one valid JSON object.

The JSON should normally contain these fields:
- title: string
- overview: string
- key_points: array of strings
- examples: array of strings
- common_mistakes: array of strings
- quick_revision: array of strings
"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": "Return one valid JSON object containing the study notes."
                },
                {"role": "user", "content": prompt}
            ],
            max_completion_tokens=4096,
            reasoning_effort="low",
            response_format={"type": "json_object"}
        )

        ai_result = response.choices[0].message.content

        if not ai_result:
            raise ValueError("AI returned an empty response.")

        notes_data = json.loads(ai_result)

        if not isinstance(notes_data, dict):
            raise ValueError("AI returned an invalid JSON object.")

        # Some model responses may wrap the notes inside another object.
        # Accept common wrapper names instead of failing.
        for wrapper in ("notes", "study_notes", "data", "result"):
            if isinstance(notes_data.get(wrapper), dict):
                notes_data = notes_data[wrapper]
                break

        # Never fail the PDF just because the model omitted one optional field.
        # Give every field a safe fallback so the PDF builder always has a
        # predictable structure.
        title = notes_data.get("title") or topic
        overview = notes_data.get("overview") or (
            f"Exam-focused revision notes for {topic}."
        )

        def normalize_list(value, fallback=None):
            if isinstance(value, list):
                return [str(item) for item in value if str(item).strip()]
            if isinstance(value, str) and value.strip():
                return [value.strip()]
            return fallback or []

        key_points = normalize_list(notes_data.get("key_points"))
        examples = normalize_list(notes_data.get("examples"))
        common_mistakes = normalize_list(notes_data.get("common_mistakes"))
        quick_revision = normalize_list(
            notes_data.get("quick_revision"),
            key_points[:6]
        )

        notes_data = {
            "title": str(title),
            "overview": str(overview),
            "key_points": key_points,
            "examples": examples,
            "common_mistakes": common_mistakes,
            "quick_revision": quick_revision
        }

        pdf_buffer = _build_handwritten_pdf(notes_data)

        filename = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            topic
        ).strip("_") or "study_notes"

        filename = filename[:60] + "_handwritten_notes.pdf"

        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        print("\n===================================")
        print("NOTES GENERATION ERROR")
        print(str(e))
        print("===================================\n")

        return render_template(
            "notes.html",
            syllabus=latest_plan["syllabus"],
            error=f"Notes generation failed: {str(e)}"
        )


# =========================
# START SERVER
# =========================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True
    )